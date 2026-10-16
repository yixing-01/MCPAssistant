"""同一真实启动目标的多入口去重回归测试。"""

import json

from unittest.mock import patch

from src.catalog.manager import CatalogManager, LookupStatus
from src.catalog.models import AppEntry
from src.catalog.scanner import SoftwareScanner
from src.catalog.shortcut_resolver import ShortcutInfo, ShortcutValidationResult


def _allowed_shortcut(shortcut_path: str, target_path: str) -> ShortcutValidationResult:
    return ShortcutValidationResult(
        is_allowed=True,
        info=ShortcutInfo(
            shortcut_path=shortcut_path,
            target_path=target_path,
            arguments="",
            is_resolved=True,
        ),
    )


def test_scanner_collapses_multiple_shortcuts_to_same_executable(tmp_path):
    """开始菜单和桌面的多个快捷方式指向同一 EXE 时只生成一个软件条目。"""
    target = tmp_path / "cloudmusic.exe"
    target.write_text("mock")
    shortcuts = [tmp_path / "menu-a.lnk", tmp_path / "menu-b.lnk", tmp_path / "desktop.lnk"]
    for shortcut in shortcuts:
        shortcut.write_text("mock")

    entries = {}

    def validate(shortcut_path):
        return _allowed_shortcut(shortcut_path, str(target))

    with patch("src.catalog.scanner.ShortcutResolver.validate", side_effect=validate):
        for shortcut in shortcuts:
            SoftwareScanner._add_or_merge(
                entries=entries,
                name="网易云音乐",
                exe_path=str(shortcut),
                source="StartMenu",
            )

    assert len(entries) == 1
    sole_entry = next(iter(entries.values()))
    assert sole_entry.display_name == "网易云音乐"
    assert sole_entry.resolved_target == str(target)


def test_loading_old_catalog_collapses_duplicate_shortcuts_and_preserves_authorization(tmp_path):
    """旧缓存中的同目标重复项自动迁移为一个，并保留用户已有授权。"""
    target = tmp_path / "cloudmusic.exe"
    target.write_text("mock")
    shortcuts = [tmp_path / "menu-a.lnk", tmp_path / "menu-b.lnk", tmp_path / "desktop.lnk"]
    for shortcut in shortcuts:
        shortcut.write_text("mock")

    old_entries = [
        AppEntry(
            app_id=f"cloudmusic-{index}",
            display_name="网易云音乐",
            alias="网易云音乐",
            exe_path=str(shortcut),
            source="Desktop" if index == 2 else "StartMenu",
            is_enabled=index == 2,
            is_valid=True,
            resolved_target=str(target),
        )
        for index, shortcut in enumerate(shortcuts)
    ]
    catalog_path = tmp_path / "catalog.json"
    catalog_path.write_text(
        json.dumps([entry.model_dump() for entry in old_entries], ensure_ascii=False),
        encoding="utf-8",
    )

    def validate(shortcut_path):
        return _allowed_shortcut(shortcut_path, str(target))

    with patch("src.catalog.manager.ShortcutResolver.validate", side_effect=validate):
        manager = CatalogManager(catalog_file_path=str(catalog_path))

    assert len(manager.apps) == 1
    sole_entry = next(iter(manager.apps.values()))
    assert sole_entry.is_enabled is True
    assert len(json.loads(catalog_path.read_text(encoding="utf-8"))) == 1


def test_lookup_treats_same_target_duplicates_as_one_candidate(tmp_path):
    """即使运行期间临时出现同目标重复项，也不能向小智返回虚假歧义。"""
    target = tmp_path / "cloudmusic.exe"
    target.write_text("mock")
    manager = CatalogManager(catalog_file_path=str(tmp_path / "catalog.json"))

    for index in range(3):
        shortcut = tmp_path / f"cloudmusic-{index}.lnk"
        shortcut.write_text("mock")
        entry = AppEntry(
            app_id=f"cloudmusic-{index}",
            display_name="网易云音乐",
            alias="网易云音乐",
            exe_path=str(shortcut),
            source="StartMenu",
            is_enabled=True,
            is_valid=True,
            resolved_target=str(target),
        )
        manager.apps[entry.app_id] = entry

    result = manager.find_authorized_app("网易云音乐")

    assert result.status == LookupStatus.FOUND
    assert result.app is not None
    assert result.app.resolved_target == str(target)


def test_rescan_matches_changed_shortcut_path_by_real_target_and_preserves_authorization(tmp_path):
    """重新扫描换成另一个等价快捷方式时不产生重复项，也不丢失原授权。"""
    target = tmp_path / "cloudmusic.exe"
    target.write_text("mock")
    old_shortcut = tmp_path / "desktop.lnk"
    new_shortcut = tmp_path / "start-menu.lnk"
    old_shortcut.write_text("mock")
    new_shortcut.write_text("mock")

    manager = CatalogManager(catalog_file_path=str(tmp_path / "catalog.json"))
    old_entry = AppEntry(
        app_id="cloudmusic-old",
        display_name="网易云音乐",
        alias="网易云音乐",
        exe_path=str(old_shortcut),
        source="Desktop",
        is_enabled=True,
        is_valid=True,
        resolved_target=str(target),
    )
    manager.apps[old_entry.app_id] = old_entry

    scanned_entry = AppEntry(
        app_id="cloudmusic-new",
        display_name="网易云音乐",
        alias="网易云音乐",
        exe_path=str(new_shortcut),
        source="StartMenu",
        is_enabled=False,
        is_valid=True,
        resolved_target=str(target),
    )

    with patch("src.catalog.manager.SoftwareScanner.scan_all", return_value=[scanned_entry]), patch(
        "src.catalog.manager.ShortcutResolver.validate",
        return_value=_allowed_shortcut(str(new_shortcut), str(target)),
    ):
        manager.refresh_scan(user_agreed=True)

    assert len(manager.apps) == 1
    sole_entry = next(iter(manager.apps.values()))
    assert sole_entry.app_id == old_entry.app_id
    assert sole_entry.is_enabled is True


def test_scanner_upgrades_stem_name_when_shortcut_discovered_later(tmp_path):
    """1. App Paths 先发现 cloudmusic.exe，随后开始菜单快捷方式发现网易云音乐，升级为中文友好名称并保留别名。"""
    target = tmp_path / "cloudmusic.exe"
    target.write_text("mock")
    shortcut = tmp_path / "网易云音乐.lnk"
    shortcut.write_text("mock")

    entries = {}

    def validate(shortcut_path):
        return _allowed_shortcut(shortcut_path, str(target))

    # 1. 模拟 App Paths 先发现
    SoftwareScanner._add_or_merge(
        entries=entries,
        name="cloudmusic",
        exe_path=str(target),
        source="AppPaths",
    )
    assert len(entries) == 1
    initial_entry = next(iter(entries.values()))
    assert initial_entry.display_name == "cloudmusic"

    # 2. 模拟随后开始菜单扫描到指向相同 EXE 的快捷方式
    with patch("src.catalog.scanner.ShortcutResolver.validate", side_effect=validate):
        SoftwareScanner._add_or_merge(
            entries=entries,
            name="网易云音乐",
            exe_path=str(shortcut),
            source="StartMenu",
        )

    assert len(entries) == 1
    sole_entry = next(iter(entries.values()))
    assert sole_entry.display_name == "网易云音乐"
    assert sole_entry.aliases[0] == "网易云音乐"
    assert "cloudmusic" in sole_entry.aliases
    assert sole_entry.resolved_target == str(target)


def test_scanner_generic_rule_not_hardcoded_for_cloudmusic(tmp_path):
    """4. 验证名称质量提升是通用规则，对虚构软件 myawesomeplayer 同样通用生效。"""
    target = tmp_path / "myawesomeplayer.exe"
    target.write_text("mock")
    shortcut = tmp_path / "超能影音.lnk"
    shortcut.write_text("mock")

    entries = {}

    def validate(shortcut_path):
        return _allowed_shortcut(shortcut_path, str(target))

    # App Paths 先发现内部英文名
    SoftwareScanner._add_or_merge(
        entries=entries,
        name="myawesomeplayer",
        exe_path=str(target),
        source="AppPaths",
    )
    # 桌面快捷方式随后发现中文友好名
    with patch("src.catalog.scanner.ShortcutResolver.validate", side_effect=validate):
        SoftwareScanner._add_or_merge(
            entries=entries,
            name="超能影音",
            exe_path=str(shortcut),
            source="Desktop",
        )

    assert len(entries) == 1
    sole_entry = next(iter(entries.values()))
    assert sole_entry.display_name == "超能影音"
    assert sole_entry.aliases[0] == "超能影音"
    assert "myawesomeplayer" in sole_entry.aliases


def test_scanner_does_not_downgrade_higher_quality_name_with_low_quality_source(tmp_path):
    """5. 低质量来源绝不能覆盖已有正式友好名称。"""
    target = tmp_path / "cloudmusic.exe"
    target.write_text("mock")
    shortcut = tmp_path / "网易云音乐.lnk"
    shortcut.write_text("mock")

    entries = {}

    def validate(shortcut_path):
        return _allowed_shortcut(shortcut_path, str(target))

    # 先从开始菜单发现高质量正式名
    with patch("src.catalog.scanner.ShortcutResolver.validate", side_effect=validate):
        SoftwareScanner._add_or_merge(
            entries=entries,
            name="网易云音乐",
            exe_path=str(shortcut),
            source="StartMenu",
        )
    # 随后 App Paths 扫描到英文 stem
    SoftwareScanner._add_or_merge(
        entries=entries,
        name="cloudmusic",
        exe_path=str(target),
        source="AppPaths",
    )

    sole_entry = next(iter(entries.values()))
    assert sole_entry.display_name == "网易云音乐"
    assert sole_entry.aliases[0] == "网易云音乐"
    assert "cloudmusic" in sole_entry.aliases


def test_user_managed_name_never_overwritten_by_scan(tmp_path):
    """6. 用户手动管理的名称绝不被自动扫描覆盖。"""
    target = tmp_path / "custom_app.exe"
    target.write_text("mock")
    shortcut = tmp_path / "扫描发现名.lnk"
    shortcut.write_text("mock")

    entries = {}
    from src.catalog.models import LaunchType, make_stable_identity
    identity = make_stable_identity(LaunchType.WIN32, str(target))

    # 预设用户手动管理的条目
    manual_entry = AppEntry(
        app_id="manual-1",
        display_name="我的专属工具",
        source="Manual",
        exe_path=str(target),
        is_user_managed=True,
        is_enabled=True,
    )
    entries[identity] = manual_entry

    def validate(shortcut_path):
        return _allowed_shortcut(shortcut_path, str(target))

    with patch("src.catalog.scanner.ShortcutResolver.validate", side_effect=validate):
        SoftwareScanner._add_or_merge(
            entries=entries,
            name="扫描发现名",
            exe_path=str(shortcut),
            source="StartMenu",
        )

    sole_entry = entries[identity]
    assert sole_entry.display_name == "我的专属工具"
    assert sole_entry.is_user_managed is True


def test_loading_old_catalog_migrates_stem_display_name_to_friendly_alias(tmp_path):
    """7. 旧缓存加载时自动将内部 stem 名称迁移为友好中文别名，保留授权与 app_id。"""
    target = tmp_path / "cloudmusic.exe"
    target.write_text("mock")
    catalog_path = tmp_path / "catalog.json"

    # 模拟真实旧缓存数据结构
    old_data = [{
        "app_id": "app_cloudmusic_fixed_id",
        "display_name": "cloudmusic",
        "aliases": ["cloudmusic", "网易云音乐"],
        "launch_type": "win32",
        "launch_target": str(target),
        "stable_identity": f"file:{str(target).lower()}",
        "exe_path": str(target),
        "source": "AppPaths",
        "is_enabled": True,
        "is_valid": True,
        "is_restricted": False,
        "is_user_managed": False,
    }]
    catalog_path.write_text(json.dumps(old_data, ensure_ascii=False), encoding="utf-8")

    manager = CatalogManager(catalog_file_path=str(catalog_path))

    assert "app_cloudmusic_fixed_id" in manager.apps
    app = manager.apps["app_cloudmusic_fixed_id"]
    assert app.display_name == "网易云音乐"
    assert app.aliases[0] == "网易云音乐"
    assert "cloudmusic" in app.aliases
    assert app.is_enabled is True  # 授权状态保持不变

    # 检查磁盘缓存已更新
    saved_data = json.loads(catalog_path.read_text(encoding="utf-8"))
    assert saved_data[0]["display_name"] == "网易云音乐"
    assert saved_data[0]["aliases"][0] == "网易云音乐"


def test_refresh_scan_updates_to_friendly_name_while_preserving_authorization(tmp_path):
    """8. 重新扫描后采用更友好的新名称，同时保留原有授权状态和 app_id。"""
    target = tmp_path / "cloudmusic.exe"
    target.write_text("mock")
    catalog_path = tmp_path / "catalog.json"
    manager = CatalogManager(catalog_file_path=str(catalog_path))

    old_entry = AppEntry(
        app_id="app_cloudmusic_old",
        display_name="cloudmusic",
        aliases=["cloudmusic"],
        exe_path=str(target),
        source="AppPaths",
        is_enabled=True,
        is_valid=True,
    )
    manager.apps[old_entry.app_id] = old_entry

    new_scanned = AppEntry(
        app_id="app_cloudmusic_new_scanned",
        display_name="网易云音乐",
        aliases=["网易云音乐"],
        exe_path=str(target),
        source="StartMenu",
        is_enabled=False,
        is_valid=True,
    )

    with patch("src.catalog.manager.SoftwareScanner.scan_all", return_value=[new_scanned]):
        manager.refresh_scan(user_agreed=True)

    assert len(manager.apps) == 1
    sole_entry = next(iter(manager.apps.values()))
    assert sole_entry.app_id == "app_cloudmusic_old"
    assert sole_entry.display_name == "网易云音乐"
    assert sole_entry.aliases[0] == "网易云音乐"
    assert "cloudmusic" in sole_entry.aliases
    assert sole_entry.is_enabled is True  # 用户已有授权完好保留


def test_presentation_layer_voice_example_uses_friendly_name(tmp_path):
    """9. 展示模型正确生成可以说：打开网易云音乐。"""
    from src.ui_qt.presentation.apps import build_app_items

    app = AppEntry(
        app_id="app_cm",
        display_name="网易云音乐",
        aliases=["网易云音乐", "cloudmusic"],
        exe_path=r"C:\fake\cloudmusic.exe",
        source="StartMenu",
        is_enabled=True,
        is_valid=True,
    )
    items = build_app_items([app])
    assert len(items) == 1
    assert items[0].display_name == "网易云音乐"
    assert items[0].voice_example == "可以说：打开网易云音乐"

