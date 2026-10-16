"""第十八轮 Qt 界面缺陷测试：列表行生命周期、行复用、顶层小窗防御与待处理开关禁用。"""

from unittest.mock import MagicMock, patch
import pytest

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QMouseEvent, QPixmap
from PySide6.QtWidgets import QApplication, QMessageBox

from src.catalog.models import AppEntry, LaunchType
from src.ui_qt.controllers.applications_controller import ApplicationsController
from src.ui_qt.icon_provider import AppIconProvider
from src.ui_qt.presentation.apps import AppBucket, AppListItem, build_app_items
from src.ui_qt.views.applications_page import ApplicationsPage
from src.ui_qt.widgets.application_row import ApplicationRow


def _make_item(app_id: str, name: str, bucket: AppBucket = AppBucket.ALLOWED, is_enabled: bool = True, can_toggle: bool = True) -> AppListItem:
    return AppListItem(
        app_id=app_id,
        display_name=name,
        aliases=(name,),
        category="电脑应用",
        icon_ref=f"C:/fake/{app_id}.exe",
        is_enabled=is_enabled,
        bucket=bucket,
        voice_example=f"可以说：打开{name}",
        repair_message="请先选择主程序。" if bucket is AppBucket.NEEDS_ATTENTION else "",
        repair_action_label="选择主程序" if bucket is AppBucket.NEEDS_ATTENTION else "",
        can_toggle=can_toggle,
    )


# =================================================================================================
# 1. 列表生命周期、行复用与父容器测试
# =================================================================================================

def test_application_row_parent_is_list_host_and_never_toplevel(qtbot):
    """验证所有 ApplicationRow 的 parent 均为 list_host，且绝对不出现在的 topLevelWidgets 中。"""
    cat_mock = MagicMock()
    controller = ApplicationsController(cat_mock, MagicMock())
    page = ApplicationsPage(controller)
    qtbot.addWidget(page)

    items = [
        _make_item("app-1", "应用一"),
        _make_item("app-2", "应用二"),
    ]
    page.set_items(items)

    assert len(page.rows) == 2
    for row in page.rows:
        # 父容器必须是 list_host
        assert row.parent() is page.list_host
        assert not row.isWindow()

    top_levels = QApplication.topLevelWidgets()
    row_top_levels = [w for w in top_levels if isinstance(w, ApplicationRow)]
    assert row_top_levels == [], "QApplication.topLevelWidgets 中绝不能出现 ApplicationRow"


def test_identical_items_reused_without_recreation(qtbot):
    """连续两次 publish 相同数据，未变化的 app_id 对应的行对象身份保持不变。"""
    cat_mock = MagicMock()
    controller = ApplicationsController(cat_mock, MagicMock())
    page = ApplicationsPage(controller)
    qtbot.addWidget(page)

    items_v1 = [
        _make_item("app-1", "应用一"),
        _make_item("app-2", "应用二"),
    ]
    page.set_items(items_v1)
    row1_v1 = page.rows[0]
    row2_v1 = page.rows[1]

    # 再次传入相同内容
    items_v2 = [
        _make_item("app-1", "应用一"),
        _make_item("app-2", "应用二"),
    ]
    page.set_items(items_v2)

    assert len(page.rows) == 2
    assert page.rows[0] is row1_v1, "未变化的条目必须复用已有 ApplicationRow 对象"
    assert page.rows[1] is row2_v1, "未变化的条目必须复用已有 ApplicationRow 对象"


def test_incremental_add_remove_and_update(qtbot):
    """增量变更时：仅更新保留行，仅新建新增行，仅删除消失行，不重建整个列表。"""
    cat_mock = MagicMock()
    controller = ApplicationsController(cat_mock, MagicMock())
    page = ApplicationsPage(controller)
    qtbot.addWidget(page)

    items_v1 = [
        _make_item("app-1", "应用一"),
        _make_item("app-2", "应用二"),
    ]
    page.set_items(items_v1)
    row1 = page.rows[0]
    row2 = page.rows[1]

    # 变更：删除 app-2，更新 app-1 状态，新增 app-3
    items_v2 = [
        _make_item("app-1", "应用一修改后", is_enabled=False),
        _make_item("app-3", "应用三"),
    ]
    page.set_items(items_v2)

    assert len(page.rows) == 2
    assert page.rows[0] is row1
    assert page.rows[0].item.display_name == "应用一修改后"
    assert page.rows[0].enabled_toggle.isChecked() is False
    assert page.rows[1] is not row2
    assert page.rows[1].item.app_id == "app-3"
    assert page.rows[1].parent() is page.list_host


def test_rapid_tab_switching_stress_30_times(qtbot):
    """快速切换三个分组标签至少 30 次：不产生额外顶层窗口、不弹窗、不抛 RuntimeError。"""
    cat_mock = MagicMock()
    entry_allowed = AppEntry(
        app_id="app-allowed",
        display_name="已允许应用",
        exe_path=r"C:\fake\a.exe",
        launch_target=r"C:\fake\a.exe",
        launch_type=LaunchType.WIN32,
        source="StartMenu",
        is_enabled=True,
        is_valid=True,
    )
    entry_not_allowed = AppEntry(
        app_id="app-not",
        display_name="未允许应用",
        exe_path=r"C:\fake\b.exe",
        launch_target=r"C:\fake\b.exe",
        launch_type=LaunchType.WIN32,
        source="StartMenu",
        is_enabled=False,
        is_valid=True,
    )
    entry_needs_att = AppEntry(
        app_id="app-att",
        display_name="待处理应用",
        exe_path=r"C:\fake\c.exe",
        launch_target=r"C:\fake\c.exe",
        launch_type=LaunchType.PORTABLE,
        source="Manual",
        is_enabled=False,
        is_valid=False,
        is_user_managed=True,
    )
    cat_mock.get_all_apps.return_value = [entry_allowed, entry_not_allowed, entry_needs_att]

    controller = ApplicationsController(cat_mock, MagicMock())
    page = ApplicationsPage(controller)
    qtbot.addWidget(page)

    with patch.object(QMessageBox, "warning") as mock_warn:
        buckets = [AppBucket.ALLOWED, AppBucket.NOT_ALLOWED, AppBucket.NEEDS_ATTENTION]
        for i in range(30):
            page._select_bucket(buckets[i % 3])
            # 确认每次切换后均无顶层浮层小窗口
            top_levels = [w for w in QApplication.topLevelWidgets() if isinstance(w, ApplicationRow)]
            assert top_levels == []

        mock_warn.assert_not_called()


def test_delayed_icon_result_does_not_revive_or_update_destroyed_row(qtbot):
    """已删除行的弱引用回调在图标结果延迟返回时静默丢弃，绝不复活或更新失效控件。"""
    provider = AppIconProvider.instance()
    provider.clear_cache()

    captured_cb = []

    def mock_load_icon_async(icon_ref, launch_type, size, callback, dpr=1.0):
        captured_cb.append(callback)

    with patch.object(provider, "load_icon_async", side_effect=mock_load_icon_async):
        item = _make_item("app-del", "即将删除的应用")
        row = ApplicationRow(item)
        qtbot.addWidget(row)
        assert len(captured_cb) == 1
        cb = captured_cb[0]

        import weakref
        wref = weakref.ref(row)

        # 销毁控件
        row.deleteLater()
        QApplication.sendPostedEvents(None, 0)
        del row

        # 构造测试位图并真实触发延迟回调
        dummy_pixmap = QPixmap(36, 36)
        dummy_pixmap.fill(Qt.GlobalColor.green)

        # 真实触发回调：弱引用拦截，不应抛出异常或死机
        cb(dummy_pixmap)
        assert wref() is None or not wref().isVisible()



def test_pending_icon_requests_empty_after_completion():
    """验证 pending 图标请求完成后计数回到 0。"""
    provider = AppIconProvider.instance()
    provider.clear_cache()

    # 初始 pending 为 0
    stats = provider.get_cache_stats()
    assert stats["pending"] == 0

    # 加载空路径直接触发回调并保持 pending 为 0
    cb_called = []
    provider.load_icon_async("", "win32", 36, lambda pix: cb_called.append(pix))
    assert len(cb_called) == 1
    assert provider.get_cache_stats()["pending"] == 0


# =================================================================================================
# 2. 待处理条目授权开关禁用测试
# =================================================================================================

def test_needs_attention_row_toggle_is_disabled(qtbot):
    """待处理条目的 ToggleSwitch 必须为 disabled，且 repair 按钮未被隐藏。"""
    item = _make_item("app-att", "失效应用", bucket=AppBucket.NEEDS_ATTENTION, is_enabled=False, can_toggle=False)
    row = ApplicationRow(item)
    qtbot.addWidget(row)
    row.show()

    assert row.enabled_toggle.isEnabled() is False
    assert not row.repair_button.isHidden()
    assert row.repair_button.text() == "选择主程序"


def test_needs_attention_row_toggle_blocks_click_and_emits_no_signal(qtbot):
    """在 disabled 开关上模拟点击，绝不发射 enabled_changed 信号。"""
    item = _make_item("app-att", "失效应用", bucket=AppBucket.NEEDS_ATTENTION, is_enabled=False, can_toggle=False)
    row = ApplicationRow(item)
    qtbot.addWidget(row)

    signals_emitted = []
    row.enabled_changed.connect(lambda aid, val: signals_emitted.append((aid, val)))

    # 模拟用户点击 ToggleSwitch
    qtbot.mouseClick(row.enabled_toggle, Qt.MouseButton.LeftButton)

    assert signals_emitted == [], "待处理条目的开关禁止发射 enabled_changed"
    assert row.enabled_toggle.isChecked() is False


def test_needs_attention_toggle_in_controller_triggers_no_save_or_popup():
    """直接向 controller.set_enabled 传入待处理条目 ID，直接被拦截，不触发重复校验、保存或弹窗。"""
    cat_mock = MagicMock()
    att_entry = AppEntry(
        app_id="app-att",
        display_name="待处理条目",
        exe_path=r"C:\fake\att.exe",
        launch_target=r"C:\fake\att.exe",
        launch_type=LaunchType.PORTABLE,
        source="Manual",
        is_user_managed=True,
        is_enabled=False,
        is_valid=False,
        requires_user_attention=True,
    )
    cat_mock.apps = {"app-att": att_entry}
    cat_mock.get_all_apps.return_value = [att_entry]

    controller = ApplicationsController(cat_mock, MagicMock())
    user_errors = []
    controller.user_error.connect(user_errors.append)

    # 尝试在 controller 层面切换待处理条目的授权状态
    controller.set_enabled("app-att", True)

    # 底层 set_app_enabled 绝不能被调用
    cat_mock.set_app_enabled.assert_not_called()
    assert user_errors == [], "待处理条目切换开关不应重复触发错误弹窗"


def test_repaired_app_switch_becomes_enabled_again(qtbot):
    """修复成功移入未允许后，开关恢复可用。"""
    item_before = _make_item("app-fix", "待修复应用", bucket=AppBucket.NEEDS_ATTENTION, is_enabled=False, can_toggle=False)
    row = ApplicationRow(item_before)
    qtbot.addWidget(row)
    assert row.enabled_toggle.isEnabled() is False

    # 模拟修复成功后调用 update_item
    item_after = _make_item("app-fix", "待修复应用", bucket=AppBucket.NOT_ALLOWED, is_enabled=False, can_toggle=True)
    row.update_item(item_after)

    assert row.enabled_toggle.isEnabled() is True
    assert row.repair_button.isHidden() is True
