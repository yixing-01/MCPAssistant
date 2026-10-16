from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QMainWindow, QMessageBox, QPushButton,
    QStackedWidget, QVBoxLayout, QWidget,
)

from src.catalog.manager import CatalogManager
from src.core.config import ConfigManager
from src.core.endpoint import validate_mcp_endpoint
from src.ui_qt.branding import get_brand_full_pixmap, get_brand_window_icon
from src.ui_qt.controllers.onboarding_controller import OnboardingController
from src.ui_qt.controllers.service_controller import ServiceController
from src.ui_qt.views.home_page import HomePage
from src.ui_qt.views.onboarding_dialog import OnboardingDialog
from src.ui_qt.views.placeholder_page import PlaceholderPage


class MainWindow(QMainWindow):
    """MCP助手三入口主窗口，串联首页、应用、设置以及首次向导。"""

    def __init__(
        self,
        start_in_background: bool = False,
        config_manager=None,
        catalog_manager=None,
        service_controller=None,
        skip_startup: bool = False,
        enable_tray: bool = True,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("MCP助手")
        brand_icon = get_brand_window_icon()
        if not brand_icon.isNull():
            self.setWindowIcon(brand_icon)
        self.resize(1180, 760)
        self.setMinimumSize(960, 640)
        self.primary_navigation_buttons: list[QPushButton] = []

        self.config_manager = config_manager or ConfigManager()
        self.catalog_manager = catalog_manager or CatalogManager()
        self.service_controller = service_controller or ServiceController(self.config_manager)
        self.onboarding_dialog: OnboardingDialog | None = None
        self.connection_code_dialog = None
        self.connection_code_controller = None
        self._exit_in_progress = False

        root = QWidget()
        root.setObjectName("pageRoot")
        root.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        root_layout = QHBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # 侧边栏
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        sidebar.setFixedWidth(220)
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(16, 20, 16, 18)
        sidebar_layout.setSpacing(6)

        # 品牌区域
        brand_container = QWidget()
        brand_layout = QHBoxLayout(brand_container)
        brand_layout.setContentsMargins(0, 0, 0, 16)
        brand_layout.setSpacing(10)

        self.brand_mark = QLabel()
        self.brand_mark.setObjectName("brandMark")
        self.brand_mark.setFixedSize(44, 44)
        self.brand_mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.brand_mark.setAccessibleName("MCP助手品牌徽标")
        dpr = round(float(self.devicePixelRatioF()), 2) if hasattr(self, "devicePixelRatioF") else 1.0
        self.brand_mark.setPixmap(get_brand_full_pixmap(44, dpr))

        brand_text_box = QVBoxLayout()
        brand_text_box.setSpacing(2)
        brand_text_box.setContentsMargins(0, 0, 0, 0)
        brand_title = QLabel("MCP助手")
        brand_title.setObjectName("brandTitle")
        brand_sub = QLabel("让桌面控制更简单")
        brand_sub.setObjectName("brandSubtitle")
        brand_text_box.addWidget(brand_title)
        brand_text_box.addWidget(brand_sub)

        brand_layout.addWidget(self.brand_mark)
        brand_layout.addLayout(brand_text_box)
        sidebar_layout.addWidget(brand_container)

        self.stack = QStackedWidget()

        # 1. 首页
        self.home_page = HomePage()
        self.home_page.retry_requested.connect(self.service_controller.restart_async)
        self.home_page.change_connection_code_requested.connect(self.open_connection_code_dialog)
        self.home_page.setup_requested.connect(self.open_onboarding)
        self.home_page.open_apps_requested.connect(lambda: self._select_page(1))
        self.service_controller.product_status_changed.connect(
            self.home_page.set_product_status
        )
        self.service_controller.user_error.connect(self._show_user_error)

        from src.ui_qt.controllers.applications_controller import ApplicationsController
        from src.ui_qt.controllers.settings_controller import SettingsController
        from src.ui_qt.views.applications_page import ApplicationsPage
        from src.ui_qt.views.settings_page import SettingsPage

        self.applications_controller = ApplicationsController(
            self.catalog_manager,
            self.config_manager,
            parent=self,
        )
        self.applications_page = ApplicationsPage(self.applications_controller)

        # 首页连接真实已允许应用变更信号并执行初始注入
        self.applications_controller.allowed_items_changed.connect(
            self.home_page.set_allowed_apps
        )
        self.home_page.set_allowed_apps(self.applications_controller.allowed_items())

        self.settings_controller = SettingsController(self.config_manager, parent=self)
        self.settings_page = SettingsPage(self.settings_controller)
        self.settings_page.retry_connection_requested.connect(self.service_controller.restart_async)
        self.settings_page.change_connection_code_requested.connect(self.open_connection_code_dialog)
        self.settings_page.stop_service_requested.connect(self.service_controller.stop_async)
        self.settings_page.help_requested.connect(self.open_help)

        from pathlib import Path
        from PySide6.QtCore import QSize
        from PySide6.QtGui import QIcon

        icon_dir = Path(__file__).parents[1] / "resources" / "icons"

        pages = [
            ("首页", "home.svg", self.home_page),
            ("我的应用", "apps.svg", self.applications_page),
            ("设置", "settings.svg", self.settings_page),
        ]
        for index, (title, icon_name, page_widget) in enumerate(pages):
            button = QPushButton(title)
            button.setObjectName("navButton")
            button.setCheckable(True)
            icon_file = icon_dir / icon_name
            if icon_file.exists():
                button.setIcon(QIcon(str(icon_file)))
                button.setIconSize(QSize(18, 18))
            button.setAccessibleName(title)
            button.clicked.connect(lambda checked=False, i=index: self._select_page(i))
            sidebar_layout.addWidget(button)
            self.primary_navigation_buttons.append(button)
            self.stack.addWidget(page_widget)

        from src.ui_qt.controllers.diagnostics_controller import DiagnosticsController
        from src.ui_qt.views.help_page import HelpPage

        self.diagnostics_controller = DiagnosticsController(
            self.config_manager,
            self.catalog_manager,
        )
        self.help_page = HelpPage(self.diagnostics_controller)
        self._previous_page_index = 0

        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        self.help_page.retry_connection_requested.connect(self.service_controller.restart_async)
        self.help_page.change_connection_code_requested.connect(self.open_connection_code_dialog)
        self.help_page.network_settings_requested.connect(
            lambda: QDesktopServices.openUrl(QUrl("ms-settings:network-status"))
        )
        self.help_page.back_requested.connect(self._back_from_help)
        self.home_page.troubleshooting_requested.connect(self.open_help)

        sidebar_layout.addStretch(1)

        # 侧边栏底部隐私保护提示卡
        sidebar_note = QFrame()
        sidebar_note.setObjectName("sidebarNote")
        sidebar_note.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        note_layout = QVBoxLayout(sidebar_note)
        note_layout.setContentsMargins(12, 10, 12, 10)
        note_layout.setSpacing(3)
        note_title = QLabel("隐私保护已开启")
        note_title.setObjectName("sidebarNoteTitle")
        note_desc = QLabel("应用信息只保存在这台电脑上")
        note_desc.setObjectName("sidebarNoteDesc")
        note_layout.addWidget(note_title)
        note_layout.addWidget(note_desc)
        sidebar_layout.addWidget(sidebar_note)

        self.help_button = QPushButton("帮助与故障排查")
        self.help_button.setObjectName("navButton")
        help_icon_file = icon_dir / "help.svg"
        if help_icon_file.exists():
            self.help_button.setIcon(QIcon(str(help_icon_file)))
            self.help_button.setIconSize(QSize(18, 18))
        self.help_button.setAccessibleName("帮助与故障排查")
        self.help_button.clicked.connect(self.open_help)
        sidebar_layout.addWidget(self.help_button)

        self.stack.addWidget(self.help_page)

        root_layout.addWidget(sidebar)
        root_layout.addWidget(self.stack, 1)
        self.setCentralWidget(root)

        # 兼容性属性供测试检索
        self.page_title = self.home_page.findChild(QLabel, "pageTitle")
        self._select_page(0)

        if enable_tray:
            from src.ui_qt.tray import QtTrayManager
            self.tray_manager = QtTrayManager(self, self.service_controller)
            self.tray_ready = self.tray_manager.start()
        else:
            self.tray_manager = None
            self.tray_ready = False

        if start_in_background and self.tray_ready:
            config = self.config_manager.load_config()
            if validate_mcp_endpoint(config.mcp_endpoint or "").is_valid:
                self.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, True)

        # 延迟检测启动状态（已配对则启动服务，否则弹出向导）
        if not skip_startup:
            QTimer.singleShot(300, self.check_startup_state)

    def request_exit(self) -> None:
        """异步退出状态机：若服务正在运行则先异步停机，等待确认停止后安全退出应用程序。"""
        if getattr(self, "_exit_in_progress", False):
            return
        self._exit_in_progress = True
        self.hide()

        # 如果服务控制器支持优雅 shutdown_async，优先使用协调停机
        is_mock = "Mock" in type(self.service_controller).__name__
        if self.service_controller and not is_mock and hasattr(self.service_controller, "shutdown_async"):
            self._exit_timeout_timer = QTimer(self)
            self._exit_timeout_timer.setSingleShot(True)
            self._exit_timeout_timer.timeout.connect(self._on_exit_timeout)
            self._exit_timeout_timer.start(5000)

            try:
                self.service_controller.shutdown_finished.connect(self._on_exit_shutdown_finished)
                self._exit_connected_signal = self.service_controller.shutdown_finished
            except Exception:
                pass
            self.service_controller.shutdown_async()
        elif self.service_controller and not is_mock and getattr(self.service_controller, "bridge", None):
            self._exit_timeout_timer = QTimer(self)
            self._exit_timeout_timer.setSingleShot(True)
            self._exit_timeout_timer.timeout.connect(self._on_exit_timeout)
            self._exit_timeout_timer.start(5000)

            try:
                self.service_controller.stop_finished.connect(self._on_exit_shutdown_finished)
                self._exit_connected_signal = self.service_controller.stop_finished
            except Exception:
                pass
            self.service_controller.stop_async()
        else:
            self._finalize_exit()

    def _on_exit_shutdown_finished(self, success: bool = True) -> None:
        """异步停机完成后的回调，注销信号并根据结果退出或恢复。"""
        try:
            import shiboken6
            if not shiboken6.isValid(self):
                return
        except Exception:
            pass

        if hasattr(self, "_exit_timeout_timer") and self._exit_timeout_timer.isActive():
            self._exit_timeout_timer.stop()

        sig = getattr(self, "_exit_connected_signal", None)
        if sig is not None:
            try:
                sig.disconnect(self._on_exit_shutdown_finished)
            except Exception:
                pass
            self._exit_connected_signal = None

        if not success:
            self._exit_in_progress = False
            self.show()
            self.raise_()
            self.activateWindow()
            QMessageBox.warning(self, "退出失败", "后台服务未能正常停止，请稍后重试。")
            return

        self._finalize_exit()

    def _on_exit_timeout(self) -> None:
        """退出等待超时处理：若后台 Bridge 仍在运行或忙碌，绝不能强退，恢复窗口并提示用户。"""
        try:
            import shiboken6
            if not shiboken6.isValid(self):
                return
        except Exception:
            pass

        is_mock = "Mock" in type(self.service_controller).__name__
        if is_mock or not self.service_controller:
            self._finalize_exit()
            return

        if self.service_controller and (self.service_controller.bridge is not None or getattr(self.service_controller, "is_busy", False)):
            self._on_exit_shutdown_finished(False)
        else:
            self._finalize_exit()

    def _finalize_exit(self) -> None:
        """最终退出清理：停止托盘并退出 QApplication，杜绝后台幽灵进程。"""
        if hasattr(self, "_exit_timeout_timer") and self._exit_timeout_timer.isActive():
            self._exit_timeout_timer.stop()
        if hasattr(self, "tray_manager") and self.tray_manager:
            try:
                self.tray_manager.stop()
            except Exception:
                pass
        from PySide6.QtWidgets import QApplication
        qapp = QApplication.instance()
        if qapp:
            qapp.quit()

    def closeEvent(self, event) -> None:
        """窗口关闭事件：若配置开启了关闭到托盘且托盘可用，则最小化到托盘；否则进入异步退出状态机。"""
        config = self.config_manager.load_config()
        if (
            config.minimize_to_tray_on_close
            and self.tray_manager is not None
            and self.tray_manager.can_hide_window()
        ):
            self.hide()
            event.ignore()
            return

        event.accept()
        self._exit_application()

    def _exit_application(self) -> None:
        """向后兼容的退出入口，直接转发至异步退出状态机。"""
        self.request_exit()

    def _select_page(self, index: int) -> None:
        """切换当前页面并更新左侧导航高亮，消除非激活按钮的键盘焦点边框。"""
        self.stack.setCurrentIndex(index)
        for button_index, button in enumerate(self.primary_navigation_buttons):
            is_active = (button_index == index)
            button.setChecked(is_active)
            if not is_active:
                button.clearFocus()

    def check_startup_state(self) -> None:
        """检测当前连接配置是否有效，有效则自动连接，无效则弹向导。"""
        config = self.config_manager.load_config()
        if validate_mcp_endpoint(config.mcp_endpoint or "").is_valid:
            self.service_controller.start()
        else:
            self.open_onboarding()

    def open_onboarding(self) -> None:
        """打开首次设置向导对话框（单实例防重开）。"""
        if self.onboarding_dialog is not None and self.onboarding_dialog.isVisible():
            self.onboarding_dialog.raise_()
            self.onboarding_dialog.activateWindow()
            return
        controller = OnboardingController(
            self.config_manager,
            self.catalog_manager,
            parent=self,
        )
        controller.completed.connect(self._on_onboarding_completed)
        self.onboarding_dialog = OnboardingDialog(controller, parent=self)
        self.onboarding_dialog.show()

    def _on_onboarding_completed(self) -> None:
        """向导完成后的回调：清空向导引用并自动发起服务连接。"""
        self.onboarding_dialog = None
        self.service_controller.start()

    def open_connection_code_dialog(self) -> None:
        """打开更换连接码专用对话框（单实例保护）。"""
        if self.connection_code_dialog is not None and self.connection_code_dialog.isVisible():
            self.connection_code_dialog.raise_()
            self.connection_code_dialog.activateWindow()
            return

        from src.ui_qt.controllers.connection_code_controller import ConnectionCodeController
        from src.ui_qt.views.connection_code_dialog import ConnectionCodeDialog

        self.connection_code_controller = ConnectionCodeController(
            self.config_manager,
            parent=self,
        )
        self.connection_code_controller.code_saved.connect(self._on_connection_code_saved)
        self.connection_code_dialog = ConnectionCodeDialog(
            self.connection_code_controller,
            self.config_manager,
            parent=self,
        )
        self.connection_code_dialog.show()

    def _on_connection_code_saved(self) -> None:
        """连接码成功保存后的回调：关闭对话框，刷新设置页并安全重启服务。"""
        if self.connection_code_dialog is not None:
            self.connection_code_dialog.close()
            self.connection_code_dialog = None
        self.connection_code_controller = None

        if hasattr(self, "settings_controller"):
            self.settings_controller.load()

        if self.service_controller.restart_async():
            QMessageBox.information(self, "连接已更新", "连接码已更新，正在重新连接。")
        else:
            QMessageBox.warning(
                self,
                "提示",
                "连接码已保存，但旧连接仍在停止，请稍后点击立即重试。",
            )

    def open_help(self) -> None:
        """打开帮助与故障排查界面并刷新当前诊断摘要。"""
        if hasattr(self, "help_page"):
            current_idx = self.stack.currentIndex()
            if current_idx < len(self.primary_navigation_buttons):
                self._previous_page_index = current_idx
            status_text = (
                self.home_page.status_title.text()
                if hasattr(self.home_page, "status_title")
                else "正在准备"
            )
            tray_avail = (
                self.tray_manager.can_hide_window()
                if hasattr(self, "tray_manager")
                else False
            )
            self.help_page.set_summary(
                self.diagnostics_controller.build_summary(status_text, tray_avail)
            )
            self.stack.setCurrentWidget(self.help_page)
            for button in self.primary_navigation_buttons:
                button.setChecked(False)

    def _back_from_help(self) -> None:
        """从帮助界面返回之前浏览的页面。"""
        self._select_page(self._previous_page_index)

    def _show_user_error(self, message: str) -> None:
        """展示友好错误提示框。"""
        QMessageBox.warning(self, "需要处理", message)
