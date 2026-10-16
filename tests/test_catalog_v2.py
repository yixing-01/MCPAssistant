import json
from pathlib import Path
from unittest.mock import Mock, patch
import pytest

from src.catalog.manager import CatalogManager
from src.catalog.models import AppEntry, LaunchType, make_stable_identity
from src.catalog.scanner import SoftwareScanner
from src.catalog.store_apps import StoreAppRecord


def test_legacy_alias_and_exe_path_migrate_to_v2(tmp_path):
    exe = tmp_path / "editor.exe"
    exe.write_bytes(b"MZ")
    entry = AppEntry(
        app_id="legacy",
        display_name="Editor",
        alias="my editor",
        exe_path=str(exe),
        source="Manual",
        is_enabled=True,
    )
    assert entry.aliases == ["my editor"]
    assert entry.launch_type == LaunchType.PORTABLE
    assert entry.launch_target == str(exe)
    assert entry.is_user_managed is True
    assert entry.stable_identity == make_stable_identity(
        LaunchType.PORTABLE, str(exe), ""
    )


def test_store_entry_does_not_require_exe_path():
    entry = AppEntry(
        app_id="store-chatgpt",
        display_name="ChatGPT",
        aliases=["ChatGPT", "Codex", "OpenAI Codex", "codeex"],
        launch_type=LaunchType.STORE_AUMID,
        launch_target="OpenAI.Codex_2p2nqsd0c76g0!App",
        stable_identity="store:openai.codex_2p2nqsd0c76g0!app",
        source="AppsFolder",
    )
    assert entry.exe_path == ""
    assert entry.alias == "ChatGPT"
    assert entry.aliases[-1] == "codeex"


def test_model_dump_writes_aliases_but_not_legacy_alias(tmp_path):
    exe = tmp_path / "tool.exe"
    exe.write_bytes(b"MZ")
    entry = AppEntry(
        app_id="tool",
        display_name="Tool",
        alias="tool voice",
        exe_path=str(exe),
        source="Manual",
    )
    dumped = entry.model_dump()
    assert dumped["aliases"] == ["tool voice"]
    assert "alias" not in dumped


def test_default_scan_never_uses_path_or_uninstall_registry():
    with patch.object(SoftwareScanner, "_scan_store_apps"), \
         patch.object(SoftwareScanner, "_scan_app_paths"), \
         patch.object(SoftwareScanner, "_scan_start_menu_shortcuts"), \
         patch.object(SoftwareScanner, "_scan_desktop_shortcuts"), \
         patch.object(SoftwareScanner, "_scan_builtin_tools"), \
         patch.object(SoftwareScanner, "_scan_path_environment") as path_scan, \
         patch.object(SoftwareScanner, "_scan_uninstall_registry") as uninstall_scan:
        SoftwareScanner.scan_all(user_agreed=True)
    path_scan.assert_not_called()
    uninstall_scan.assert_not_called()


def test_store_scan_creates_launchable_aumid_entry():
    entries = {}
    provider = patch(
        "src.catalog.scanner.StoreAppProvider.enumerate_apps",
        return_value=[StoreAppRecord("ChatGPT", "OpenAI.Codex_2p2nqsd0c76g0!App")],
    )
    with provider:
        SoftwareScanner._scan_store_apps(entries)
    app = next(iter(entries.values()))
    assert app.launch_type == LaunchType.STORE_AUMID
    assert app.launch_target == "OpenAI.Codex_2p2nqsd0c76g0!App"
    assert app.exe_path == ""
    assert app.aliases == ["ChatGPT", "Codex", "OpenAI Codex", "codeex"]
    assert app.is_restricted is False
    assert app.is_enabled is False


def _win32_entry(app_id, name, path, source="StartMenu", enabled=False):
    return AppEntry(
        app_id=app_id,
        display_name=name,
        aliases=[name],
        launch_type=LaunchType.WIN32,
        launch_target=str(path),
        exe_path=str(path),
        stable_identity=make_stable_identity(LaunchType.WIN32, str(path)),
        source=source,
        is_enabled=enabled,
        is_valid=True,
    )


def test_successful_rescan_removes_stale_path_and_registry_entries(tmp_path):
    catalog = tmp_path / "catalog.json"
    stale_path = tmp_path / "git.exe"
    stale_registry = tmp_path / "uninst.exe"
    keep = tmp_path / "keep.exe"
    for item in (stale_path, stale_registry, keep):
        item.write_bytes(b"MZ")
    manager = CatalogManager(str(catalog))
    manager.apps = {
        "path": _win32_entry("path", "git", stale_path, "Path", True),
        "registry": _win32_entry("registry", "Old", stale_registry, "Registry", True),
        "keep": _win32_entry("keep", "Keep", keep, "StartMenu", True),
    }
    discovered = [_win32_entry("new-keep", "Keep", keep, "StartMenu", False)]
    with patch.object(SoftwareScanner, "scan_all", return_value=discovered):
        result = manager.refresh_scan(user_agreed=True)
    assert set(manager.apps) == {"keep"}
    assert manager.apps["keep"].is_enabled is True
    assert set(result.removed_ids) == {"path", "registry"}


def test_manual_portable_survives_successful_rescan(tmp_path):
    portable = tmp_path / "portable.exe"
    portable.write_bytes(b"MZ")
    manager = CatalogManager(str(tmp_path / "catalog.json"))
    manual = manager.add_manual_app("Portable", str(portable), alias="portable", authorized=True)
    with patch.object(SoftwareScanner, "scan_all", return_value=[]):
        manager.refresh_scan(user_agreed=True)
    assert manager.apps[manual.app_id].is_enabled is True
    assert manager.apps[manual.app_id].is_user_managed is True


def test_failed_scan_preserves_memory_and_catalog_bytes(tmp_path):
    exe = tmp_path / "keep.exe"
    exe.write_bytes(b"MZ")
    catalog = tmp_path / "catalog.json"
    manager = CatalogManager(str(catalog))
    manager.apps = {"keep": _win32_entry("keep", "Keep", exe, enabled=True)}
    manager.save_catalog()
    before = catalog.read_bytes()
    with patch.object(SoftwareScanner, "scan_all", side_effect=RuntimeError("boom")):
        with pytest.raises(RuntimeError):
            manager.refresh_scan(user_agreed=True)
    assert set(manager.apps) == {"keep"}
    assert catalog.read_bytes() == before


def test_same_name_changed_identity_does_not_inherit_authorization(tmp_path):
    old_exe = tmp_path / "old.exe"
    new_exe = tmp_path / "new.exe"
    old_exe.write_bytes(b"MZ")
    new_exe.write_bytes(b"MZ")
    manager = CatalogManager(str(tmp_path / "catalog.json"))
    manager.apps = {"old": _win32_entry("old", "Player", old_exe, enabled=True)}
    discovered = [_win32_entry("new", "Player", new_exe, enabled=False)]
    with patch.object(SoftwareScanner, "scan_all", return_value=discovered):
        manager.refresh_scan(user_agreed=True)
    assert set(manager.apps) == {"new"}
    assert manager.apps["new"].is_enabled is False


def test_single_and_bulk_authorization_accept_installed_store_app(tmp_path):
    provider = Mock()
    provider.is_installed.return_value = True
    launcher = Mock()
    manager = CatalogManager(
        str(tmp_path / "catalog.json"),
        store_provider=provider,
        store_launcher=launcher,
    )
    app = AppEntry(
        app_id="chatgpt",
        display_name="ChatGPT",
        aliases=["ChatGPT", "Codex"],
        launch_type=LaunchType.STORE_AUMID,
        launch_target="OpenAI.Codex_2p2nqsd0c76g0!App",
        source="AppsFolder",
    )
    manager.apps[app.app_id] = app
    assert manager.set_app_enabled(app.app_id, True) is True
    manager.set_app_enabled(app.app_id, False)
    assert manager.batch_set_enabled(True) == 1


def test_store_launch_revalidates_installation_and_uses_store_launcher(tmp_path):
    provider = Mock()
    provider.is_installed.return_value = True
    launcher = Mock()
    launcher.activate.return_value = 1234
    manager = CatalogManager(
        str(tmp_path / "catalog.json"), provider, launcher
    )
    app = AppEntry(
        app_id="chatgpt",
        display_name="ChatGPT",
        aliases=["ChatGPT", "Codex"],
        launch_type=LaunchType.STORE_AUMID,
        launch_target="OpenAI.Codex_2p2nqsd0c76g0!App",
        source="AppsFolder",
        is_enabled=True,
    )
    manager.apps[app.app_id] = app
    assert manager.launch_app(app) == "软件已成功启动: ChatGPT"
    provider.is_installed.assert_called_with(app.launch_target)
    launcher.activate.assert_called_once_with(app.launch_target)


def test_uninstalled_store_app_is_revoked_and_not_activated(tmp_path):
    provider = Mock()
    provider.is_installed.return_value = False
    launcher = Mock()
    manager = CatalogManager(str(tmp_path / "catalog.json"), provider, launcher)
    app = AppEntry(
        app_id="gone",
        display_name="Gone",
        aliases=["Gone"],
        launch_type=LaunchType.STORE_AUMID,
        launch_target="Vendor.App_family!App",
        source="AppsFolder",
        is_enabled=True,
    )
    manager.apps[app.app_id] = app
    with pytest.raises(FileNotFoundError):
        manager.launch_app(app)
    assert app.is_enabled is False
    assert app.is_valid is False
    launcher.activate.assert_not_called()


def test_single_authorization_rejects_maintenance_exe(tmp_path):
    bad = tmp_path / "uninst.exe"
    bad.write_bytes(b"MZ")
    manager = CatalogManager(str(tmp_path / "catalog.json"))
    app = _win32_entry("bad", "Editor", bad, "AppPaths", False)
    manager.apps[app.app_id] = app
    assert manager.set_app_enabled(app.app_id, True) is False
    assert app.is_valid is False
    assert app.authorization_block_reason


def test_repair_manual_app_changes_target_and_revokes_authorization(tmp_path):
    import copy
    old_exe = tmp_path / "old.exe"
    new_exe = tmp_path / "new.exe"
    old_exe.write_bytes(b"MZ")
    new_exe.write_bytes(b"MZ")
    manager = CatalogManager(catalog_file_path=str(tmp_path / "catalog.json"))
    app = manager.add_manual_app("本地播放器", str(old_exe), "播放器", authorized=True)
    old_exe.unlink()

    repaired = manager.repair_manual_app(app.app_id, str(new_exe))

    assert repaired.app_id == app.app_id
    assert repaired.launch_target == str(new_exe)
    assert repaired.is_valid is True
    assert repaired.is_enabled is False
    assert repaired.aliases == ["播放器"]


def test_repair_rejects_automatic_entry(tmp_path):
    exe = tmp_path / "normal.exe"
    exe.write_bytes(b"MZ")
    manager = CatalogManager(catalog_file_path=str(tmp_path / "catalog.json"))
    app = AppEntry(
        app_id="auto",
        display_name="普通应用",
        aliases=["普通应用"],
        launch_type=LaunchType.WIN32,
        launch_target=str(exe),
        source="AppPaths",
    )
    manager.apps[app.app_id] = app
    with pytest.raises(PermissionError, match="仅允许修复手动添加的应用"):
        manager.repair_manual_app(app.app_id, str(exe))


def test_repair_rolls_back_memory_when_save_fails(tmp_path, monkeypatch):
    import copy
    old_exe = tmp_path / "old.exe"
    new_exe = tmp_path / "new.exe"
    old_exe.write_bytes(b"MZ")
    new_exe.write_bytes(b"MZ")
    manager = CatalogManager(catalog_file_path=str(tmp_path / "catalog.json"))
    app = manager.add_manual_app("工具", str(old_exe), authorized=False)
    before = copy.deepcopy(manager.apps)
    monkeypatch.setattr(manager, "save_catalog", lambda: (_ for _ in ()).throw(OSError("disk")))
    with pytest.raises(OSError):
        manager.repair_manual_app(app.app_id, str(new_exe))
    assert manager.apps == before


def test_display_name_prefers_file_description_then_product_then_stem():
    from src.catalog.windows_metadata import choose_display_name
    assert choose_display_name("tool", "Tool Editor", "Tool Suite") == "Tool Editor"
    assert choose_display_name("tool", "", "Tool Suite") == "Tool Suite"
    assert choose_display_name("tool", "", "") == "tool"


def test_close_app_uses_same_is_enabled_authorization(tmp_path):
    """验证 close_app 严格使用 is_enabled 单一授权状态：未授权时拒绝，授权后允许"""
    exe = tmp_path / "app.exe"
    exe.write_bytes(b"MZ")
    manager = CatalogManager(catalog_file_path=str(tmp_path / "catalog.json"))
    app = manager.add_manual_app("测试应用", str(exe), alias="testapp", authorized=False)

    # 1. 未授权状态：启动和关闭均被拒绝
    with pytest.raises(PermissionError, match="尚未被用户授权"):
        manager.launch_app(app)
    with pytest.raises(PermissionError, match="尚未被用户授权"):
        manager.close_app(app)

    # 2. 授权状态：启动和关闭均通过授权校验（注入假 API 验证关闭调用）
    manager.set_app_enabled(app.app_id, True)
    from tests.test_window_control import FakeWindowApi
    from src.catalog.window_control import WindowTarget
    fake_api = FakeWindowApi([
        WindowTarget(hwnd=1001, pid=5555, process_path=str(exe)),
    ])
    manager.window_controller.api = fake_api

    res = manager.close_app(app)
    assert res.status.value == "ALL_CLOSED"
    assert fake_api.post_message_calls == [(1001, 0x0010)]

    # 3. 再次取消授权：关闭再次被拒绝
    manager.set_app_enabled(app.app_id, False)
    with pytest.raises(PermissionError, match="尚未被用户授权"):
        manager.close_app(app)


