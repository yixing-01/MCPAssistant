from unittest.mock import MagicMock

from PySide6.QtCore import Qt

from src.ui_qt.views.onboarding_dialog import OnboardingDialog


def test_wizard_has_three_plain_language_steps(qtbot):
    controller = MagicMock()
    dialog = OnboardingDialog(controller, new_apps=[])
    qtbot.addWidget(dialog)
    assert dialog.step_titles == ["欢迎", "连接 MCP 服务", "选择应用"]
    assert "WebSocket" not in dialog.visible_copy()
    assert "DPAPI" not in dialog.visible_copy()


def test_endpoint_is_masked_and_can_be_revealed(qtbot):
    controller = MagicMock()
    dialog = OnboardingDialog(controller, new_apps=[])
    qtbot.addWidget(dialog)
    dialog.show_step(1)
    assert dialog.endpoint_edit.echoMode().name == "Password"
    qtbot.mouseClick(dialog.reveal_button, Qt.MouseButton.LeftButton)
    assert dialog.endpoint_edit.echoMode().name == "Normal"


def test_only_checked_new_apps_are_completed(qtbot):
    controller = MagicMock()
    apps = [MagicMock(app_id="a", display_name="微信"), MagicMock(app_id="b", display_name="剪映")]
    dialog = OnboardingDialog(controller, new_apps=apps)
    qtbot.addWidget(dialog)
    dialog.show_step(2)
    dialog.app_checks["a"].setChecked(True)
    dialog.app_checks["b"].setChecked(False)
    qtbot.mouseClick(dialog.next_button, Qt.MouseButton.LeftButton)
    controller.complete.assert_called_once_with(["a"])


def test_new_apps_checkboxes_default_unchecked(qtbot):
    """新发现的软件在向导列表中必须默认不勾选。"""
    controller = MagicMock()
    apps = [MagicMock(app_id="a", display_name="微信"), MagicMock(app_id="b", display_name="剪映")]
    dialog = OnboardingDialog(controller, new_apps=apps)
    qtbot.addWidget(dialog)
    dialog.show_step(2)
    assert dialog.app_checks["a"].isChecked() is False
    assert dialog.app_checks["b"].isChecked() is False
