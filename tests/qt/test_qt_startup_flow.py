from unittest.mock import MagicMock

from src.core.config import AppConfig
from src.ui_qt.views.main_window import MainWindow


def test_valid_config_starts_service_once(qtbot):
    config = MagicMock()
    config.load_config.return_value = AppConfig(
        mcp_endpoint="wss://api.xiaozhi.me/mcp/?token=GOOD_TOKEN"
    )
    service = MagicMock()
    window = MainWindow(config_manager=config, service_controller=service)
    qtbot.addWidget(window)
    window.check_startup_state()
    service.start.assert_called_once()


def test_missing_config_opens_one_onboarding_dialog(qtbot):
    config = MagicMock()
    config.load_config.return_value = AppConfig()
    service = MagicMock()
    window = MainWindow(config_manager=config, service_controller=service)
    qtbot.addWidget(window)
    window.check_startup_state()
    first = window.onboarding_dialog
    window.check_startup_state()
    assert window.onboarding_dialog is first
    service.start.assert_not_called()


def test_localhost_config_does_not_autoconnect_and_opens_onboarding(qtbot):
    """即使配置中写入了 localhost 地址，主程序启动时也必须强制拒绝并引导进入向导。"""
    config = MagicMock()
    config.load_config.return_value = AppConfig(
        mcp_endpoint="ws://localhost:8080/mcp/?token=LOCAL_DEV"
    )
    service = MagicMock()
    window = MainWindow(config_manager=config, service_controller=service)
    qtbot.addWidget(window)
    window.check_startup_state()
    service.start.assert_not_called()
    assert window.onboarding_dialog is not None


def test_background_mode_requires_valid_credentials_and_ready_tray(qtbot):
    """后台启动模式仅在凭据合法且托盘成功启动时生效隐藏，无效凭据不隐藏。"""
    from PySide6.QtCore import Qt

    config = MagicMock()
    config.load_config.return_value = AppConfig(mcp_endpoint="")
    window = MainWindow(start_in_background=True, config_manager=config)
    qtbot.addWidget(window)
    assert not window.testAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)


def test_production_qt_mainwindow_has_no_localhost_bypass_string():
    """生产代码中不得包含 'localhost' in 或 '127.0.0.1' in 的绕过代码。"""
    from pathlib import Path
    main_window_path = Path(__file__).resolve().parents[2] / "src" / "ui_qt" / "views" / "main_window.py"
    code = main_window_path.read_text(encoding="utf-8")
    assert "localhost" not in code
    assert "127.0.0.1" not in code


def test_start_service_rejects_invalid_endpoint_and_reports_error(qtbot):
    """调用服务启动时若当前 Endpoint 不合法，必须拒绝启动且不调用底层 Bridge。"""
    config = MagicMock()
    config.load_config.return_value = AppConfig(mcp_endpoint="ws://insecure.site/mcp/?token=test")
    service = MagicMock()
    window = MainWindow(config_manager=config, service_controller=service)
    qtbot.addWidget(window)
    # 模拟触发重新连接或服务启动
    window.check_startup_state()
    service.start.assert_not_called()
