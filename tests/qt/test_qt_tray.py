from unittest.mock import MagicMock

from PySide6.QtWidgets import QWidget

from src.ui_qt.tray import QtTrayManager


def test_unavailable_tray_never_hides_window(qtbot):
    window = QWidget()
    qtbot.addWidget(window)
    tray = QtTrayManager(window, MagicMock(), available_check=lambda: False)
    assert tray.start() is False
    assert tray.can_hide_window() is False


def test_tray_actions_call_single_service_controller(qtbot):
    window = QWidget()
    qtbot.addWidget(window)
    service = MagicMock()
    tray = QtTrayManager(window, service, available_check=lambda: True)
    assert tray.start() is True
    tray.start_action.trigger()
    tray.stop_action.trigger()
    service.start.assert_called_once()
    assert (service.stop_async.call_count == 1 or service.stop.call_count == 1)


def test_tray_unavailable_keeps_window_visible(qtbot):
    """托盘启动返回 False 时，窗口不得被隐藏，can_hide_window 必须始终返回 False。"""
    window = QWidget()
    qtbot.addWidget(window)
    window.show()
    tray = QtTrayManager(window, MagicMock(), available_check=lambda: False)
    assert tray.start() is False
    assert tray.can_hide_window() is False
    assert window.isVisible() is True


def test_tray_stop_safely_disables_availability(qtbot):
    """托盘停止后，is_available() 和 can_hide_window() 必须安全变为 False。"""
    window = QWidget()
    qtbot.addWidget(window)
    tray = QtTrayManager(window, MagicMock(), available_check=lambda: True)
    assert tray.start() is True
    tray.stop()
    assert tray.is_available() is False
    assert tray.can_hide_window() is False
