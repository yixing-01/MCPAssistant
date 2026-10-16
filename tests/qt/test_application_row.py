from PySide6.QtCore import Qt

from src.ui_qt.presentation.apps import AppBucket, AppListItem
from src.ui_qt.widgets.application_row import ApplicationRow


def test_row_emits_direct_toggle(qtbot):
    item = AppListItem("id", "微信", ("微信",), "聊天与通讯", "", True, AppBucket.ALLOWED, "可以说：打开微信")
    row = ApplicationRow(item)
    qtbot.addWidget(row)
    row.show()
    with qtbot.waitSignal(row.enabled_changed, timeout=1000) as signal:
        qtbot.mouseClick(row.enabled_toggle, Qt.MouseButton.LeftButton)
    assert signal.args == ["id", False]
