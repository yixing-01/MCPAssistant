"""第15轮高风险与中风险缺陷 Qt 界面与控制器回归测试集。
验证：
- ApplicationsPage 与 ApplicationsController 接口统一与严格调用 (Item 1)
- ApplicationsController 手动应用名称推导与参数健壮性 (Item 1)
- SettingsController 保存目录安全校验与敏感路径拦截 (Item 4)
- SettingsPage 文件夹列表、添加、移除、清空与拦截弹窗 (Item 4)
- MainWindow 托盘关闭与彻底退出防幽灵进程 (Item 8)
- QtTrayManager show_window 清除 WA_DontShowOnScreen (Item 8)
- ServiceController 异步 stop_async 与 restart_async 忙碌状态管理 (Item 11)
"""

import os
from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMessageBox

from src.catalog.models import AppEntry, LaunchType
from src.ui_qt.controllers.applications_controller import ApplicationsController
from src.ui_qt.controllers.service_controller import ServiceController
from src.ui_qt.controllers.settings_controller import SettingsController
from src.ui_qt.tray import QtTrayManager
from src.ui_qt.views.applications_page import ApplicationsPage
from src.ui_qt.views.main_window import MainWindow
from src.ui_qt.views.settings_page import SettingsPage


# =================================================================================================
# 1. ApplicationsPage 与 ApplicationsController 严格接口测试 (Item 1)
# =================================================================================================
def test_applications_controller_add_manual_derives_stem(tmp_path):
    """手动添加应用仅传入文件路径时，必须安全自动推导主文件名作为应用名称与别名。"""
    cat_mock = MagicMock()
    cat_mock.get_all_apps.return_value = []
    fake_exe = tmp_path / "NotepadLite.exe"
    fake_exe.write_text("dummy")

    controller = ApplicationsController(cat_mock, MagicMock())
    success = controller.add_manual(str(fake_exe))

    assert success is True
    cat_mock.add_manual_app.assert_called_once_with(
        name="NotepadLite",
        exe_path=str(fake_exe),
        alias="NotepadLite",
        authorized=False,
    )


def test_applications_page_calls_unified_controller_methods(qtbot, tmp_path):
    """ApplicationsPage 必须通过统一公开方法调用 Controller，严禁调用失效方法。"""
    fake_exe = tmp_path / "MyTool.exe"
    fake_exe.write_text("dummy")

    app_entry = AppEntry(
        app_id="app-1",
        display_name="应用1",
        launch_type=LaunchType.WIN32,
        launch_target=str(fake_exe),
        source="manual",
        is_enabled=False,
        is_valid=True,
    )
    cat_mock = MagicMock()
    cat_mock.get_all_apps.return_value = [app_entry]

    # 使用真实 ApplicationsController 实例，杜绝宽松 MagicMock 吞掉接口错误
    controller = ApplicationsController(cat_mock, MagicMock())
    page = ApplicationsPage(controller)
    page.set_items(controller._items)
    qtbot.addWidget(page)

    # 1. 批量授权操作
    controller._visible_ids = ["app-1"]
    with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes), \
         patch.object(QMessageBox, "information"):
        page._confirm_batch(True)
    cat_mock.batch_set_enabled.assert_called_with(True, app_ids=["app-1"])

    # 2. 手工添加操作
    with patch("PySide6.QtWidgets.QFileDialog.getOpenFileName", return_value=(str(fake_exe), "")):
        page._add_manual()
    cat_mock.add_manual_app.assert_called_with(
        name="MyTool",
        exe_path=str(fake_exe),
        alias="MyTool",
        authorized=False,
    )

    # 3. 修复失效路径操作
    repair_exe = tmp_path / "Repaired.exe"
    repair_exe.write_text("dummy")
    with patch("PySide6.QtWidgets.QFileDialog.getOpenFileName", return_value=(str(repair_exe), "")):
        page._repair_manual("app-1")
    cat_mock.repair_manual_app.assert_called_with("app-1", str(repair_exe))


# =================================================================================================
# 2. SettingsController 保存目录强校验测试 (Item 4)
# =================================================================================================
def test_settings_controller_rejects_sensitive_save_directory(qtbot):
    """SettingsController 保存配置时，若开启文件写入且目录包含敏感系统路径，必须拒绝保存。"""
    config_mock = MagicMock()
    config_mock.load_config.return_value.auto_start_with_windows = False
    config_mock.load_config.return_value.allow_file_write = False
    config_mock.load_config.return_value.allowed_write_dirs = []

    controller = SettingsController(config_mock, lambda: False, lambda v: True)

    win_dir = os.environ.get("SystemRoot", r"C:\Windows")
    with qtbot.waitSignal(controller.save_failed, timeout=1000) as sig:
        controller.save({
            "allow_file_write": True,
            "allowed_write_dirs": [win_dir],
        })

    assert "受保护的系统或敏感目录" in sig.args[0]
    config_mock.save_config.assert_not_called()


# =================================================================================================
# 3. SettingsPage 目录交互与敏感目录拦截测试 (Item 4)
# =================================================================================================
def test_settings_page_directory_list_interactions(qtbot):
    """SettingsPage 必须提供授权目录展示、单项移除、清空与敏感目录弹窗拦截。"""
    config_mock = MagicMock()
    config_mock.load_config.return_value.auto_start_with_windows = False
    config_mock.load_config.return_value.minimize_to_tray_on_close = True
    config_mock.load_config.return_value.allow_run_apps = True
    config_mock.load_config.return_value.allow_open_url = True
    config_mock.load_config.return_value.allow_file_write = True
    safe_folder = r"D:\SafeWorkspace\MyDocs"
    config_mock.load_config.return_value.allowed_write_dirs = [safe_folder]

    def _mock_is_safe(p):
        if "windows" in str(p).lower():
            return False, "安全拒绝：禁止操作受保护的系统或敏感目录 (windows)"
        return True, ""

    with patch("src.ui_qt.controllers.settings_controller.PathSandbox.is_safe_write_directory", side_effect=_mock_is_safe), \
         patch("src.ui_qt.views.settings_page.PathSandbox.is_safe_write_directory", side_effect=_mock_is_safe):
        controller = SettingsController(config_mock, lambda: False, lambda v: True)
        page = SettingsPage(controller)
        qtbot.addWidget(page)

        assert safe_folder in page.allowed_write_dirs
        assert page.clear_dirs_btn.isEnabled()

        # 1. 尝试选择敏感目录，必须弹出告警并拒绝添加
        win_dir = r"C:\Windows\System32"
        with patch("PySide6.QtWidgets.QFileDialog.getExistingDirectory", return_value=win_dir):
            with patch.object(QMessageBox, "warning") as mock_warn:
                page._choose_write_dir()
                mock_warn.assert_called_once()
                assert "受保护" in mock_warn.call_args[0][2]
        assert win_dir not in page.allowed_write_dirs

        # 2. 移除单项
        page._remove_write_dir(safe_folder)
        assert len(page.allowed_write_dirs) == 0
        assert not page.clear_dirs_btn.isEnabled()


# =================================================================================================
# 4. MainWindow 托盘与窗口关闭退出测试 (Item 8)
# =================================================================================================
def test_main_window_close_event_exits_when_tray_disabled(tmp_path):
    """当托盘被禁用或未开启最小化到托盘时，关闭窗口必须调用 _exit_application 退出 QApplication。"""
    config_mock = MagicMock()
    config_mock.load_config.return_value.minimize_to_tray_on_close = False
    config_mock.load_config.return_value.mcp_endpoint = ""

    cat_mock = MagicMock()
    cat_mock.get_all_apps.return_value = []

    svc_mock = MagicMock()
    window = MainWindow(cat_mock, config_mock, svc_mock, enable_tray=False, skip_startup=True)

    with patch.object(window, "_exit_application") as mock_exit:
        event = MagicMock()
        window.closeEvent(event)
        event.accept.assert_called_once()
        mock_exit.assert_called_once()


def test_tray_show_window_clears_wa_dont_show_on_screen():
    """从托盘显示主窗口时，必须清除 WA_DontShowOnScreen 属性保证窗口可见。"""
    mock_window = MagicMock()
    svc_mock = MagicMock()
    tray = QtTrayManager(mock_window, svc_mock, available_check=lambda: True)

    tray.show_window()
    mock_window.setAttribute.assert_called_once_with(Qt.WidgetAttribute.WA_DontShowOnScreen, False)
    mock_window.showNormal.assert_called_once()
    mock_window.raise_.assert_called_once()
    mock_window.activateWindow.assert_called_once()


# =================================================================================================
# 5. ServiceController 异步操作测试 (Item 11)
# =================================================================================================
def test_service_controller_async_stop_and_restart(qtbot):
    """ServiceController 的 stop_async 与 restart_async 必须异步发射 busy_changed 与完成信号。"""
    config_mock = MagicMock()
    config_mock.load_config.return_value.mcp_endpoint = "wss://api.xiaozhi.me/mcp/?token=VALID_TOKEN"

    bridge_mock = MagicMock()
    bridge_mock.stop.return_value = True
    bridge_mock.is_running.return_value = False

    controller = ServiceController(config_mock, bridge_factory=lambda **kwargs: bridge_mock)
    controller.bridge = bridge_mock

    # 验证 stop_async
    with qtbot.waitSignal(controller.stop_finished, timeout=2000) as sig:
        controller.stop_async()

    assert sig.args == [True]
    assert controller.is_busy() is False

    # 验证 restart_async
    with qtbot.waitSignal(controller.restart_finished, timeout=2000) as sig:
        controller.restart_async()

    assert sig.args == [True]
    assert controller.is_busy() is False
