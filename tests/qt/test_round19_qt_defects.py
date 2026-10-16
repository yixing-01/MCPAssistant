"""第十九轮 Qt 界面缺陷测试：忙碌恢复、跨分组行复用、批量授权过滤与诊断待处理对齐。"""

from unittest.mock import MagicMock, patch
import pytest

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication, QMessageBox

from src.catalog.models import AppEntry, LaunchType
from src.core.config import AppConfig
from src.ui_qt.controllers.applications_controller import ApplicationsController
from src.ui_qt.controllers.diagnostics_controller import DiagnosticsController
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
# 1. 忙碌状态恢复测试 (Issue 1)
# =================================================================================================

def test_busy_true_disables_all_controls(qtbot):
    """进入忙碌状态时，全部按钮、所有行的开关及修复按钮均被禁用。"""
    cat_mock = MagicMock()
    controller = ApplicationsController(cat_mock, MagicMock())
    page = ApplicationsPage(controller)
    qtbot.addWidget(page)

    item1 = _make_item("app-1", "普通应用", bucket=AppBucket.ALLOWED, can_toggle=True)
    item2 = _make_item("app-2", "待处理应用", bucket=AppBucket.NEEDS_ATTENTION, can_toggle=False)
    page.set_items([item1, item2])

    page._set_busy(True)

    assert page.refresh_button.isEnabled() is False
    assert page.add_button.isEnabled() is False
    assert page.more_button.isEnabled() is False
    for row in page.rows:
        assert row.enabled_toggle.isEnabled() is False
    assert page.rows[1].repair_button.isEnabled() is False


def test_busy_false_restores_only_can_toggle_true(qtbot):
    """忙碌状态结束恢复时，仅 can_toggle=True 的普通行恢复启用，待处理行严格保持禁用。"""
    cat_mock = MagicMock()
    controller = ApplicationsController(cat_mock, MagicMock())
    page = ApplicationsPage(controller)
    qtbot.addWidget(page)

    item_allowed = _make_item("app-1", "普通应用", bucket=AppBucket.ALLOWED, can_toggle=True)
    item_att = _make_item("app-2", "待处理应用", bucket=AppBucket.NEEDS_ATTENTION, can_toggle=False)
    page.set_items([item_allowed, item_att])

    # 初始状态：普通行可用，待处理行置灰
    assert page.rows[0].enabled_toggle.isEnabled() is True
    assert page.rows[1].enabled_toggle.isEnabled() is False

    # 模拟进入 busy
    page._set_busy(True)
    assert page.rows[0].enabled_toggle.isEnabled() is False
    assert page.rows[1].enabled_toggle.isEnabled() is False

    # 模拟退出 busy
    page._set_busy(False)
    assert page.refresh_button.isEnabled() is True
    assert page.add_button.isEnabled() is True
    assert page.rows[0].enabled_toggle.isEnabled() is True, "普通行开关应恢复为可点击"
    assert page.rows[1].enabled_toggle.isEnabled() is False, "待处理行开关必须继续保持禁用！"
    assert page.rows[1].repair_button.isEnabled() is True, "待处理修复按钮应恢复可用"


def test_busy_during_set_items_applies_disabled(qtbot):
    """若在忙碌中调用 set_items，渲染的控件必须处于禁用状态，退出 busy 后再恢复。"""
    cat_mock = MagicMock()
    controller = ApplicationsController(cat_mock, MagicMock())
    page = ApplicationsPage(controller)
    qtbot.addWidget(page)

    page._set_busy(True)
    item1 = _make_item("app-1", "普通应用", can_toggle=True)
    item2 = _make_item("app-2", "待处理应用", can_toggle=False)
    page.set_items([item1, item2])

    assert page.rows[0].enabled_toggle.isEnabled() is False
    assert page.rows[1].enabled_toggle.isEnabled() is False

    page._set_busy(False)
    assert page.rows[0].enabled_toggle.isEnabled() is True
    assert page.rows[1].enabled_toggle.isEnabled() is False


def test_scan_failure_restores_busy_and_preserves_needs_attention_disabled(qtbot):
    """扫描刷新失败路径在发出 busy_changed(False) 后，待处理条目开关保持置灰禁用。"""
    cat_mock = MagicMock()
    app_att = AppEntry(
        app_id="app-att",
        display_name="待处理应用",
        exe_path=r"C:\fake\att.exe",
        launch_target=r"C:\fake\att.exe",
        launch_type=LaunchType.PORTABLE,
        source="Manual",
        is_enabled=False,
        is_valid=False,
        is_user_managed=True,
    )
    cat_mock.get_all_apps.return_value = [app_att]
    controller = ApplicationsController(cat_mock, MagicMock())
    page = ApplicationsPage(controller)
    qtbot.addWidget(page)

    page._select_bucket(AppBucket.NEEDS_ATTENTION)
    assert len(page.rows) == 1
    assert page.rows[0].enabled_toggle.isEnabled() is False

    with patch.object(QMessageBox, "warning"):
        # 模拟后台扫描失败
        controller._is_busy = True
        controller.busy_changed.emit(True)
        assert page.rows[0].enabled_toggle.isEnabled() is False

        controller._accept_scan(controller._scan_request_id, Exception("scan network failure"))

    assert controller.is_busy is False
    assert page._is_busy is False
    assert page.rows[0].enabled_toggle.isEnabled() is False, "扫描失败后待处理开关仍必须禁用"


def test_batch_failure_restores_busy_and_preserves_needs_attention_disabled(qtbot):
    """批量操作失败返回后，发出 busy_changed(False)，待处理条目开关保持置灰禁用。"""
    cat_mock = MagicMock()
    app_att = AppEntry(
        app_id="app-att",
        display_name="待处理应用",
        exe_path=r"C:\fake\att.exe",
        launch_target=r"C:\fake\att.exe",
        launch_type=LaunchType.PORTABLE,
        source="Manual",
        is_enabled=False,
        is_valid=False,
        is_user_managed=True,
    )
    app_ok = AppEntry(
        app_id="app-ok",
        display_name="普通应用",
        exe_path=r"C:\fake\ok.exe",
        launch_target=r"C:\fake\ok.exe",
        launch_type=LaunchType.WIN32,
        source="StartMenu",
        is_enabled=True,
        is_valid=True,
    )
    cat_mock.get_all_apps.return_value = [app_ok, app_att]
    controller = ApplicationsController(cat_mock, MagicMock())
    page = ApplicationsPage(controller)
    qtbot.addWidget(page)

    item_ok = _make_item("app-ok", "普通应用", bucket=AppBucket.ALLOWED, can_toggle=True)
    item_att = _make_item("app-att", "待处理应用", bucket=AppBucket.NEEDS_ATTENTION, can_toggle=False)
    page.set_items([item_ok, item_att])

    with patch.object(QMessageBox, "warning"):
        controller._is_busy = True
        controller.busy_changed.emit(True)
        assert page.rows[0].enabled_toggle.isEnabled() is False
        assert page.rows[1].enabled_toggle.isEnabled() is False

        controller._accept_batch(controller._batch_request_id, Exception("batch io error"))

    assert controller.is_busy is False
    assert page.rows[0].enabled_toggle.isEnabled() is True
    # 验证缓存中的待处理行仍然禁用
    assert page._rows_by_id["app-att"].enabled_toggle.isEnabled() is False
    # 切换至待处理分组，验证可见行的开关仍严格保持禁用
    page._select_bucket(AppBucket.NEEDS_ATTENTION)
    assert len(page.rows) == 1
    assert page.rows[0].item.app_id == "app-att"
    assert page.rows[0].enabled_toggle.isEnabled() is False


# =================================================================================================
# 2. 跨分组惰性行缓存复用与目录同步清理 (Issue 2)
# =================================================================================================

def test_cross_bucket_switching_reuses_row_instances(qtbot):
    """跨分组往返切换：同一个条目的 ApplicationRow Python 与 Qt 对象被 100% 复用，不销毁重建。"""
    cat_mock = MagicMock()
    app_allowed = AppEntry(
        app_id="app-1",
        display_name="已允许一",
        exe_path=r"C:\fake\a.exe",
        launch_target=r"C:\fake\a.exe",
        launch_type=LaunchType.WIN32,
        source="StartMenu",
        is_enabled=True,
        is_valid=True,
    )
    app_not = AppEntry(
        app_id="app-2",
        display_name="未允许二",
        exe_path=r"C:\fake\b.exe",
        launch_target=r"C:\fake\b.exe",
        launch_type=LaunchType.WIN32,
        source="StartMenu",
        is_enabled=False,
        is_valid=True,
    )
    cat_mock.get_all_apps.return_value = [app_allowed, app_not]
    controller = ApplicationsController(cat_mock, MagicMock())
    page = ApplicationsPage(controller)
    qtbot.addWidget(page)

    # 1. 处于已允许分组
    page._select_bucket(AppBucket.ALLOWED)
    assert len(page.rows) == 1
    row_allowed_v1 = page.rows[0]
    assert row_allowed_v1.item.app_id == "app-1"

    # 2. 切换至未允许分组
    page._select_bucket(AppBucket.NOT_ALLOWED)
    assert len(page.rows) == 1
    row_not_v1 = page.rows[0]
    assert row_not_v1.item.app_id == "app-2"
    assert row_not_v1 is not row_allowed_v1

    # 3. 再次切回已允许分组
    page._select_bucket(AppBucket.ALLOWED)
    assert len(page.rows) == 1
    row_allowed_v2 = page.rows[0]
    assert row_allowed_v2 is row_allowed_v1, "返回原分组时必须复用原 ApplicationRow 对象实例！"


def test_invisible_cached_rows_are_hidden_not_destroyed(qtbot):
    """切换分组时，当前不可见的行从布局中移除并隐藏，但保持在缓存中，绝不 deleteLater。"""
    cat_mock = MagicMock()
    app1 = AppEntry(
        app_id="app-1",
        display_name="已允许应用",
        exe_path=r"C:\fake\a.exe",
        launch_target=r"C:\fake\a.exe",
        launch_type=LaunchType.WIN32,
        source="StartMenu",
        is_enabled=True,
        is_valid=True,
    )
    app2 = AppEntry(
        app_id="app-2",
        display_name="未允许应用",
        exe_path=r"C:\fake\b.exe",
        launch_target=r"C:\fake\b.exe",
        launch_type=LaunchType.WIN32,
        source="StartMenu",
        is_enabled=False,
        is_valid=True,
    )
    cat_mock.get_all_apps.return_value = [app1, app2]
    controller = ApplicationsController(cat_mock, MagicMock())
    page = ApplicationsPage(controller)
    qtbot.addWidget(page)

    page._select_bucket(AppBucket.ALLOWED)
    row1 = page.rows[0]

    # 切到未允许
    page._select_bucket(AppBucket.NOT_ALLOWED)
    # row1 必须被隐藏
    assert row1.isHidden() is True
    # row1 必须不在 list_layout 中
    assert page.list_layout.indexOf(row1) == -1
    # row1 仍保存在 _rows_by_id 缓存中
    assert "app-1" in page._rows_by_id
    assert page._rows_by_id["app-1"] is row1


def test_catalog_removal_cleans_up_cached_row(qtbot):
    """当条目彻底从底层目录移除（如卸载或删除手动应用），reload 后缓存行才被安全销毁。"""
    cat_mock = MagicMock()
    app1 = AppEntry(
        app_id="app-1",
        display_name="应用一",
        exe_path=r"C:\fake\a.exe",
        launch_target=r"C:\fake\a.exe",
        launch_type=LaunchType.WIN32,
        source="StartMenu",
        is_enabled=True,
        is_valid=True,
    )
    app2 = AppEntry(
        app_id="app-2",
        display_name="应用二",
        exe_path=r"C:\fake\b.exe",
        launch_target=r"C:\fake\b.exe",
        launch_type=LaunchType.WIN32,
        source="StartMenu",
        is_enabled=True,
        is_valid=True,
    )
    cat_mock.get_all_apps.return_value = [app1, app2]
    controller = ApplicationsController(cat_mock, MagicMock())
    page = ApplicationsPage(controller)
    qtbot.addWidget(page)

    page._select_bucket(AppBucket.ALLOWED)
    assert "app-1" in page._rows_by_id
    assert "app-2" in page._rows_by_id

    # 底层目录删除了 app-1，仅剩 app-2
    cat_mock.get_all_apps.return_value = [app2]
    controller.reload()

    # app-1 已彻底移出缓存
    assert "app-1" not in page._rows_by_id
    assert "app-2" in page._rows_by_id
    assert len(page.rows) == 1
    assert page.rows[0].item.app_id == "app-2"


def test_lazy_row_creation_does_not_precreate_all_rows(qtbot):
    """惰性创建：打开页面时绝不预先创建所有 240 个行控件，仅按需创建当前可见条目。"""
    apps = [
        AppEntry(
            app_id=f"app-{i}",
            display_name=f"应用 {i}",
            exe_path=rf"C:\fake\{i}.exe",
            launch_target=rf"C:\fake\{i}.exe",
            launch_type=LaunchType.WIN32,
            source="StartMenu",
            is_enabled=(i < 3),
            is_valid=True,
        )
        for i in range(10)
    ]
    cat_mock = MagicMock()
    cat_mock.get_all_apps.return_value = apps
    controller = ApplicationsController(cat_mock, MagicMock())
    page = ApplicationsPage(controller)
    qtbot.addWidget(page)

    # 初始默认已允许分组 (3 个)
    assert len(page.rows) == 3
    assert len(page._rows_by_id) == 3, "初始仅应创建可见的 3 个行，不得预先创建全部条目"

    # 切换至未允许分组 (7 个)
    page._select_bucket(AppBucket.NOT_ALLOWED)
    assert len(page.rows) == 7
    assert len(page._rows_by_id) == 10, "访问后才按需惰性创建并累计缓存至 10 个"


# =================================================================================================
# 3. 批量授权过滤与待处理拦截测试 (Issue 3)
# =================================================================================================

def test_more_button_disabled_in_needs_attention_bucket(qtbot):
    """待处理分组中（can_toggle 均为 False），更多按钮及菜单项必须置灰禁用。"""
    cat_mock = MagicMock()
    app_att = AppEntry(
        app_id="app-att",
        display_name="失效应用",
        exe_path=r"C:\fake\att.exe",
        launch_target=r"C:\fake\att.exe",
        launch_type=LaunchType.PORTABLE,
        source="Manual",
        is_enabled=False,
        is_valid=False,
        is_user_managed=True,
    )
    cat_mock.get_all_apps.return_value = [app_att]
    controller = ApplicationsController(cat_mock, MagicMock())
    page = ApplicationsPage(controller)
    qtbot.addWidget(page)

    page._select_bucket(AppBucket.NEEDS_ATTENTION)

    assert page.more_button.isEnabled() is False
    assert page.allow_visible_action.isEnabled() is False
    assert page.revoke_visible_action.isEnabled() is False


def test_more_button_enabled_when_toggleable_items_exist(qtbot):
    """当前分组存在可切换授权条目时，更多按钮及菜单动作恢复可用。"""
    cat_mock = MagicMock()
    app_ok = AppEntry(
        app_id="app-ok",
        display_name="正常应用",
        exe_path=r"C:\fake\ok.exe",
        launch_target=r"C:\fake\ok.exe",
        launch_type=LaunchType.WIN32,
        source="StartMenu",
        is_enabled=True,
        is_valid=True,
    )
    cat_mock.get_all_apps.return_value = [app_ok]
    controller = ApplicationsController(cat_mock, MagicMock())
    page = ApplicationsPage(controller)
    qtbot.addWidget(page)

    page._select_bucket(AppBucket.ALLOWED)

    assert page.more_button.isEnabled() is True
    assert page.allow_visible_action.isEnabled() is True
    assert page.revoke_visible_action.isEnabled() is True


def test_confirm_batch_blocks_zero_toggleable_items(qtbot):
    """当可见列表中无可操作项时，点击批量操作直接弹提示信息，不调用 controller.batch_set_visible。"""
    cat_mock = MagicMock()
    controller = ApplicationsController(cat_mock, MagicMock())
    controller.batch_set_visible = MagicMock()
    page = ApplicationsPage(controller)
    qtbot.addWidget(page)

    # 传入不可操作的待处理项
    item_att = _make_item("app-att", "待处理", bucket=AppBucket.NEEDS_ATTENTION, can_toggle=False)
    page.set_items([item_att])

    with patch.object(QMessageBox, "information") as mock_info:
        page._confirm_batch(True)
        mock_info.assert_called_once()
        assert "没有可操作的应用" in mock_info.call_args[0][2]
        controller.batch_set_visible.assert_not_called()


def test_confirm_batch_dialog_prompts_only_toggleable_count(qtbot):
    """批量确认弹窗中文案只展示可操作的应用数量。"""
    cat_mock = MagicMock()
    controller = ApplicationsController(cat_mock, MagicMock())
    controller.batch_set_visible = MagicMock()
    page = ApplicationsPage(controller)
    qtbot.addWidget(page)

    # 2 个可操作项 + 1 个待处理不可操作项
    item1 = _make_item("app-1", "应用一", can_toggle=True)
    item2 = _make_item("app-2", "应用二", can_toggle=True)
    item3 = _make_item("app-3", "待处理", can_toggle=False)
    page.set_items([item1, item2, item3])

    with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No) as mock_q:
        page._confirm_batch(True)
        mock_q.assert_called_once()
        # 弹窗提示应明确是 2 个应用，绝不是 3 个
        assert "2 个应用" in mock_q.call_args[0][2]
        controller.batch_set_visible.assert_not_called()


def test_batch_set_visible_filters_can_toggle_in_controller():
    """ApplicationsController.batch_set_visible 严格过滤 can_toggle，无可操作项时不进入 busy。"""
    cat_mock = MagicMock()
    app_att = AppEntry(
        app_id="app-att",
        display_name="待处理应用",
        exe_path=r"C:\fake\att.exe",
        launch_target=r"C:\fake\att.exe",
        launch_type=LaunchType.PORTABLE,
        source="Manual",
        is_enabled=False,
        is_valid=False,
        is_user_managed=True,
    )
    cat_mock.get_all_apps.return_value = [app_att]
    controller = ApplicationsController(cat_mock, MagicMock())
    controller.set_bucket(AppBucket.NEEDS_ATTENTION)

    busy_signals = []
    controller.busy_changed.connect(lambda b: busy_signals.append(b))

    controller.batch_set_visible(True)

    # 不可进入 busy，不得发射 busy_changed，不得调用底层 batch_set_enabled
    assert controller.is_busy is False
    assert busy_signals == []
    cat_mock.batch_set_enabled.assert_not_called()


# =================================================================================================
# 4. 诊断摘要数量与展示模型 100% 对齐测试 (Issue 4)
# =================================================================================================

def test_diagnostics_summary_aligned_with_applications_page(tmp_path):
    """诊断摘要中的“需要处理”与“已允许”数量必须与 build_app_items 统计严格对齐。"""
    config_mock = MagicMock()
    config_mock.last_load_error = None
    config_mock.load_config.return_value = AppConfig(mcp_endpoint="wss://api.xiaozhi.me/mcp/?token=test")

    # 构造 6 类典型目录数据
    app_allowed1 = AppEntry(app_id="a1", display_name="允许一", exe_path=r"C:\1.exe", launch_target=r"C:\1.exe", launch_type=LaunchType.WIN32, source="StartMenu", is_enabled=True, is_valid=True)
    app_allowed2 = AppEntry(app_id="a2", display_name="允许二", exe_path=r"C:\2.exe", launch_target=r"C:\2.exe", launch_type=LaunchType.WIN32, source="StartMenu", is_enabled=True, is_valid=True)
    app_not_allowed = AppEntry(app_id="a3", display_name="未允许", exe_path=r"C:\3.exe", launch_target=r"C:\3.exe", launch_type=LaunchType.WIN32, source="StartMenu", is_enabled=False, is_valid=True)
    # 待处理 1：手动管理失效
    app_manual_att = AppEntry(app_id="a4", display_name="手动失效", exe_path=r"C:\4.exe", launch_target=r"C:\4.exe", launch_type=LaunchType.PORTABLE, source="Manual", is_enabled=False, is_valid=False, is_user_managed=True)
    # 待处理 2：非手动，但曾点击允许失败被标记 requires_user_attention=True
    app_active_att = AppEntry(app_id="a5", display_name="主动点击失效", exe_path=r"C:\5.exe", launch_target=r"C:\5.exe", launch_type=LaunchType.WIN32, source="StartMenu", is_enabled=False, is_valid=False, is_user_managed=False, requires_user_attention=True)
    # 隐藏项 1：被动扫描 invalid（无 attention）
    app_passive_invalid = AppEntry(app_id="a6", display_name="被动扫描垃圾", exe_path=r"C:\6.exe", launch_target=r"C:\6.exe", launch_type=LaunchType.WIN32, source="StartMenu", is_enabled=False, is_valid=False, is_user_managed=False, requires_user_attention=False)
    # 隐藏项 2：高危系统项
    app_dangerous = AppEntry(app_id="a7", display_name="cmd", exe_path=r"C:\Windows\system32\cmd.exe", launch_target=r"C:\Windows\system32\cmd.exe", launch_type=LaunchType.WIN32, source="StartMenu", is_enabled=False, is_valid=False, validation_status="dangerous")

    all_apps = [
        app_allowed1, app_allowed2, app_not_allowed,
        app_manual_att, app_active_att,
        app_passive_invalid, app_dangerous,
    ]

    cat_mock = MagicMock()
    cat_mock.get_all_apps.return_value = all_apps

    # 1. 验证应用页面构建的模型统计
    items = build_app_items(all_apps)
    allowed_count = len([it for it in items if it.bucket is AppBucket.ALLOWED])
    not_allowed_count = len([it for it in items if it.bucket is AppBucket.NOT_ALLOWED])
    needs_attention_count = len([it for it in items if it.bucket is AppBucket.NEEDS_ATTENTION])

    assert allowed_count == 2
    assert not_allowed_count == 1
    assert needs_attention_count == 2

    # 2. 验证 DiagnosticsController 生成的摘要
    diag = DiagnosticsController(config_mock, cat_mock, log_dir=tmp_path)
    summary = diag.build_summary(product_status="正在运行", tray_available=True)

    assert "已允许应用：2" in summary
    assert "需要处理：2" in summary, "诊断摘要的需要处理数量必须对齐应用页（包含 requires_user_attention=True）"
    assert "系统托盘：可用" in summary
    assert "当前状态：正在运行" in summary
