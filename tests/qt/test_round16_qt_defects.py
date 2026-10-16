"""第十六轮 Qt 界面异步停止/重启与安全退出回归测试 (Item 1)

验证：
1. 首页、设置页、帮助页重试信号统一接入 restart_async()
2. 设置页停止与托盘停止统一接入 stop_async()
3. 更换连接码后调用 restart_async()
4. 窗口关闭与托盘退出进入异步退出状态机，防止重复调用且不阻塞主线程
5. ServiceController.stop_async() 与 restart_async() 保持幂等
"""

import threading
import time
from unittest.mock import MagicMock, patch
import pytest
from PySide6.QtCore import QObject, Signal, Qt, QTimer
from PySide6.QtWidgets import QApplication

from src.core.config import AppConfig, ConfigManager
from src.ui_qt.controllers.service_controller import ServiceController
from src.ui_qt.tray import QtTrayManager
from src.ui_qt.views.main_window import MainWindow


class DummyDelayedBridge:
    """带可控延迟的测试用 Bridge 替身，模拟真实子进程清理耗时。"""

    def __init__(self, stop_delay: float = 0.5):
        self.stop_delay = stop_delay
        self.stop_called = 0
        self.is_running_val = True

    def start(self):
        self.is_running_val = True

    def stop(self) -> bool:
        self.stop_called += 1
        time.sleep(self.stop_delay)
        self.is_running_val = False
        return True

    def is_running(self) -> bool:
        return self.is_running_val


def test_ui_signals_connected_to_async_methods(qtbot):
    """首页、设置页、帮助页、托盘菜单的重试与停止信号必须连接到 async 方法，不得连接同步阻塞方法。"""
    mock_config = MagicMock()
    mock_config.load_config.return_value = AppConfig()
    mock_catalog = MagicMock()
    mock_catalog.apps = {}

    service_controller = ServiceController(mock_config, parent=None)
    window = MainWindow(
        config_manager=mock_config,
        catalog_manager=mock_catalog,
        service_controller=service_controller,
        skip_startup=True,
        enable_tray=True,
    )
    qtbot.addWidget(window)

    # 验证真实信号槽绑定不指向同步阻塞的 restart 或 stop
    with patch.object(service_controller, "restart") as mock_sync_restart, \
         patch.object(service_controller, "stop") as mock_sync_stop, \
         patch.object(service_controller, "restart_async") as mock_async_restart, \
         patch.object(service_controller, "stop_async") as mock_async_stop:

        # 1. 首页重试
        window.home_page.retry_requested.emit()
        assert mock_sync_restart.call_count == 0
        assert mock_async_restart.call_count == 1

        # 2. 设置页重试
        window.settings_page.retry_connection_requested.emit()
        assert mock_sync_restart.call_count == 0
        assert mock_async_restart.call_count == 2

        # 3. 设置页停止
        window.settings_page.stop_service_requested.emit()
        assert mock_sync_stop.call_count == 0
        assert mock_async_stop.call_count == 1

        # 4. 帮助页重试
        window.help_page.retry_connection_requested.emit()
        assert mock_sync_restart.call_count == 0
        assert mock_async_restart.call_count == 3

        # 5. 托盘停止
        if window.tray_manager:
            window.tray_manager.stop_action.trigger()
            assert mock_sync_stop.call_count == 0
            assert mock_async_stop.call_count == 2


def test_connection_code_saved_uses_restart_async(qtbot):
    """更新连接码后保存必须调用 restart_async()，给出非阻塞反馈。"""
    mock_config = MagicMock()
    mock_config.load_config.return_value = AppConfig()
    mock_catalog = MagicMock()
    mock_catalog.apps = {}

    service_controller = ServiceController(mock_config, parent=None)
    window = MainWindow(
        config_manager=mock_config,
        catalog_manager=mock_catalog,
        service_controller=service_controller,
        skip_startup=True,
        enable_tray=False,
    )
    qtbot.addWidget(window)

    with patch.object(service_controller, "restart_async", return_value=True) as mock_async_restart, \
         patch.object(service_controller, "restart") as mock_sync_restart, \
         patch("PySide6.QtWidgets.QMessageBox.information") as mock_info:

        window._on_connection_code_saved()
        assert mock_sync_restart.call_count == 0
        assert mock_async_restart.call_count == 1
        mock_info.assert_called_once()


def test_service_controller_async_idempotent(qtbot):
    """ServiceController.stop_async() 与 restart_async() 在 busy 期间必须保持幂等，不得创建重复 worker。"""
    mock_config = MagicMock()
    mock_config.load_config.return_value = AppConfig()

    controller = ServiceController(mock_config)
    delayed_bridge = DummyDelayedBridge(stop_delay=0.3)
    controller.bridge = delayed_bridge

    busy_states = []
    controller.busy_changed.connect(busy_states.append)

    # 第一次触发 stop_async
    controller.stop_async()
    assert controller.is_busy == True
    assert controller.is_busy() is True

    # 忙碌中重复触发 stop_async 与 restart_async
    controller.stop_async()
    controller.restart_async()

    # 等待完成
    with qtbot.waitSignal(controller.stop_finished, timeout=2000):
        pass

    assert delayed_bridge.stop_called == 1
    assert controller.is_busy == False
    assert controller.is_busy() is False
    # busy 状态应成对出现 [True, False]
    assert busy_states == [True, False]


def test_window_close_async_exit_state_machine(qtbot):
    """关闭窗口触发退出时，必须启动异步退出状态机：先 stop_async，stop_finished 后再 quit。"""
    mock_config = MagicMock()
    mock_config.load_config.return_value = AppConfig(minimize_to_tray_on_close=False)
    mock_catalog = MagicMock()
    mock_catalog.apps = {}

    controller = ServiceController(mock_config)
    delayed_bridge = DummyDelayedBridge(stop_delay=0.2)
    controller.bridge = delayed_bridge

    window = MainWindow(
        config_manager=mock_config,
        catalog_manager=mock_catalog,
        service_controller=controller,
        skip_startup=True,
        enable_tray=False,
    )
    qtbot.addWidget(window)

    quit_called = []
    with patch.object(QApplication, "quit", side_effect=lambda: quit_called.append(True)):
        # 触发关闭事件
        from PySide6.QtGui import QCloseEvent
        event = QCloseEvent()
        window.closeEvent(event)

        # 此时应处于退出中状态
        assert getattr(window, "_exit_in_progress", False) is True
        # 在 stop 尚未完成前，quit 绝不能被调用
        assert len(quit_called) == 0

        # 等待后台停止完成
        with qtbot.waitSignal(controller.stop_finished, timeout=2000):
            pass

        # 此时 stop 已完成，QApplication.quit 必须已被调用
        assert len(quit_called) == 1
