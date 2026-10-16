from src.catalog.models import AppEntry, LaunchType
from src.ui_qt.presentation.apps import AppBucket, build_app_items, filter_items


def app(app_id, name, *, enabled=False, valid=True, manual=False, path="C:/secret/app.exe", dangerous=False, restricted=False, attention=False):
    return AppEntry(
        app_id=app_id,
        display_name=name,
        aliases=[name, f"打开{name}"],
        launch_type=LaunchType.PORTABLE if manual else LaunchType.WIN32,
        launch_target=path,
        source="Manual" if manual else "AppPaths",
        is_user_managed=manual,
        is_enabled=enabled,
        is_valid=valid,
        requires_user_attention=attention,
        is_restricted=restricted,
        validation_status="dangerous" if dangerous else ("valid" if valid else "invalid"),
    )


def test_dangerous_automatic_apps_are_hidden():
    items = build_app_items([app("a", "危险系统项", valid=False, manual=False, dangerous=True)])
    assert items == []


def test_passive_invalid_automatic_apps_are_hidden():
    """被动扫描发现的普通 invalid 自动应用保持隐藏，不进入 needs_attention。"""
    items = build_app_items([app("a", "被动扫描项", valid=False, manual=False, attention=False)])
    assert items == []


def test_invalid_automatic_apps_enter_needs_attention():
    """需要用户处理的普通 invalid 自动条目进入 NEEDS_ATTENTION。"""
    items = build_app_items([app("a", "可修复项", valid=False, manual=False, attention=True)])
    assert len(items) == 1
    assert items[0].bucket is AppBucket.NEEDS_ATTENTION
    assert items[0].repair_action_label == "选择主程序"


def test_invalid_manual_app_needs_attention():
    items = build_app_items([app("m", "本地播放器", valid=False, manual=True)])
    assert items[0].bucket is AppBucket.NEEDS_ATTENTION
    assert not hasattr(items[0], "launch_target")


def test_search_uses_name_and_alias_not_path():
    items = build_app_items([app("a", "微信", path="C:/secret/hidden.exe")])
    assert len(filter_items(items, query="微信")) == 1
    assert filter_items(items, query="secret") == []
