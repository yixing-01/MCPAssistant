"""脱敏诊断控制器测试用例。

验证诊断摘要绝不包含连接码、Endpoint 或 Token，导出的日志经过脱敏过滤。
"""

from pathlib import Path
from unittest.mock import MagicMock

from src.core.config import AppConfig
from src.ui_qt.controllers.diagnostics_controller import DiagnosticsController


def test_summary_never_contains_endpoint_or_token(tmp_path):
    """诊断摘要无论在何种状态下，严禁输出 Token、完整 URL 或 wss:// 地址。"""
    config = MagicMock()
    config.last_load_error = None
    config.load_config.return_value = AppConfig(
        mcp_endpoint="wss://api.xiaozhi.me/mcp/?token=SECRET_TOKEN"
    )
    catalog = MagicMock()
    catalog.get_all_apps.return_value = []
    controller = DiagnosticsController(config, catalog, log_dir=tmp_path)
    text = controller.build_summary("正在恢复", tray_available=True)
    assert "SECRET_TOKEN" not in text
    assert "wss://" not in text
    assert "当前状态：正在恢复" in text
    assert "系统托盘：可用" in text


def test_export_sanitizes_log_lines(tmp_path):
    """导出日志时必须对日志行进行深度安全脱敏，屏蔽 Token 等敏感信息。"""
    source = tmp_path / "app.log"
    source.write_text("连接 token=SECRET_TOKEN\n正常信息\n", encoding="utf-8")
    destination = tmp_path / "export.txt"
    controller = DiagnosticsController(MagicMock(), MagicMock(), log_dir=tmp_path)
    controller.export_logs(destination)
    exported = destination.read_text(encoding="utf-8")
    assert "SECRET_TOKEN" not in exported
    assert "正常信息" in exported
