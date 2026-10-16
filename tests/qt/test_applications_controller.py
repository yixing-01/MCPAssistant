from unittest.mock import MagicMock

from src.catalog.models import AppEntry, LaunchType
from src.ui_qt.controllers.applications_controller import ApplicationsController
from src.ui_qt.presentation.apps import AppBucket


def test_toggle_uses_single_app_authorization(qtbot):
    manager = MagicMock()
    manager.get_all_apps.return_value = []
    manager.set_app_enabled.return_value = True
    controller = ApplicationsController(manager, MagicMock())
    controller.set_enabled("app-1", True)
    manager.set_app_enabled.assert_called_once_with("app-1", True)
    manager.batch_set_enabled.assert_not_called()


def test_failed_authorization_emits_plain_error(qtbot):
    manager = MagicMock()
    manager.get_all_apps.return_value = []
    manager.set_app_enabled.return_value = False
    controller = ApplicationsController(manager, MagicMock())
    with qtbot.waitSignal(controller.user_error, timeout=1000) as signal:
        controller.set_enabled("bad", True)
    assert signal.args == ["这个扫描入口不能直接允许，已移到“需要处理”。请选择软件主程序。"]


def test_filter_change_publishes_only_requested_bucket(qtbot):
    manager = MagicMock()
    manager.get_all_apps.return_value = []
    controller = ApplicationsController(manager, MagicMock())
    with qtbot.waitSignal(controller.items_changed, timeout=1000) as signal:
        controller.set_bucket(AppBucket.ALLOWED)
    assert signal.args == [[]]


def test_batch_action_is_scoped_to_visible_ids(qtbot):
    manager = MagicMock()
    manager.get_all_apps.return_value = []
    controller = ApplicationsController(manager, MagicMock())
    controller._visible_ids = ["visible-1", "visible-2"]
    controller.batch_set_visible(False)
    with qtbot.waitSignal(controller.busy_changed, timeout=1000):
        pass
    manager.batch_set_enabled.assert_called_once_with(
        False, app_ids=["visible-1", "visible-2"]
    )


def test_allowed_items_unaffected_by_search_and_tab_filter(qtbot):
    """4. 搜索和标签切换不会改变首页的已允许应用候选集合。"""
    apps = [
        AppEntry(
            app_id="app-allowed-1",
            display_name="微信",
            launch_type=LaunchType.WIN32,
            launch_target=r"C:\fake\wechat.exe",
            source="start_menu",
            is_enabled=True,
            is_valid=True,
        ),
        AppEntry(
            app_id="app-allowed-2",
            display_name="ChatGPT",
            launch_type=LaunchType.WIN32,
            launch_target=r"C:\fake\chatgpt.exe",
            source="start_menu",
            is_enabled=True,
            is_valid=True,
        ),
        AppEntry(
            app_id="app-disabled-1",
            display_name="记事本",
            launch_type=LaunchType.WIN32,
            launch_target=r"C:\fake\notepad.exe",
            source="start_menu",
            is_enabled=False,
            is_valid=True,
        ),
    ]
    manager = MagicMock()
    manager.get_all_apps.return_value = apps
    controller = ApplicationsController(manager, MagicMock())

    # 初始状态：包含两个已允许应用
    initial_allowed = controller.allowed_items()
    assert len(initial_allowed) == 2
    assert {a.app_id for a in initial_allowed} == {"app-allowed-1", "app-allowed-2"}

    # 1. 切换到“未允许”分组
    controller.set_bucket(AppBucket.NOT_ALLOWED)
    assert len(controller.allowed_items()) == 2
    assert {a.app_id for a in controller.allowed_items()} == {"app-allowed-1", "app-allowed-2"}

    # 2. 执行搜索“微信”（此时可见列表只剩微信，但 allowed_items 必须仍包含 ChatGPT）
    controller.set_bucket(AppBucket.ALLOWED)
    controller.set_query("微信")
    assert len(controller._visible_ids) == 1
    assert len(controller.allowed_items()) == 2
    assert {a.app_id for a in controller.allowed_items()} == {"app-allowed-1", "app-allowed-2"}


def test_allowed_items_changed_signal_on_authorization_and_reload(qtbot):
    """16. 授权/撤销或重新扫描后，首页连接的 allowed_items_changed 会发布新数据。"""
    apps = [
        AppEntry(
            app_id="app-1",
            display_name="微信",
            launch_type=LaunchType.WIN32,
            launch_target=r"C:\fake\wechat.exe",
            source="start_menu",
            is_enabled=True,
            is_valid=True,
        ),
    ]
    manager = MagicMock()
    manager.get_all_apps.return_value = apps
    manager.set_app_enabled.return_value = True
    controller = ApplicationsController(manager, MagicMock())

    # 监听 allowed_items_changed 信号
    with qtbot.waitSignal(controller.allowed_items_changed, timeout=1000) as sig:
        controller.reload()
    assert len(sig.args[0]) == 1
    assert sig.args[0][0].app_id == "app-1"
