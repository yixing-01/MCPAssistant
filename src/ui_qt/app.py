from __future__ import annotations

import sys
from typing import Sequence

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from src.ui_qt.branding import get_brand_window_icon, set_windows_app_user_model_id
from src.ui_qt.dpi import enable_per_monitor_v2
from src.ui_qt.theme import apply_theme


def configure_application(app: QApplication) -> None:
    """配置应用程序的基础名称、组织、默认字体与全局视觉主题。"""
    app.setApplicationName("MCP助手")
    app.setOrganizationName("MCPAssistant")
    brand_icon = get_brand_window_icon()
    if not brand_icon.isNull():
        app.setWindowIcon(brand_icon)
    apply_theme(app)


def create_application(argv: Sequence[str]) -> QApplication:
    """创建并配置具备 DPI 感知能力与统一品牌标识的 QApplication。"""
    set_windows_app_user_model_id("MCPAssistant.Desktop")
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    enable_per_monitor_v2()
    app = QApplication(list(argv))
    configure_application(app)
    return app


def run_qt(argv: Sequence[str] | None = None, start_in_background: bool = False) -> int:
    """运行 PySide6 Qt 图形界面入口。"""
    from src.ui_qt.theme import apply_theme
    from src.ui_qt.views.main_window import MainWindow

    app = create_application(argv or sys.argv)
    app.setQuitOnLastWindowClosed(False)
    apply_theme(app)
    window = MainWindow(start_in_background=start_in_background)
    if not start_in_background:
        window.show()
    return app.exec()


def run_qt_smoke(argv: Sequence[str] | None = None) -> int:
    """运行轻量级 Qt 图形界面烟测，不触发后台连接与托盘常驻，250ms 后正常退出。"""
    from PySide6.QtCore import QTimer
    from src.ui_qt.theme import apply_theme
    from src.ui_qt.views.main_window import MainWindow

    app = create_application(argv or sys.argv)
    app.setQuitOnLastWindowClosed(True)
    apply_theme(app)
    window = MainWindow(skip_startup=True, enable_tray=False)
    window.show()
    QTimer.singleShot(250, app.quit)
    return app.exec()

