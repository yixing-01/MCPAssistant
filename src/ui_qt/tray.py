from __future__ import annotations

from collections.abc import Callable

from PySide6.QtGui import QAction, QColor, QIcon, QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon


class QtTrayManager:
    """管理系统托盘图标、菜单及窗口隐藏/恢复生命周期。"""

    def __init__(
        self,
        window,
        service_controller,
        available_check: Callable[[], bool] | None = None,
    ):
        self.window = window
        self.service_controller = service_controller
        self.available_check = available_check or QSystemTrayIcon.isSystemTrayAvailable
        self.icon: QSystemTrayIcon | None = None
        from PySide6.QtCore import QObject
        action_parent = window if isinstance(window, QObject) else None
        self.start_action = QAction("启动/重新连接", action_parent)
        self.stop_action = QAction("停止服务", action_parent)
        self.show_action = QAction("显示主窗口", action_parent)
        self.exit_action = QAction("退出程序", action_parent)

        self.start_action.triggered.connect(service_controller.start)
        self.stop_action.triggered.connect(service_controller.stop_async)
        self.show_action.triggered.connect(self.show_window)
        self.exit_action.triggered.connect(self.exit_app)
        if hasattr(service_controller, "busy_changed"):
            service_controller.busy_changed.connect(self._on_busy_changed)

    def _on_busy_changed(self, is_busy: bool) -> None:
        """根据后台服务的忙碌状态动态禁用/恢复托盘动作，防止重复并发点击。"""
        self.start_action.setEnabled(not is_busy)
        self.stop_action.setEnabled(not is_busy)

    def start(self) -> bool:
        """初始化并显示系统托盘图标；若系统不支持托盘则返回 False。"""
        if not self.available_check():
            return False
        from PySide6.QtCore import QObject
        from src.ui_qt.branding import get_brand_tray_icon
        tray_parent = self.window if isinstance(self.window, QObject) else None
        tray_icon = get_brand_tray_icon()
        if tray_icon and not tray_icon.isNull():
            self.icon = QSystemTrayIcon(tray_icon, tray_parent)
        else:
            pixmap = QPixmap(32, 32)
            pixmap.fill(QColor("#000000"))
            self.icon = QSystemTrayIcon(QIcon(pixmap), tray_parent)

        menu = QMenu()
        menu.addAction(self.show_action)
        menu.addSeparator()
        menu.addAction(self.start_action)
        menu.addAction(self.stop_action)
        menu.addSeparator()
        menu.addAction(self.exit_action)
        self.icon.setContextMenu(menu)
        self.icon.setToolTip("MCP助手")
        self.icon.activated.connect(
            lambda reason: self.show_window()
            if reason == QSystemTrayIcon.ActivationReason.Trigger
            else None
        )
        self.icon.show()
        return self.icon.isVisible()

    def can_hide_window(self) -> bool:
        """检查托盘是否就绪且可见，作为关闭时最小化到托盘的前提条件。"""
        return bool(self.icon and self.icon.isVisible())

    def is_available(self) -> bool:
        """检查托盘是否当前处于可用且已显示状态。"""
        return self.can_hide_window()

    def stop(self) -> None:
        """隐藏并清理托盘图标。"""
        if self.icon:
            self.icon.hide()
            self.icon = None

    def show_window(self) -> None:
        """从托盘恢复并置顶主窗口。"""
        if hasattr(self.window, "setAttribute"):
            from PySide6.QtCore import Qt
            self.window.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen, False)
        self.window.showNormal()
        self.window.raise_()
        self.window.activateWindow()

    def exit_app(self) -> None:
        """干净停止服务并退出整个应用程序（优先转发至主窗口异步退出状态机）。"""
        if hasattr(self.window, "request_exit"):
            self.window.request_exit()
            return
        if hasattr(self.service_controller, "shutdown_async"):
            try:
                self.service_controller.shutdown_finished.connect(lambda ok: self._finalize_exit() if ok else None)
                self.service_controller.shutdown_async()
                return
            except Exception:
                pass
        elif hasattr(self.service_controller, "stop_async"):
            try:
                self.service_controller.stop_finished.connect(lambda ok: self._finalize_exit())
                self.service_controller.stop_async()
                return
            except Exception:
                pass
        self._finalize_exit()

    def _finalize_exit(self) -> None:
        """托盘图标清理与进程彻底退出。"""
        if self.icon:
            self.icon.hide()
            self.icon = None
        qapp = QApplication.instance()
        if qapp:
            qapp.quit()
