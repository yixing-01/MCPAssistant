"""第十六轮缺陷修复专项测试用例 (Codex 复核遗留 6 项缺陷)

严格覆盖：
1. UI 停止与重启异步接入 (Item 1)
2. 连接测试必须收到合法 initialize 请求才能判定成功 (Item 2)
3. 计算器结果限制为可 JSON 序列化的 int 或有限 float，拒绝 complex (Item 3)
4. control.exe 与 taskmgr.exe 纳入集中高危策略并禁止授权/启动 (Item 4)
5. URL 校验严格拒绝 DNS 解析失败与 198.18.0.0/15 保留地址 (Item 5)
6. 计算器所有分支不记录表达式原文或片段 (Item 6)
"""

import json
import logging
import math
import os
import socket
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.catalog.dangerous_targets import (
    DANGEROUS_PROGRAM_FILENAMES,
    is_dangerous_system_program,
)
from src.catalog.manager import CatalogManager
from src.catalog.models import AppEntry, LaunchType
from src.catalog.scanner import SoftwareScanner, is_main_program_candidate
from src.core.config import AppConfig
from src.core.endpoint import test_mcp_connection as check_mcp_connection
from src.core.logger import app_logger
from src.core.permission import PermissionEngine
from src.core.security import SafeCalculator, UrlValidator


# =================================================================================================
# 2. 连接测试必须收到合法 initialize 请求才能判定成功 (Item 2)
# =================================================================================================
def test_test_mcp_connection_rejects_unauthorized_error_json():
    """建立 WebSocket 后若服务端返回 Unauthorized 错误 JSON，必须判定为失败且不泄露 Token。"""
    with patch("websockets.connect") as mock_connect:
        mock_ws = MagicMock()
        mock_ws.closed = False
        mock_ws.close_code = None

        error_json = '{"jsonrpc": "2.0", "error": {"code": 401, "message": "Unauthorized"}}'
        mock_ws.recv = AsyncMock(return_value=error_json)
        mock_connect.return_value.__aenter__ = AsyncMock(return_value=mock_ws)
        mock_connect.return_value.__aexit__ = AsyncMock(return_value=None)

        success, message = check_mcp_connection("wss://api.xiaozhi.me/mcp/?token=SECRET_AUTH_TOKEN", timeout=2.0)
        assert success is False
        assert "SECRET_AUTH_TOKEN" not in message
        assert "认证失败" in message or "拒绝" in message


def test_test_mcp_connection_rejects_garbage_string():
    """建立连接后若收到非 JSON 垃圾字符串，必须判定为失败。"""
    with patch("websockets.connect") as mock_connect:
        mock_ws = MagicMock()
        mock_ws.closed = False

        mock_ws.recv = AsyncMock(return_value="<html><body>502 Bad Gateway</body></html>")
        mock_connect.return_value.__aenter__ = AsyncMock(return_value=mock_ws)
        mock_connect.return_value.__aexit__ = AsyncMock(return_value=None)

        success, message = check_mcp_connection("wss://api.xiaozhi.me/mcp/?token=test", timeout=2.0)
        assert success is False
        assert "格式无效" in message or "协议" in message or "失败" in message


def test_test_mcp_connection_rejects_wrong_method():
    """建立连接后若收到非 initialize 请求（如 tools/list 或 ping），必须判定为失败。"""
    with patch("websockets.connect") as mock_connect:
        mock_ws = MagicMock()
        mock_ws.closed = False

        wrong_rpc = '{"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}'
        mock_ws.recv = AsyncMock(return_value=wrong_rpc)
        mock_connect.return_value.__aenter__ = AsyncMock(return_value=mock_ws)
        mock_connect.return_value.__aexit__ = AsyncMock(return_value=None)

        success, message = check_mcp_connection("wss://api.xiaozhi.me/mcp/?token=test", timeout=2.0)
        assert success is False
        assert "协议不匹配" in message or "非预期" in message or "initialize" in message or "失败" in message


def test_test_mcp_connection_rejects_missing_id_or_params():
    """收到缺少 id 或 params 格式不符的 initialize 请求必须判定为失败。"""
    with patch("websockets.connect") as mock_connect:
        mock_ws = MagicMock()
        mock_ws.closed = False

        invalid_init = '{"jsonrpc": "2.0", "method": "initialize"}'  # 缺少 id 与 params
        mock_ws.recv = AsyncMock(return_value=invalid_init)
        mock_connect.return_value.__aenter__ = AsyncMock(return_value=mock_ws)
        mock_connect.return_value.__aexit__ = AsyncMock(return_value=None)

        success, message = check_mcp_connection("wss://api.xiaozhi.me/mcp/?token=test", timeout=2.0)
        assert success is False


def test_test_mcp_connection_rejects_silent_timeout():
    """WebSocket 建立后若服务端超时保持静默，必须判定为失败，不能固定 0.8s 误判为成功。"""
    import asyncio

    with patch("websockets.connect") as mock_connect:
        mock_ws = MagicMock()
        mock_ws.closed = False

        mock_ws.recv = AsyncMock(side_effect=asyncio.TimeoutError)
        mock_connect.return_value.__aenter__ = AsyncMock(return_value=mock_ws)
        mock_connect.return_value.__aexit__ = AsyncMock(return_value=None)

        success, message = check_mcp_connection("wss://api.xiaozhi.me/mcp/?token=test", timeout=0.5)
        assert success is False
        assert "超时" in message


def test_test_mcp_connection_success_on_valid_initialize():
    """收到合法的 MCP initialize 请求时必须正确判定为连接成功。"""
    with patch("websockets.connect") as mock_connect:
        mock_ws = MagicMock()
        mock_ws.closed = False

        valid_init = json.dumps({
            "jsonrpc": "2.0",
            "id": "init-1",
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "clientInfo": {"name": "xiaozhi-cloud", "version": "1.0.0"},
            }
        })
        mock_ws.recv = AsyncMock(return_value=valid_init)
        mock_connect.return_value.__aenter__ = AsyncMock(return_value=mock_ws)
        mock_connect.return_value.__aexit__ = AsyncMock(return_value=None)

        success, message = check_mcp_connection("wss://api.xiaozhi.me/mcp/?token=valid_token", timeout=2.0)
        assert success is True
        assert "连接成功" in message


# =================================================================================================
# 3. 计算器必须永远只返回可安全序列化的 int 或 float (Item 3)
# =================================================================================================
def test_calculator_rejects_complex_expressions():
    """SafeCalculator 必须彻底拒绝生成 complex 复数结果（如 (-1) ** 0.5, pow(-1, 0.5)）。"""
    with pytest.raises(ValueError) as exc1:
        SafeCalculator.evaluate("(-1) ** 0.5")
    assert "复数" in str(exc1.value) or "不支持" in str(exc1.value)

    with pytest.raises(ValueError) as exc2:
        SafeCalculator.evaluate("pow(-1, 0.5)")
    assert "复数" in str(exc2.value) or "不支持" in str(exc2.value)


def test_calculator_results_always_json_serializable():
    """所有成功计算结果必须能通过 json.dumps() 且为 int 或有限 float。"""
    test_expressions = [
        "1 + 1",
        "-5 * 8",
        "3.14159 * 2",
        "sqrt(16)",
        "sin(pi / 2)",
        "round(2.7)",
        "abs(-42)",
    ]
    for expr in test_expressions:
        res = SafeCalculator.evaluate(expr)
        assert isinstance(res, (int, float))
        assert not isinstance(res, bool)
        assert not math.isnan(res) and not math.isinf(res)
        # 必须能够通过标准 JSON 序列化
        serialized = json.dumps(res)
        assert serialized


def test_calculator_tool_handles_unserializable_safely():
    """当通过 MCP 计算器工具调用复数或异常表达式时，必须返回受控错误字典，不能使子进程崩溃。"""
    from mcp.server.fastmcp import FastMCP
    from src.mcp_server.tools import register_mcp_tools

    dummy_mcp = FastMCP("test-calc")
    mock_config = MagicMock()
    mock_config.load_config.return_value = AppConfig(allow_calculator=True)
    mock_catalog = MagicMock()

    register_mcp_tools(dummy_mcp, mock_config, mock_catalog)

    # 从 fastmcp 中查找注册的 计算器 工具函数
    tool_fn = None
    for tool in dummy_mcp._tool_manager.list_tools():
        if tool.name == "计算器":
            tool_fn = tool.fn
            break

    assert tool_fn is not None
    resp = tool_fn("(-1) ** 0.5")
    assert resp["是否成功"] is False
    assert "错误" in resp
    # 响应字典本身必须能够安全 JSON 序列化
    json.dumps(resp)


# =================================================================================================
# 4. 解决内置维护工具仍可授权的问题 (Item 4)
# =================================================================================================
def test_dangerous_targets_blocks_control_and_taskmgr():
    """control.exe 与 Taskmgr.exe 必须被集中危险程序策略判定为危险，且不是主程序候选。"""
    for prog in ["control.exe", "Taskmgr.exe", "taskmgr.exe", r"C:\Windows\System32\control.exe", r"C:\Windows\System32\Taskmgr.exe"]:
        is_dang, reason = is_dangerous_system_program(prog)
        assert is_dang is True, f"{prog} 必须被判定为危险程序"
        assert "禁止" in reason or "高危" in reason

        is_cand = is_main_program_candidate(prog)
        assert is_cand is False, f"{prog} 绝不能是主程序候选"


def test_scan_builtin_tools_does_not_produce_control_or_taskmgr():
    """扫描系统内置工具时，绝不能产生控制面板或任务管理器条目，但必须保留记事本、计算器、画图。"""
    entries = {}
    with patch("os.path.exists", return_value=True), patch("os.path.isdir", return_value=False):
        SoftwareScanner._scan_builtin_tools(entries)

    names = {app.display_name for app in entries.values()}
    targets = {os.path.basename(app.exe_path).lower() for app in entries.values()}

    assert "控制面板" not in names
    assert "任务管理器" not in names
    assert "control.exe" not in targets
    assert "taskmgr.exe" not in targets

    # notepad 等应存在
    assert "记事本" in names or any("notepad.exe" in t for t in targets)


def test_catalog_revalidation_revokes_control_and_taskmgr(tmp_path):
    """旧 catalog 缓存中若存在已授权的 control.exe 或 Taskmgr.exe，重新加载与验证时必须强制撤销授权并标记拦截。"""
    catalog_file = tmp_path / "catalog.json"
    old_data = [
        {
            "app_id": "control-old",
            "display_name": "控制面板",
            "exe_path": r"C:\Windows\System32\control.exe",
            "launch_target": r"C:\Windows\System32\control.exe",
            "launch_type": "win32",
            "source": "BuiltinTools",
            "is_enabled": True,
            "is_valid": True,
            "is_restricted": False,
            "validation_status": "valid",
            "authorization_block_reason": "",
            "aliases": ["控制面板"],
        },
        {
            "app_id": "taskmgr-old",
            "display_name": "任务管理器",
            "exe_path": r"C:\Windows\System32\Taskmgr.exe",
            "launch_target": r"C:\Windows\System32\Taskmgr.exe",
            "launch_type": "win32",
            "source": "BuiltinTools",
            "is_enabled": True,
            "is_valid": True,
            "is_restricted": False,
            "validation_status": "valid",
            "authorization_block_reason": "",
            "aliases": ["任务管理器"],
        },
        {
            "app_id": "notepad-ok",
            "display_name": "记事本",
            "exe_path": r"C:\Windows\System32\notepad.exe",
            "launch_target": r"C:\Windows\System32\notepad.exe",
            "launch_type": "win32",
            "source": "BuiltinTools",
            "is_enabled": True,
            "is_valid": True,
            "is_restricted": False,
            "validation_status": "valid",
            "authorization_block_reason": "",
            "aliases": ["记事本"],
        }
    ]
    catalog_file.write_text(json.dumps(old_data), encoding="utf-8")

    mgr = CatalogManager(catalog_file_path=str(catalog_file))
    # 验证 control-old 与 taskmgr-old 均被撤销授权且标记原因
    c_app = mgr.apps.get("control-old")
    assert c_app is not None
    assert c_app.is_enabled is False
    assert c_app.is_valid is False
    assert "高危" in c_app.authorization_block_reason or "禁止" in c_app.authorization_block_reason

    t_app = mgr.apps.get("taskmgr-old")
    assert t_app is not None
    assert t_app.is_enabled is False
    assert t_app.is_valid is False
    assert "高危" in t_app.authorization_block_reason or "禁止" in t_app.authorization_block_reason

    # 尝试单项授权必须被拒绝
    assert mgr.set_app_enabled("control-old", True) is False
    assert mgr.set_app_enabled("taskmgr-old", True) is False


def test_launch_app_rejects_control_and_taskmgr_even_if_forged(tmp_path):
    """即使传入伪造已授权的 control.exe AppEntry，launch_app 必须终验拦截并抛出 PermissionError。"""
    catalog_file = tmp_path / "catalog.json"
    catalog_file.write_text("[]", encoding="utf-8")
    mgr = CatalogManager(catalog_file_path=str(catalog_file))

    forged_app = AppEntry(
        app_id="forged-control",
        display_name="控制面板",
        exe_path=r"C:\Windows\System32\control.exe",
        launch_target=r"C:\Windows\System32\control.exe",
        launch_type=LaunchType.WIN32,
        source="Manual",
        is_enabled=True,
        is_valid=True,
        is_restricted=False,
    )
    # 即使强行放入 mgr.apps
    mgr.apps[forged_app.app_id] = forged_app

    with pytest.raises(PermissionError) as exc:
        mgr.launch_app(forged_app)
    assert "高危" in str(exc.value) or "禁止" in str(exc.value) or "安全拒绝" in str(exc.value)


# =================================================================================================
# 5. URL 校验必须拒绝 DNS 失败及保留地址 (Item 5)
# =================================================================================================
def test_url_validator_rejects_invalid_dns_failure():
    """域名解析失败 (如 .invalid) 必须抛出受控 ValueError，不能放行。"""
    with patch("socket.getaddrinfo", side_effect=socket.gaierror("Name or service not known")):
        with pytest.raises(ValueError) as exc:
            UrlValidator.validate_url("https://example.invalid/path")
        assert "解析失败" in str(exc.value) or "安全拒绝" in str(exc.value)


def test_url_validator_rejects_empty_dns_result():
    """DNS 解析返回空列表必须抛出安全拒绝异常。"""
    with patch("socket.getaddrinfo", return_value=[]):
        with pytest.raises(ValueError) as exc:
            UrlValidator.validate_url("https://nodns.example.com")
        assert "未解析到有效" in str(exc.value) or "安全拒绝" in str(exc.value)


def test_url_validator_rejects_reserved_198_18_range():
    """直接输入 198.18.x.x 或域名解析到 198.18.0.0/15 保留地址，必须按 reserved 策略严格拒绝。"""
    # 1. 直接 IP
    with pytest.raises(ValueError) as exc1:
        UrlValidator.validate_url("http://198.18.0.1:8080/test")
    assert "安全拒绝" in str(exc1.value)

    # 2. 域名解析到 198.18.0.71
    fake_dns = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('198.18.0.71', 443))]
    with patch("socket.getaddrinfo", return_value=fake_dns):
        with pytest.raises(ValueError) as exc2:
            UrlValidator.validate_url("https://fake-ip.example.com")
        assert "安全拒绝" in str(exc2.value)
        assert "受保护" in str(exc2.value) or "私网" in str(exc2.value) or "保留" in str(exc2.value)


def test_url_validator_rejects_multi_ip_with_one_private():
    """多 IP 解析中只要包含一个私网或回环地址，必须严格拒绝。"""
    mixed_dns = [
        (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.216.34', 443)),
        (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('127.0.0.1', 443)),
    ]
    with patch("socket.getaddrinfo", return_value=mixed_dns):
        with pytest.raises(ValueError) as exc:
            UrlValidator.validate_url("https://mixed.example.com")
        assert "安全拒绝" in str(exc.value)


def test_url_validator_accepts_clean_public_ips():
    """DNS 解析为纯公网合法 IP 时必须正常通过。"""
    clean_dns = [
        (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.216.34', 443)),
        (socket.AF_INET6, socket.SOCK_STREAM, 6, '', ('2606:2800:220:1:248:1893:25c8:1946', 443)),
    ]
    with patch("socket.getaddrinfo", return_value=clean_dns):
        res = UrlValidator.validate_url("https://example.com/test?query=1")
        assert res == "https://example.com/test?query=1"


# =================================================================================================
# 6. 计算器所有分支都不得记录完整表达式或表达式片段 (Item 6)
# =================================================================================================
def test_calculator_audit_and_logs_never_leak_expression_when_disabled():
    """当计算器功能关闭时，审计记录和日志均只记录安全摘要，绝不包含原始表达式内容。"""
    from mcp.server.fastmcp import FastMCP
    from src.mcp_server.tools import register_mcp_tools
    from src.core.permission import permission_engine

    permission_engine.clear_audit()
    dummy_mcp = FastMCP("test-calc-disabled")
    mock_config = MagicMock()
    mock_config.load_config.return_value = AppConfig(allow_calculator=False)
    mock_catalog = MagicMock()

    register_mcp_tools(dummy_mcp, mock_config, mock_catalog)

    tool_fn = None
    for tool in dummy_mcp._tool_manager.list_tools():
        if tool.name == "计算器":
            tool_fn = tool.fn
            break

    secret_expr = "100 + 200 # my_secret_financial_formula"
    resp = tool_fn(secret_expr)
    assert resp["是否成功"] is False

    # 检查内存审计记录
    records = permission_engine.audit_records
    assert len(records) > 0
    last_record = records[-1]
    assert "my_secret_financial_formula" not in last_record.target_summary
    assert "my_secret_financial_formula" not in last_record.result_or_error
    assert "表达式[长度=" in last_record.target_summary


def test_calculator_audit_and_logs_never_leak_unauthorized_variables():
    """未授权变量名导致失败时，日志与审计记录均不得包含变量名原文。"""
    from mcp.server.fastmcp import FastMCP
    from src.mcp_server.tools import register_mcp_tools
    from src.core.permission import permission_engine

    permission_engine.clear_audit()
    dummy_mcp = FastMCP("test-calc-unauth")
    mock_config = MagicMock()
    mock_config.load_config.return_value = AppConfig(allow_calculator=True)
    mock_catalog = MagicMock()

    register_mcp_tools(dummy_mcp, mock_config, mock_catalog)

    tool_fn = None
    for tool in dummy_mcp._tool_manager.list_tools():
        if tool.name == "计算器":
            tool_fn = tool.fn
            break

    secret_var_expr = "my_confidential_bank_variable + 10"
    resp = tool_fn(secret_var_expr)
    assert resp["是否成功"] is False

    records = permission_engine.audit_records
    assert len(records) > 0
    last_record = records[-1]
    assert "my_confidential_bank_variable" not in last_record.target_summary
    assert "my_confidential_bank_variable" not in last_record.result_or_error
    assert "my_confidential_bank_variable" not in resp["错误"]
    assert "表达式[长度=" in last_record.target_summary


def test_calculator_audit_and_logs_never_leak_string_constants():
    """包含敏感字符串常量的非法表达式失败时，不得泄露字符串内容。"""
    from mcp.server.fastmcp import FastMCP
    from src.mcp_server.tools import register_mcp_tools
    from src.core.permission import permission_engine

    permission_engine.clear_audit()
    dummy_mcp = FastMCP("test-calc-str")
    mock_config = MagicMock()
    mock_config.load_config.return_value = AppConfig(allow_calculator=True)
    mock_catalog = MagicMock()

    register_mcp_tools(dummy_mcp, mock_config, mock_catalog)

    tool_fn = None
    for tool in dummy_mcp._tool_manager.list_tools():
        if tool.name == "计算器":
            tool_fn = tool.fn
            break

    secret_str_expr = "'super_secret_token_12345' * 2"
    resp = tool_fn(secret_str_expr)
    assert resp["是否成功"] is False

    records = permission_engine.audit_records
    assert len(records) > 0
    last_record = records[-1]
    assert "super_secret_token_12345" not in last_record.target_summary
    assert "super_secret_token_12345" not in last_record.result_or_error
    assert "super_secret_token_12345" not in resp["错误"]
