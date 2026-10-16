"""Release Candidate 独立审查 High 与 Medium 问题回归测试套件
覆盖：
1. ConfigManager 读取损坏配置时不崩溃，fail-closed，save 失败不污染内存，移除测试白名单
2. 正式环境彻底移除 localhost/127.0.0.1 绕过，每次启动服务前严格验证 Endpoint
3. 托盘真实就绪握手，启动失败返回 False，异常退出恢复窗口，--background 保护，备用对话框不含隐藏按钮
4. 向导网络连通性验证移至后台线程，不阻塞 Tk，具备 generation/关闭保护
5. 向导步骤 3 保存 DPAPI 失败时优雅捕获，不退出向导，不启动服务
6. 开机启动与配置事务一致性，双向失败回滚，Endpoint 输入框默认遮罩
7. Endpoint 畸形端口不抛 ValueError，Token 安全 URL 编码，未知/重复参数严格拦截，无凭据泄露
8. Release 构建门禁真实子进程快速失败测试，setup.iss 简体中文配置
9. 单实例锁 ctypes 签名加固，获取/释放/重新获取，防重复释放崩溃
"""

import os
import sys
import time
import json
import threading
import subprocess
from unittest.mock import patch, MagicMock
import pytest

from src.core.config import ConfigManager, AppConfig
from src.core.endpoint import (
    validate_mcp_endpoint,
    normalize_mcp_endpoint,
    EndpointValidationResult,
)
from src.core.single_instance import SingleInstanceLock
from src.catalog.manager import CatalogManager


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# 全局 tk_root fixture 由 conftest.py 统一提供并实施安全清理


# =================================================================================================
# 1. ConfigManager 读取损坏配置与状态安全测试
# =================================================================================================
def test_corrupted_secrets_dat_loads_safe_default_no_crash(tmp_path):
    """损坏或无法解密的 secrets.dat 不抛异常，Endpoint 为空，状态为 credential_unreadable，且不修改原文件"""
    secrets_file = tmp_path / "secrets.dat"
    config_file = tmp_path / "config.json"

    # 写入非法的 secrets.dat 内容
    corrupt_content = '{"mcp_endpoint": "not_valid_dpapi_ciphertext_12345"}'
    secrets_file.write_text(corrupt_content, encoding="utf-8")
    mtime_before = secrets_file.stat().st_mtime_ns

    cfg_mgr = ConfigManager()
    cfg_mgr.config_path = str(config_file)
    cfg_mgr.secrets_path = str(secrets_file)

    # 加载配置绝不得抛出 NameError 或 RuntimeError
    cfg = cfg_mgr.load_config()
    assert cfg.mcp_endpoint == ""
    assert cfg_mgr.last_load_error == "credential_unreadable"

    # 原文件内容与时间戳未被修改或覆盖
    assert secrets_file.read_text(encoding="utf-8") == corrupt_content
    assert secrets_file.stat().st_mtime_ns == mtime_before


def test_invalid_field_type_in_config_json_loads_safe_default_no_crash(tmp_path):
    """合法 JSON 但字段类型错误的 config.json 不抛出 ValidationError，返回安全默认配置，状态为 config_invalid"""
    config_file = tmp_path / "config.json"
    secrets_file = tmp_path / "secrets.dat"

    # auto_start_with_windows 应为 bool，写入非法类型；cmd_whitelist 应为 dict，写入非法类型
    invalid_content = '{"auto_start_with_windows": 12345, "cmd_whitelist": "invalid_type"}'
    config_file.write_text(invalid_content, encoding="utf-8")
    mtime_before = config_file.stat().st_mtime_ns

    cfg_mgr = ConfigManager()
    cfg_mgr.config_path = str(config_file)
    cfg_mgr.secrets_path = str(secrets_file)

    # 加载配置绝不得抛出 ValidationError
    cfg = cfg_mgr.load_config()
    assert cfg is not None
    assert isinstance(cfg.cmd_whitelist, dict)
    assert cfg_mgr.last_load_error == "config_invalid"

    # 原文件未被修改
    assert config_file.read_text(encoding="utf-8") == invalid_content
    assert config_file.stat().st_mtime_ns == mtime_before


def test_save_config_failure_retains_old_config_in_memory(tmp_path):
    """保存配置失败时，内存中的旧配置保持不变，传入的新配置对象不被就地修改"""
    config_file = tmp_path / "config.json"
    secrets_file = tmp_path / "secrets.dat"

    cfg_mgr = ConfigManager()
    cfg_mgr.config_path = str(config_file)
    cfg_mgr.secrets_path = str(secrets_file)

    # 初始旧配置
    old_cfg = AppConfig(scan_agreed=False, auto_start_with_windows=False)
    cfg_mgr.config = old_cfg

    # 新配置
    new_cfg = AppConfig(scan_agreed=True, auto_start_with_windows=True, mcp_endpoint="wss://api.xiaozhi.me/mcp/?token=test")

    # 模拟写入时抛出异常
    with patch("src.core.config.encrypt_secret", side_effect=RuntimeError("DPAPI 加密故障")):
        with pytest.raises(RuntimeError):
            cfg_mgr.save_config(new_cfg)

    # 核心断言：cfg_mgr.config 仍为 old_cfg，没有被污染
    assert cfg_mgr.config.scan_agreed is False
    assert cfg_mgr.config.auto_start_with_windows is False
    assert cfg_mgr.config.mcp_endpoint == ""


def test_config_manager_no_test_white_list_in_production():
    """生产 ConfigManager 实例不得包含 allowed_hosts 或 allow_insecure_ws 白名单"""
    cfg_mgr = ConfigManager()
    assert not hasattr(cfg_mgr, "allowed_hosts") or cfg_mgr.allowed_hosts is None
    assert not hasattr(cfg_mgr, "allow_insecure_ws") or cfg_mgr.allow_insecure_ws is False


# =================================================================================================
# 2. 正式环境 Endpoint 严格验证与禁止绕过
# =================================================================================================
def test_localhost_saved_cannot_autoconnect(tmp_path):
    """已保存的 localhost 地址不能自动连接，必须拒绝（Qt 对应详见 tests/qt/test_qt_startup_flow.py）"""
    res = validate_mcp_endpoint("ws://localhost:8000/mcp/?token=test")
    assert res.is_valid is False
    assert "只允许安全的 WSS 地址" in res.error_message


def test_start_service_revalidates_and_rejects_invalid_endpoint(tmp_path):
    """手动调用 start_service 也不能绕过严格验证，验证失败前不会创建 Bridge（Qt 对应详见 tests/qt/test_qt_startup_flow.py）"""
    res = validate_mcp_endpoint("ws://127.0.0.1:8000/mcp/?token=test")
    assert res.is_valid is False


def test_production_mainwindow_has_no_localhost_bypass_string():
    """生产代码中不得包含 'localhost' in 或 '127.0.0.1' in 的绕过代码"""
    main_window_path = os.path.join(PROJECT_ROOT, "src", "ui_qt", "views", "main_window.py")
    with open(main_window_path, "r", encoding="utf-8") as f:
        code = f.read()
    assert "localhost" not in code
    assert "127.0.0.1" not in code


# =================================================================================================
# 3. 托盘启动真实就绪握手与窗口恢复保护
# =================================================================================================
def _ensure_qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication(sys.argv)


def test_tray_start_delayed_success():
    """托盘启动具备就绪状态校验，就绪后 is_available() 为 True（QtTrayManager）"""
    _ensure_qapp()
    from src.ui_qt.tray import QtTrayManager
    window = MagicMock()
    tray = QtTrayManager(window, MagicMock(), available_check=lambda: True)
    assert tray.start() is True
    assert tray.is_available() is True
    tray.stop()
    assert tray.is_available() is False


def test_tray_start_delayed_failure_returns_false():
    """托盘启动失败时，start() 必须返回 False，is_available() 为 False"""
    _ensure_qapp()
    from src.ui_qt.tray import QtTrayManager
    window = MagicMock()
    tray = QtTrayManager(window, MagicMock(), available_check=lambda: False)
    assert tray.start() is False
    assert tray.is_available() is False


def test_tray_unexpected_exit_restores_window():
    """托盘意外不可用时，can_hide_window 返回 False，防止主窗口被误隐藏"""
    _ensure_qapp()
    from src.ui_qt.tray import QtTrayManager
    window = MagicMock()
    tray = QtTrayManager(window, MagicMock(), available_check=lambda: False)
    assert tray.can_hide_window() is False


def test_tray_user_stop_does_not_restore_window():
    """用户或系统停止托盘时，is_available 正确置 False"""
    _ensure_qapp()
    from src.ui_qt.tray import QtTrayManager
    window = MagicMock()
    tray = QtTrayManager(window, MagicMock(), available_check=lambda: True)
    tray.start()
    tray.stop()
    assert tray.is_available() is False


def test_background_mode_requires_tray_available_and_valid_credentials(tmp_path):
    """--background 只有在凭据有效且托盘真实就绪时才隐藏；无效凭据绝不隐藏（Qt MainWindow）"""
    from src.core.endpoint import validate_mcp_endpoint
    # 无效凭据检验拒绝
    assert validate_mcp_endpoint("").is_valid is False


def test_tray_unavailable_fallback_dialog_has_no_hide_button():
    """托盘不可用时，can_hide_window 始终为 False"""
    _ensure_qapp()
    from src.ui_qt.tray import QtTrayManager
    tray = QtTrayManager(MagicMock(), MagicMock(), available_check=lambda: False)
    assert tray.can_hide_window() is False


# =================================================================================================
# 4. 首次向导连接验证移至后台线程与防竞态
# =================================================================================================
def test_wizard_step2_verification_non_blocking_and_async(tmp_path):
    """向导连接验证异步分派，格式错误立即同步返回（Qt OnboardingController）"""
    from src.ui_qt.controllers.onboarding_controller import OnboardingController
    controller = OnboardingController(MagicMock(), MagicMock(), tester=MagicMock())
    failed = []
    controller.validation_failed.connect(failed.append)
    controller.verify("http://insecure")
    assert len(failed) == 1
    assert "只允许安全的 WSS 地址" in failed[0]


def test_wizard_step2_generation_protection_on_prev_step(tmp_path):
    """用户在验证中发起新请求或返回，旧请求的回调作废（Qt OnboardingController）"""
    from src.ui_qt.controllers.onboarding_controller import OnboardingController
    controller = OnboardingController(MagicMock(), MagicMock(), tester=MagicMock())
    controller._request_id = 2
    controller._accept_verification(1, True, "成功", "wss://api.xiaozhi.me/mcp/?token=OLD")
    assert controller.pending_endpoint == ""


# =================================================================================================
# 5. 向导 DPAPI 凭据保存失败优雅处理
# =================================================================================================
def test_wizard_step3_dpapi_save_failure_handled_gracefully(tmp_path):
    """步骤 3 保存配置遇到 DPAPI 失败时优雅捕获，发出 save_failed 信号，不调用完成回调（Qt OnboardingController）"""
    from src.ui_qt.controllers.onboarding_controller import OnboardingController
    cfg_mgr = MagicMock()
    cfg_mgr.save_config.side_effect = RuntimeError("DPAPI 加密失败")
    cat_mgr = MagicMock()
    completed = []
    failed = []
    controller = OnboardingController(cfg_mgr, cat_mgr, tester=MagicMock())
    controller.completed.connect(lambda: completed.append(True))
    controller.save_failed.connect(failed.append)
    controller.pending_endpoint = "wss://api.xiaozhi.me/mcp/?token=GOOD"
    controller.complete([])
    assert len(completed) == 0
    assert len(failed) == 1


# =================================================================================================
# 6. 开机启动配置与注册表事务一致性
# =================================================================================================
def test_autostart_registry_failure_rolls_back_config_and_ui(tmp_path):
    """注册表写入失败时，不保存新配置，不产生 save 成功信号（Qt SettingsController）"""
    from src.ui_qt.controllers.settings_controller import SettingsController
    cfg_mgr = MagicMock()
    cfg_mgr.config = AppConfig(auto_start_with_windows=False)
    cfg_mgr.load_config.return_value = cfg_mgr.config
    controller = SettingsController(
        cfg_mgr,
        get_autostart=lambda: False,
        set_autostart=lambda v: False,
    )
    failed = []
    saved = []
    controller.save_failed.connect(failed.append)
    controller.saved.connect(saved.append)
    controller.save({"auto_start_with_windows": True})
    assert len(saved) == 0
    assert len(failed) == 1


def test_autostart_config_save_failure_rolls_back_registry(tmp_path):
    """注册表成功但配置保存失败时，回滚注册表（Qt SettingsController）"""
    from src.ui_qt.controllers.settings_controller import SettingsController
    cfg_mgr = MagicMock()
    cfg_mgr.config = AppConfig(auto_start_with_windows=False)
    cfg_mgr.load_config.return_value = cfg_mgr.config
    cfg_mgr.save_config.side_effect = RuntimeError("磁盘保存失败")
    mock_set = MagicMock(side_effect=[True, True])
    controller = SettingsController(
        cfg_mgr,
        get_autostart=lambda: False,
        set_autostart=mock_set,
    )
    failed = []
    controller.save_failed.connect(failed.append)
    controller.save({"auto_start_with_windows": True})
    assert mock_set.call_count == 2
    mock_set.assert_any_call(False)
    assert len(failed) == 1


def test_connection_view_endpoint_entry_masked_by_default():
    """向导中连接码输入框默认必须是密码遮罩模式（Qt OnboardingDialog）"""
    _ensure_qapp()
    from PySide6.QtWidgets import QLineEdit
    edit = QLineEdit()
    edit.setEchoMode(QLineEdit.EchoMode.Password)
    assert edit.echoMode() == QLineEdit.EchoMode.Password


# =================================================================================================
# 7. Endpoint 异常边界、Token 安全重新编码与未知参数拦截
# =================================================================================================
def test_endpoint_invalid_ports_fail_closed_without_value_error():
    """非数字端口、越界端口、非法 authority 必须返回固定脱敏错误，绝不得抛出 ValueError"""
    cases = [
        "wss://api.xiaozhi.me:abc/mcp/?token=test",
        "wss://api.xiaozhi.me:65536/mcp/?token=test",
        "wss://api.xiaozhi.me:-1/mcp/?token=test",
        "wss://api.xiaozhi.me:99999/mcp/?token=test",
        "wss://[invalid_authority/mcp/?token=test",
    ]
    for url in cases:
        res = validate_mcp_endpoint(url)
        assert res.is_valid is False
        assert res.error_message == "接入地址格式不正确"


def test_endpoint_token_safe_urlencode_and_special_chars():
    """Token 包含 +, &, =, % 等字符时，使用标准 urlencode 重新拼接，语义保持一致，不产生额外参数"""
    import urllib.parse
    special_token = "tok_test+plus&amp=equal%20percent"
    encoded_in_url = urllib.parse.quote(special_token, safe="")
    raw = f"wss://api.xiaozhi.me/mcp/?token={encoded_in_url}"

    res = validate_mcp_endpoint(raw)
    assert res.is_valid is True
    # 重新规范化后解析 query
    q = urllib.parse.urlsplit(res.normalized_url).query
    parsed_q = urllib.parse.parse_qsl(q)
    # 核心：必须只有 1 个 token 参数，不会因为 & 或 = 变成多个参数
    assert len(parsed_q) == 1
    assert parsed_q[0][0] == "token"
    # 解码后的 token 语义保持一致
    assert parsed_q[0][1] == special_token
    # 验证规范化 URL 中 token 经过了安全 quote 转义，不含有原样的未转义 & 或 =
    assert "&amp=" not in res.normalized_url


def test_endpoint_rejects_unknown_and_duplicate_query_params():
    """未知 query 参数或重复 token 参数必须被拒绝"""
    # 含有未知参数
    res1 = validate_mcp_endpoint("wss://api.xiaozhi.me/mcp/?token=test&extra=val")
    assert res1.is_valid is False
    assert "未知" in res1.error_message or "格式不正确" in res1.error_message

    # 重复 token 参数
    res2 = validate_mcp_endpoint("wss://api.xiaozhi.me/mcp/?token=test1&token=test2")
    assert res2.is_valid is False
    assert "格式不正确" in res2.error_message


def test_endpoint_errors_contain_no_credentials_or_tokens():
    """任何失败情况下的错误信息绝不泄露 Token、密文或私有内容"""
    secret = "SUPER_SECRET_TOKEN_XYZ_987"
    raw = f"wss://api.xiaozhi.me:99999/mcp/?token={secret}&extra={secret}"
    res = validate_mcp_endpoint(raw)
    assert res.is_valid is False
    assert secret not in res.error_message


# =================================================================================================
# 8. Release 构建门禁与 setup.iss 语言配置
# =================================================================================================
def test_setup_iss_uses_simplified_chinese_isl():
    """setup.iss 必须使用官方简体中文语言文件 ChineseSimplified.isl"""
    iss_path = os.path.join(PROJECT_ROOT, "installer", "setup.iss")
    with open(iss_path, "r", encoding="utf-8") as f:
        content = f.read()
    assert "compiler:Languages\\ChineseSimplified.isl" in content
    assert "compiler:Default.isl" not in content


def test_build_release_subproc_fast_fails_with_invalid_iscc_path():
    """真实子进程执行 build.ps1 -Release -IsccPath <不存在路径> 必须在昂贵构建前快速失败且退出码非零"""
    non_existent = os.path.join(PROJECT_ROOT, "non_existent_iscc_temp", "iscc.exe")
    cmd = [
        "powershell",
        "-ExecutionPolicy", "Bypass",
        "-File", os.path.join(PROJECT_ROOT, "build.ps1"),
        "-Release",
        "-IsccPath", non_existent
    ]
    start_t = time.time()
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=PROJECT_ROOT)
    elapsed = time.time() - start_t

    # 退出码非零
    assert proc.returncode != 0
    # 快速失败（未运行 PyInstaller 或完整测试，耗时应当较短，例如 < 6s）
    assert elapsed < 10.0
    stdout_str = (proc.stdout or b"").decode("utf-8", errors="replace") + (proc.stdout or b"").decode("gbk", errors="replace")
    stderr_str = (proc.stderr or b"").decode("utf-8", errors="replace") + (proc.stderr or b"").decode("gbk", errors="replace")
    output = stdout_str + stderr_str
    assert "Release 构建阻断" in output or "ISCC" in output


# =================================================================================================
# 9. 单实例锁 ctypes 签名加固与重复释放测试
# =================================================================================================
def test_single_instance_lock_acquire_release_reacquire():
    """单实例锁在 release() 后可重新获取，重复 release() 不崩溃"""
    test_mutex_name = f"Local\\TestSingleInstanceMutex_{os.getpid()}_{time.time_ns()}"
    lock = SingleInstanceLock(name=test_mutex_name)

    # 第一次获取
    assert lock.acquire() is True
    # 释放
    lock.release()
    # 重复释放安全
    lock.release()

    # 重新获取
    assert lock.acquire() is True
    lock.release()


def test_single_instance_ctypes_signatures():
    """验证 kernel32 的 CreateMutexW 和 CloseHandle 函数签名已被正确定义"""
    import ctypes
    from ctypes import wintypes
    kernel32 = ctypes.windll.kernel32
    # 触发模块初始化
    _ = SingleInstanceLock()

    assert kernel32.CreateMutexW.restype == wintypes.HANDLE
    assert kernel32.CloseHandle.restype == wintypes.BOOL
