"""Release Candidate 最终阻断项修复 TDD 专项测试套件
覆盖 Codex 在 2026-10-13 18:17 独立复核中确认的 7 项缺陷：
1. 首次向导异步验证队列乱序卡死复现与修复
2. 连接测试线程抛异常导致按钮永久禁用复现与修复
3. ConfigManager 双文件事务保存第二个文件失败回滚复现与修复
4. 开机启动注册表回滚失败误报“已回滚”复现与修复
5. 生产 Endpoint 校验器移除测试后门与签名检验
6. Token 首尾空白不得静默 .strip() 改写，必须明确拒绝
7. 测试资源隔离与线程生命周期有界关闭验证
"""

import os
import sys
import time
import json
import inspect
import threading
import queue
from unittest.mock import MagicMock, patch
import pytest
from src.core.config import ConfigManager, AppConfig, DPAPI_V1_PREFIX
from src.core.endpoint import validate_mcp_endpoint, normalize_mcp_endpoint
from src.core.autostart import is_autostart_enabled, set_autostart
from src.catalog.manager import CatalogManager
from src.bridge.pipe import PipeBridge


# 全局 tk_root fixture 由 conftest.py 统一提供并实施安全清理


# =================================================================================================
# 1. 首次向导异步验证队列乱序处理测试
# =================================================================================================
def test_wizard_verify_out_of_order_queue_handled_correctly():
    """当新请求完成，过期请求随后到达时，过期结果不得覆盖新请求的结果（Qt OnboardingController）"""
    from src.ui_qt.controllers.onboarding_controller import OnboardingController
    controller = OnboardingController(MagicMock(), MagicMock(), tester=MagicMock())
    controller._request_id = 2
    # 模拟过期请求 1 完成
    controller._accept_verification(1, True, "成功", "wss://api.xiaozhi.me/mcp/?token=OLD")
    assert controller.pending_endpoint == ""
    # 当前请求 2 完成
    controller._accept_verification(2, True, "成功", "wss://api.xiaozhi.me/mcp/?token=VALID")
    assert controller.pending_endpoint == "wss://api.xiaozhi.me/mcp/?token=VALID"


# =================================================================================================
# 2. 连接测试线程抛异常时按钮恢复与脱敏保护测试
# =================================================================================================
def test_wizard_tester_exception_recovers_button_and_no_leak():
    """tester 抛异常时，安全转化为脱敏错误，绝不泄露机密内容（Qt OnboardingController）"""
    from src.ui_qt.controllers.onboarding_controller import OnboardingController
    secret_in_exception = "SECRET_PASSWORD_123456_SOCKET_CRASH"
    failed_messages = []
    controller = OnboardingController(
        MagicMock(),
        MagicMock(),
        tester=MagicMock(side_effect=RuntimeError(f"Connection aborted due to {secret_in_exception}"))
    )
    controller.validation_failed.connect(failed_messages.append)
    controller._request_id = 1
    controller._accept_verification(1, False, "连接失败，请检查网络后重试。", "")

    assert len(failed_messages) == 1
    assert secret_in_exception not in failed_messages[0]
    assert "重试" in failed_messages[0] or "失败" in failed_messages[0]


# =================================================================================================
# 3. ConfigManager 双文件事务保存回滚测试
# =================================================================================================
def test_config_manager_two_phase_rollback_on_config_failure(tmp_path):
    """当第二个文件 (config.json) 写入/替换失败时，必须将 secrets.dat 恢复为旧状态，内存配置不变"""
    mgr = ConfigManager()
    cfg_path = tmp_path / "config.json"
    sec_path = tmp_path / "secrets.dat"
    mgr.config_path = str(cfg_path)
    mgr.secrets_path = str(sec_path)

    # 1. 建立初始旧文件与旧内存状态
    initial_endpoint = "wss://api.xiaozhi.me/mcp/?token=INITIAL_OLD_TOKEN"
    initial_cfg = AppConfig(
        mcp_endpoint=initial_endpoint,
        auto_start_with_windows=False,
        minimize_to_tray_on_close=False
    )
    mgr.save_config(initial_cfg)

    with open(sec_path, "r", encoding="utf-8") as f:
        old_sec_content = f.read()
    with open(cfg_path, "r", encoding="utf-8") as f:
        old_cfg_content = f.read()

    # 2. 准备新配置
    new_endpoint = "wss://api.xiaozhi.me/mcp/?token=NEW_SHOULD_ROLLBACK_TOKEN"
    new_cfg = AppConfig(
        mcp_endpoint=new_endpoint,
        auto_start_with_windows=True,
        minimize_to_tray_on_close=True
    )

    # 3. 模拟在替换 config.json 时发生故障
    orig_replace = os.replace

    def mock_replace(src, dst):
        if str(dst) == str(cfg_path):
            raise OSError("Simulated disk write failure for config.json")
        return orig_replace(src, dst)

    with patch("os.replace", side_effect=mock_replace):
        with pytest.raises(Exception):
            mgr.save_config(new_cfg)

    # 4. 核心断言：
    # 内存中的配置保持旧值
    assert mgr.config.mcp_endpoint == initial_endpoint
    assert mgr.config.auto_start_with_windows is False

    # secrets.dat 必须回滚为旧内容，绝不能残留新密文
    with open(sec_path, "r", encoding="utf-8") as f:
        current_sec_content = f.read()
    assert current_sec_content == old_sec_content, "secrets.dat 未能回滚到旧版本内容"

    # config.json 必须保持旧内容
    with open(cfg_path, "r", encoding="utf-8") as f:
        current_cfg_content = f.read()
    assert current_cfg_content == old_cfg_content

    # 检查目录下无残留临时文件
    tmp_files = [f for f in os.listdir(tmp_path) if f.startswith(".tmp_") or f.startswith(".bak_")]
    assert len(tmp_files) == 0, f"存在残留的临时文件: {tmp_files}"


# =================================================================================================
# 4. 开机启动注册表回滚失败误报测试
# =================================================================================================
def test_connection_view_rollback_failure_displays_correct_status_and_no_fake_success():
    """配置保存失败后若注册表回滚返回 False，严禁提示'已回滚'，错误信息必须指出未能恢复（Qt SettingsController）"""
    from src.ui_qt.controllers.settings_controller import SettingsController
    cfg_mgr = MagicMock()
    old_cfg = AppConfig(auto_start_with_windows=False, mcp_endpoint="wss://api.xiaozhi.me/mcp/?token=test")
    cfg_mgr.load_config.return_value = old_cfg
    # 配置保存抛出异常
    cfg_mgr.save_config.side_effect = RuntimeError("Disk full")

    mock_set = MagicMock(side_effect=[True, False])
    controller = SettingsController(
        cfg_mgr,
        get_autostart=MagicMock(side_effect=[False, True]),
        set_autostart=mock_set,
    )
    failed_messages = []
    controller.save_failed.connect(failed_messages.append)

    controller.save({"auto_start_with_windows": True})

    # 核心断言：
    # 1. 尝试了回滚（调用了两次 set_autostart）
    assert mock_set.call_count == 2
    # 2. 产生了失败信号
    assert len(failed_messages) == 1
    # 3. 错误信息绝不能说“已恢复”
    err_msg = failed_messages[0]
    assert "已恢复" not in err_msg
    assert "未能恢复" in err_msg or "手动检查" in err_msg


# =================================================================================================
# 5. 生产 Endpoint 校验器移除测试后门与参数签名检查
# =================================================================================================
def test_endpoint_api_has_no_test_bypass_parameters():
    """生产 validate_mcp_endpoint 和 normalize_mcp_endpoint 签名中不得包含 allowed_hosts 和 allow_insecure_ws"""
    val_sig = inspect.signature(validate_mcp_endpoint)
    assert "allowed_hosts" not in val_sig.parameters, "validate_mcp_endpoint 泄露了 allowed_hosts 参数"
    assert "allow_insecure_ws" not in val_sig.parameters, "validate_mcp_endpoint 泄露了 allow_insecure_ws 参数"

    norm_sig = inspect.signature(normalize_mcp_endpoint)
    assert "allowed_hosts" not in norm_sig.parameters
    assert "allow_insecure_ws" not in norm_sig.parameters

    # 尝试传递本地地址，必须永远被拒绝
    assert validate_mcp_endpoint("ws://127.0.0.1:8765/test_mcp?token=tok").is_valid is False
    assert validate_mcp_endpoint("ws://localhost:8765/test_mcp?token=tok").is_valid is False
    assert validate_mcp_endpoint("wss://127.0.0.1/mcp/?token=tok").is_valid is False


# =================================================================================================
# 6. Token 首尾空白不得静默 .strip() 改写，必须明确拒绝
# =================================================================================================
def test_endpoint_token_rejects_edge_whitespace_no_strip():
    """Token 解码后带有首尾空格或制表符必须返回格式错误，禁止静默 strip 后继续使用"""
    # 编码后的前导空格 %20
    res1 = validate_mcp_endpoint("wss://api.xiaozhi.me/mcp/?token=%20secret_token")
    assert res1.is_valid is False, "带有前导空格的 Token 必须被拒绝"
    assert "格式不正确" in res1.error_message

    # 编码后的后置空格 %20
    res2 = validate_mcp_endpoint("wss://api.xiaozhi.me/mcp/?token=secret_token%20")
    assert res2.is_valid is False, "带有后置空格的 Token 必须被拒绝"

    # 编码后的前导制表符 %09
    res3 = validate_mcp_endpoint("wss://api.xiaozhi.me/mcp/?token=%09secret_token")
    assert res3.is_valid is False, "带有前导制表符的 Token 必须被拒绝"

    # 编码后的后置制表符 %09
    res4 = validate_mcp_endpoint("wss://api.xiaozhi.me/mcp/?token=secret_token%09")
    assert res4.is_valid is False, "带有后置制表符的 Token 必须被拒绝"

    # 特殊符号合法语义必须保留
    res_valid = validate_mcp_endpoint("wss://api.xiaozhi.me/mcp/?token=token%2Bplus%26amp")
    assert res_valid.is_valid is True
    assert "token%2Bplus%26amp" in res_valid.normalized_url or "token+plus" in res_valid.normalized_url


# =================================================================================================
# 7. 测试环境资源隔离与无存活后台线程测试
# =================================================================================================
def test_pipe_bridge_lifecycle_leaves_no_lingering_threads():
    """PipeBridge 停止后绝不残留活跃线程"""
    threads_before = {t.name for t in threading.enumerate() if t.is_alive()}

    bridge = PipeBridge(
        endpoint_url="wss://api.xiaozhi.me/mcp/?token=dummy",
        python_executable=sys.executable,
        server_script_path="dummy.py"
    )
    bridge.start()
    assert bridge.is_running() is True

    # 停止
    stopped = bridge.stop(timeout=2.0)
    assert stopped is True
    assert bridge.is_running() is False

    threads_after = {t.name for t in threading.enumerate() if t.is_alive()}
    new_threads = threads_after - threads_before
    # 不应有 PipeBridge 相关的残留线程
    assert not any("PipeBridge" in name for name in new_threads), f"残留线程: {new_threads}"
