"""第十七轮后端与安全核心缺陷回归测试集 (Round 17 Defects)

覆盖问题：
- 问题二：DNS 解析有界超时、并发控制与 fail-closed
- 问题四（后端部分）：CatalogManager.replace_invalid_app_target 接口与快照回滚
- 问题三（后端部分）：CatalogManager.batch_set_enabled 异常时原子回滚
"""

import time
import socket
import threading
from unittest.mock import patch, MagicMock
import pytest

from src.core.security import UrlValidator
from src.catalog.models import AppEntry, LaunchType
from src.catalog.manager import CatalogManager


# =================================================================================================
# 1. DNS 解析有界超时、并发限制与 fail-closed 测试 (Item 2)
# =================================================================================================

def test_dns_resolve_timeout_constant_defined():
    """UrlValidator 必须定义显式的 DNS_RESOLVE_TIMEOUT_SECONDS 常量且默认为 2.0 秒。"""
    assert hasattr(UrlValidator, "DNS_RESOLVE_TIMEOUT_SECONDS")
    assert UrlValidator.DNS_RESOLVE_TIMEOUT_SECONDS == 2.0


def test_dns_resolve_times_out_and_fails_closed():
    """慢 DNS 查询在指定超时内终止并抛出安全拒绝异常 (fail-closed)，绝不能永久挂起或放行。"""
    unblock_worker = threading.Event()

    def slow_getaddrinfo(*args, **kwargs):
        unblock_worker.wait(timeout=1.0)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.216.34', 443))]

    try:
        with patch.object(UrlValidator, "DNS_RESOLVE_TIMEOUT_SECONDS", 0.05):
            with patch("socket.getaddrinfo", side_effect=slow_getaddrinfo):
                start = time.perf_counter()
                with pytest.raises(ValueError) as exc:
                    UrlValidator.validate_url("https://slow.example.com/test")
                elapsed = time.perf_counter() - start

                # 验证执行时间在 50ms 左右，远小于 slow_getaddrinfo 的 1.0s
                assert elapsed < 0.25, f"DNS 查询耗时 {elapsed}s，未能及时超时退出"
                err_msg = str(exc.value)
                assert "安全拒绝" in err_msg
                assert "超时" in err_msg or "解析失败" in err_msg
    finally:
        unblock_worker.set()
        time.sleep(0.05)


def test_dns_concurrency_bounded_semaphore_rejects_when_full():
    """当并发 DNS 解析请求占满上限时，必须快速拒绝后续请求，防止拒绝服务攻击与无界线程。"""
    block_event = threading.Event()

    def blocking_getaddrinfo(*args, **kwargs):
        block_event.wait(timeout=1.0)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.216.34', 443))]

    max_concurrent = getattr(UrlValidator, "MAX_CONCURRENT_DNS_RESOLUTIONS", 4)

    # 占满所有信号量
    acquired = []
    for _ in range(max_concurrent):
        acquired.append(UrlValidator._dns_semaphore.acquire(blocking=False))
    assert all(acquired), "必须能初始获取所有信号量"

    try:
        # 当信号量耗尽时，validate_url 必须快速抛出安全拒绝，不能阻塞
        start = time.perf_counter()
        with pytest.raises(ValueError) as exc:
            UrlValidator.validate_url("https://concurrency.example.com")
        elapsed = time.perf_counter() - start

        assert elapsed < 0.1, f"并发占满时未能快速拒绝，耗时 {elapsed}s"
        assert "安全拒绝" in str(exc.value)
        assert "并发" in str(exc.value) or "上限" in str(exc.value) or "解析" in str(exc.value)
    finally:
        # 释放占用的信号量
        for _ in range(max_concurrent):
            UrlValidator._dns_semaphore.release()


def test_dns_clean_public_ip_passes():
    """纯公网干净 IP 在正常解析下应无障碍通过。"""
    clean_dns = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('180.101.50.242', 443))]
    with patch("socket.getaddrinfo", return_value=clean_dns):
        url = "https://public.example.com/api/test?query=safe"
        assert UrlValidator.validate_url(url) == url


def test_dns_rejects_mixed_private_ip():
    """多 IP 解析中只要包含一个私网或保留地址，必须严格拒绝。"""
    mixed_dns = [
        (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('180.101.50.242', 443)),
        (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('192.168.1.1', 443)),
    ]
    with patch("socket.getaddrinfo", return_value=mixed_dns):
        with pytest.raises(ValueError) as exc:
            UrlValidator.validate_url("https://mixed.example.com")
        assert "安全拒绝" in str(exc.value)


# =================================================================================================
# 2. CatalogManager 批量操作原子性与回滚测试 (Item 3 后端)
# =================================================================================================

def test_catalog_batch_set_enabled_atomic_rollback_on_failure(tmp_path):
    """CatalogManager.batch_set_enabled 在发生内部异常或保存异常时，必须原子回滚内存状态。"""
    cat_file = tmp_path / "catalog.json"
    cat_file.write_text("[]", encoding="utf-8")
    mgr = CatalogManager(catalog_file_path=str(cat_file))

    app1 = AppEntry(
        app_id="app-1",
        display_name="应用1",
        exe_path=r"C:\fake\app1.exe",
        launch_target=r"C:\fake\app1.exe",
        launch_type=LaunchType.WIN32,
        source="AppsFolder",
        is_enabled=False,
        is_valid=True,
    )
    app2 = AppEntry(
        app_id="app-2",
        display_name="应用2",
        exe_path=r"C:\fake\app2.exe",
        launch_target=r"C:\fake\app2.exe",
        launch_type=LaunchType.WIN32,
        source="AppsFolder",
        is_enabled=False,
        is_valid=True,
    )
    mgr.apps["app-1"] = app1
    mgr.apps["app-2"] = app2

    # 模拟在校验过程中产生异常
    with patch.object(mgr, "_validate_launchable", side_effect=RuntimeError("Disk IO failure")):
        with pytest.raises(RuntimeError):
            mgr.batch_set_enabled(True, app_ids=["app-1", "app-2"])

    # 验证内存状态未被部分污染
    assert mgr.apps["app-1"].is_enabled is False
    assert mgr.apps["app-2"].is_enabled is False


# =================================================================================================
# 3. CatalogManager 自动条目修复接口 replace_invalid_app_target 测试 (Item 4 后端)
# =================================================================================================

def test_replace_invalid_app_target_success(tmp_path):
    """自动条目失效后，通过 replace_invalid_app_target 选择有效新目标，成功转为手动管理安全条目并保持未授权。"""
    cat_file = tmp_path / "catalog.json"
    cat_file.write_text("[]", encoding="utf-8")
    mgr = CatalogManager(catalog_file_path=str(cat_file))

    # 创建一个失效的自动扫描条目（例如带参数快捷方式被拦截）
    invalid_auto_app = AppEntry(
        app_id="auto-lenovo",
        display_name="联想电脑管家",
        exe_path=r"C:\fake\old_shortcut.lnk",
        launch_target=r"C:\fake\old_shortcut.lnk",
        launch_type=LaunchType.SHORTCUT,
        source="StartMenu",
        is_enabled=False,
        is_valid=False,
        is_user_managed=False,
        authorization_block_reason="快捷方式包含启动参数，为避免参数注入，禁止授权和启动",
        validation_status="invalid",
    )
    mgr.apps[invalid_auto_app.app_id] = invalid_auto_app

    # 用户选择的真实合法可执行程序
    valid_exe = tmp_path / "LenovoManager.exe"
    valid_exe.write_text("dummy")

    repaired = mgr.replace_invalid_app_target(invalid_auto_app.app_id, str(valid_exe))

    assert repaired.is_valid is True
    assert repaired.is_enabled is False  # 修复后必须保持未授权，等待用户主动允许
    assert repaired.is_user_managed is True  # 转换为手动管理
    assert repaired.validation_status == "valid"
    assert repaired.authorization_block_reason == ""
    assert repaired.display_name == "联想电脑管家"  # 保留原有显示名称


def test_replace_invalid_app_target_rejects_dangerous_target(tmp_path):
    """replace_invalid_app_target 绝不允许选择 cmd.exe、control.exe 等危险系统程序。"""
    cat_file = tmp_path / "catalog.json"
    cat_file.write_text("[]", encoding="utf-8")
    mgr = CatalogManager(catalog_file_path=str(cat_file))

    invalid_app = AppEntry(
        app_id="auto-tool",
        display_name="失效工具",
        exe_path=r"C:\fake\missing.exe",
        launch_target=r"C:\fake\missing.exe",
        launch_type=LaunchType.WIN32,
        source="StartMenu",
        is_enabled=False,
        is_valid=False,
        is_user_managed=False,
        validation_status="invalid",
    )
    mgr.apps[invalid_app.app_id] = invalid_app

    for dang in ["cmd.exe", "powershell.exe", "control.exe", "Taskmgr.exe"]:
        with pytest.raises(PermissionError) as exc:
            mgr.replace_invalid_app_target(invalid_app.app_id, dang)
        assert "安全拒绝" in str(exc.value) or "高危" in str(exc.value) or "禁止" in str(exc.value)
        # 确保原条目仍保持 invalid 且未授权
        assert mgr.apps[invalid_app.app_id].is_valid is False


def test_replace_invalid_app_target_rejects_already_dangerous_entry(tmp_path):
    """如果条目本身是 dangerous 或 restricted（如 cmd 条目），禁止被修复。"""
    cat_file = tmp_path / "catalog.json"
    cat_file.write_text("[]", encoding="utf-8")
    mgr = CatalogManager(catalog_file_path=str(cat_file))

    dangerous_app = AppEntry(
        app_id="dang-cmd",
        display_name="命令提示符",
        exe_path=r"C:\Windows\System32\cmd.exe",
        launch_target=r"C:\Windows\System32\cmd.exe",
        launch_type=LaunchType.WIN32,
        source="StartMenu",
        is_enabled=False,
        is_valid=False,
        is_user_managed=False,
        is_restricted=True,
        validation_status="dangerous",
    )
    mgr.apps[dangerous_app.app_id] = dangerous_app

    valid_exe = tmp_path / "SomeSafe.exe"
    valid_exe.write_text("dummy")

    with pytest.raises(PermissionError) as exc:
        mgr.replace_invalid_app_target(dangerous_app.app_id, str(valid_exe))
    assert "禁止" in str(exc.value) or "受限" in str(exc.value) or "危险" in str(exc.value)
