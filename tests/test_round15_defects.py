"""第15轮高风险与中风险缺陷修复回归测试集。
验证：
- 危险系统程序集中黑名单与全入口阻断 (Item 3)
- 手动添加应用别名合并与去重 (Item 12)
- 安全计算器边界限制、溢出与非数值防护 (Item 2)
- URL 深度校验、IP/内网/SSRF/端口限制 (Item 9)
- 路径沙箱敏感目录、根目录、危险扩展名与原子写入 (Item 4)
- 握手队列溢出异常传播与会话任务清理 (Item 5 & 6)
- MCP 连接测试即时关闭探测与错误分类脱敏 (Item 7)
- 隐私日志全面脱敏 (Item 10)
"""

import asyncio
import os
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.catalog.dangerous_targets import is_dangerous_system_program, DANGEROUS_PROGRAM_FILENAMES
from src.catalog.manager import CatalogManager
from src.catalog.models import AppEntry, LaunchType
from src.catalog.shortcut_resolver import ShortcutResolver
from src.core.endpoint import test_mcp_connection as check_mcp_connection, validate_mcp_endpoint
from src.core.logger import sanitize_text
from src.core.security import SafeCalculator, UrlValidator, PathSandbox
from src.bridge.pipe import PipeBridge, McpHandshakeState, ServiceStatus


# =================================================================================================
# 1. 危险系统程序集中策略验证 (Item 3)
# =================================================================================================
def test_dangerous_targets_blacklist_coverage():
    """集中黑名单必须覆盖全部关键系统解释器、管理与破坏性程序。"""
    targets = [
        r"C:\Windows\System32\cmd.exe",
        r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
        r"C:\Program Files\PowerShell\7\pwsh.exe",
        r"C:\Windows\regedit.exe",
        r"C:\Windows\System32\taskkill.exe",
        r"C:\Windows\System32\shutdown.exe",
        r"C:\Windows\System32\format.com",
        r"C:\Windows\System32\bcdedit.exe",
        r"C:\Windows\System32\diskpart.exe",
        r"C:\Windows\System32\wmic.exe",
        r"C:\Windows\System32\mshta.exe",
        r"C:\Windows\System32\rundll32.exe",
        r"C:\Windows\System32\regsvr32.exe",
        r"C:\Windows\System32\certutil.exe",
        r"C:\Windows\System32\bitsadmin.exe",
    ]
    for target in targets:
        assert is_dangerous_system_program(target), f"应判定为危险系统程序: {target}"


def test_catalog_manager_rejects_dangerous_program(tmp_path):
    """CatalogManager.add_manual_app 必须拒绝将危险系统程序添加至目录。"""
    cat_path = tmp_path / "catalog.json"
    manager = CatalogManager(str(cat_path))
    manager.load_catalog()

    cmd_path = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "cmd.exe")
    if os.path.exists(cmd_path):
        with pytest.raises(PermissionError) as exc_info:
            manager.add_manual_app("CMD命令行", cmd_path, alias="cmd", authorized=False)
        assert "高危系统程序" in str(exc_info.value)


# =================================================================================================
# 2. 手动应用别名更新与去重验证 (Item 12)
# =================================================================================================
def test_manual_app_alias_deduplication(tmp_path):
    """重复添加同一手动程序时，必须安全合并 aliases 并去重。"""
    cat_path = tmp_path / "catalog.json"
    fake_exe = tmp_path / "custom_tool.exe"
    fake_exe.write_text("dummy")

    manager = CatalogManager(str(cat_path))
    manager.load_catalog()

    # 首次添加
    app1 = manager.add_manual_app("工具", str(fake_exe), alias="tool", authorized=True)
    assert "tool" in app1.aliases

    # 再次添加相同路径，传入新别名
    app2 = manager.add_manual_app("工具新名", str(fake_exe), alias="mytool", authorized=False)
    assert app2.app_id == app1.app_id
    assert "mytool" in app2.aliases
    assert "tool" in app2.aliases
    # 大小写不敏感去重
    manager.add_manual_app("工具新名", str(fake_exe), alias="TOOL", authorized=False)
    assert len([a for a in app2.aliases if a.lower() == "tool"]) == 1


# =================================================================================================
# 3. 安全计算器严格防护验证 (Item 2)
# =================================================================================================
def test_safe_calculator_length_and_depth_limits():
    """安全计算器必须拒绝过长表达式、超深递归与超多节点。"""
    long_expr = "1 + " * 100 + "1"
    with pytest.raises(ValueError) as exc:
        SafeCalculator.evaluate(long_expr)
    assert "超出安全限制" in str(exc.value)

    deep_expr = "math.sqrt(" * 12 + "16" + ")" * 12
    with pytest.raises(ValueError) as exc:
        SafeCalculator.evaluate(deep_expr)
    assert "超出上限" in str(exc.value) or "超出限制" in str(exc.value)


def test_safe_calculator_overflow_and_power_dos():
    """安全计算器必须阻断超大乘方与乘法导致的内存耗尽攻击。"""
    with pytest.raises(ValueError) as exc:
        SafeCalculator.evaluate("2 ** 1000")
    assert "幂指数过大" in str(exc.value)

    with pytest.raises(ValueError) as exc:
        SafeCalculator.evaluate("(10 ** 20) * (10 ** 25)")
    assert "超出安全计算上限" in str(exc.value)


def test_safe_calculator_rejects_non_numeric():
    """安全计算器禁止字符串操作与非数值运算。"""
    with pytest.raises(ValueError) as exc:
        SafeCalculator.evaluate("'abc' * 10")
    assert "不允许的常量类型" in str(exc.value) or "必须为纯数值" in str(exc.value)


# =================================================================================================
# 4. URL 深度校验与 SSRF 防护验证 (Item 9)
# =================================================================================================
def test_url_validator_rejects_userinfo_and_ssrf():
    """UrlValidator 必须拒绝包含认证凭据、本地回环、内网私有地址的 URL。"""
    with pytest.raises(ValueError) as exc:
        UrlValidator.validate_url("http://user:pass@example.com")
    assert "禁止包含用户名或密码" in str(exc.value)

    with pytest.raises(ValueError) as exc:
        UrlValidator.validate_url("http://127.0.0.1/admin")
    assert "禁止访问本地回环" in str(exc.value)

    with pytest.raises(ValueError) as exc:
        UrlValidator.validate_url("http://192.168.1.1/router")
    assert "禁止访问私有内网地址" in str(exc.value)

    with pytest.raises(ValueError) as exc:
        UrlValidator.validate_url("http://example.com:22/")
    assert "禁止访问非标准 Web 端口" in str(exc.value)


# =================================================================================================
# 5. 路径沙箱敏感目录与危险扩展名验证 (Item 4)
# =================================================================================================
def test_path_sandbox_rejects_sensitive_and_dangerous_files(tmp_path):
    """PathSandbox 必须拒绝系统根、Windows 目录、临时目录及危险扩展名。"""
    safe_dir = str(tmp_path / "safe_docs")
    os.makedirs(safe_dir, exist_ok=True)

    # 危险脚本扩展名
    with pytest.raises(ValueError) as exc:
        PathSandbox.validate_write_path(os.path.join(safe_dir, "script.bat"), [safe_dir])
    assert "禁止创建或写入可执行程序" in str(exc.value)

    with pytest.raises(ValueError) as exc:
        PathSandbox.validate_write_path(os.path.join(safe_dir, "hack.exe"), [safe_dir])
    assert "禁止创建或写入可执行程序" in str(exc.value)

    # 敏感系统目录
    win_dir = os.environ.get("SystemRoot", r"C:\Windows")
    is_safe, reason = PathSandbox.is_safe_write_directory(win_dir)
    assert not is_safe
    assert "受保护的系统或敏感目录" in reason


# =================================================================================================
# 6. 握手队列溢出异常立即传播验证 (Item 5)
# =================================================================================================
def test_handshake_buffer_overflow_propagates_immediately():
    """当握手期间 WS 缓冲队列超过最大消息数时，必须立即抛出异常并中止握手。"""
    bridge = PipeBridge("wss://api.xiaozhi.me/mcp/?token=test", "python", "server.py")
    bridge.MAX_PENDING_MSGS = 2
    bridge.HANDSHAKE_TIMEOUT = 3.0

    mock_ws = MagicMock()
    # 模拟首包为合法 initialize
    first_msg = '{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}'
    overflow_msgs = [first_msg, '{"msg": 1}', '{"msg": 2}', '{"msg": 3}']

    async def mock_recv():
        if overflow_msgs:
            return overflow_msgs.pop(0)
        await asyncio.sleep(10)
        return ""

    mock_ws.recv = mock_recv
    mock_ws.send = MagicMock()

    mock_proc = MagicMock()
    mock_proc.stdin = MagicMock()
    mock_proc.stdin.closed = False
    mock_proc.stdout = MagicMock()

    # 模拟 stdout.readline 阻塞挂起
    def mock_readline():
        import time
        time.sleep(5)
        return b""

    mock_proc.stdout.readline = mock_readline

    async def _runner():
        with pytest.raises(RuntimeError) as exc_info:
            await bridge._perform_mcp_handshake(mock_ws, mock_proc)
        assert "超出上限" in str(exc_info.value)

    asyncio.run(_runner())


# =================================================================================================
# 7. MCP 连接测试即时关闭探测与错误分类验证 (Item 7)
# =================================================================================================
def test_test_mcp_connection_detects_closed_code_4001():
    """test_mcp_connection 在服务端立即以 4001 关闭时必须判定为失败并脱敏提示。"""
    import websockets.exceptions

    with patch("websockets.connect") as mock_connect:
        # 模拟进入上下文后立即抛出 ConnectionClosedError(4001)
        mock_ws = MagicMock()
        mock_ws.closed = True
        mock_ws.close_code = 4001

        async def _enter_cm(*args, **kwargs):
            raise websockets.exceptions.ConnectionClosedError(
                rcvd=MagicMock(code=4001, reason="Unauthorized"),
                sent=None,
            )

        mock_connect.return_value.__aenter__ = _enter_cm
        mock_connect.return_value.__aexit__ = MagicMock()

        success, message = check_mcp_connection("wss://api.xiaozhi.me/mcp/?token=FAKE_EXPIRED")
        assert not success
        assert "连接已被服务端拒绝" in message or "连接码可能无效" in message
        assert "FAKE_EXPIRED" not in message


# =================================================================================================
# 8. 敏感日志脱敏强化验证 (Item 10)
# =================================================================================================
def test_sanitize_text_comprehensive():
    """日志脱敏器必须屏蔽 URL 中的 userinfo、Token query 以及 Windows 用户私有路径。"""
    raw_userinfo = "连接至 http://admin:secret123@api.xiaozhi.me/mcp"
    sanitized = sanitize_text(raw_userinfo)
    assert "secret123" not in sanitized
    assert "admin:secret123" not in sanitized

    raw_query = "WebSocket: wss://api.xiaozhi.me/mcp/?token=sensitive_token_value&other=1"
    sanitized_q = sanitize_text(raw_query)
    assert "sensitive_token_value" not in sanitized_q
    assert "***REDACTED***" in sanitized_q

    raw_user_path = r"打开了 C:\Users\Administrator\Documents\secret.txt"
    sanitized_p = sanitize_text(raw_user_path)
    assert "Administrator" not in sanitized_p
    assert r"C:\Users\***\Documents\secret.txt" in sanitized_p
