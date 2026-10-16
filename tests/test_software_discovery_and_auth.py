"""软件自动发现、两级明确授权与安全启动机制全量回归测试套件"""

import os
import sys
import tempfile
import tkinter as tk
from unittest.mock import MagicMock, patch
import pytest

from src.catalog.models import AppEntry
from src.catalog.scanner import (
    SoftwareScanner,
    MAX_SHORTCUT_SCAN_DEPTH,
    MAX_PATH_DIRS,
    MAX_PATH_TOTAL_APPS
)
from src.catalog.manager import CatalogManager
from src.catalog.shortcut_resolver import ShortcutResolver, ShortcutInfo
from src.core.config import ConfigManager, AppConfig
from src.mcp_server.tools import register_mcp_tools
from mcp.server.fastmcp import FastMCP


# -------------------------------------------------------------------------------------------------
# 1. 未同意扫描时，各扫描子函数完全不会执行
# -------------------------------------------------------------------------------------------------
def test_scan_subfunctions_never_executed_when_not_agreed():
    """未同意扫描时，所有内部扫描子函数绝不执行"""
    with patch.object(SoftwareScanner, "_scan_store_apps") as m0, \
         patch.object(SoftwareScanner, "_scan_app_paths") as m1, \
         patch.object(SoftwareScanner, "_scan_uninstall_registry") as m2, \
         patch.object(SoftwareScanner, "_scan_start_menu_shortcuts") as m3, \
         patch.object(SoftwareScanner, "_scan_desktop_shortcuts") as m4, \
         patch.object(SoftwareScanner, "_scan_builtin_tools") as m5, \
         patch.object(SoftwareScanner, "_scan_path_environment") as m6:

        res = SoftwareScanner.scan_all(user_agreed=False)
        assert res == []
        m0.assert_not_called()
        m1.assert_not_called()
        m2.assert_not_called()
        m3.assert_not_called()
        m4.assert_not_called()
        m5.assert_not_called()
        m6.assert_not_called()


# -------------------------------------------------------------------------------------------------
# 2. 首次向导扫描同意与批量授权默认未勾选
# -------------------------------------------------------------------------------------------------
def test_wizard_checkboxes_default_unchecked(tmp_path):
    """新发现的软件授权在首次向导中必须默认不勾选（Qt UI 交互详见 tests/qt/test_onboarding_dialog.py）"""
    from src.catalog.models import AppEntry, LaunchType
    entry = AppEntry(
        app_id="test",
        display_name="测试应用",
        aliases=["测试应用"],
        launch_type=LaunchType.WIN32,
        launch_target="C:/test.exe",
        source="StartMenu",
        is_enabled=False
    )
    assert entry.is_enabled is False, "新应用条目默认必须处于未授权状态"


# -------------------------------------------------------------------------------------------------
# 3. 新扫描的软件默认未授权
# -------------------------------------------------------------------------------------------------
def test_newly_discovered_apps_default_unauthorized(tmp_path):
    """新发现的软件条目 is_enabled 必须默认等于 False"""
    fake_exe = tmp_path / "calc_tool.exe"
    fake_exe.write_text("mock")

    entries = {}
    SoftwareScanner._add_or_merge(
        entries=entries,
        name="计算工具",
        exe_path=str(fake_exe),
        source="Test"
    )

    assert len(entries) == 1
    entry = list(entries.values())[0]
    assert entry.is_enabled is False, "新发现的软件必须默认为未授权状态"
    assert entry.is_valid is True


# -------------------------------------------------------------------------------------------------
# 4. 重新扫描保留旧条目的授权状态和别名
# -------------------------------------------------------------------------------------------------
def test_rescan_preserves_existing_authorization_and_alias(tmp_path):
    """重新扫描时，已存在的软件条目必须保留原有的授权启用状态与用户别名"""
    cat_file = tmp_path / "catalog.json"
    manager = CatalogManager(catalog_file_path=str(cat_file))

    fake_exe = tmp_path / "editor.exe"
    fake_exe.write_text("mock")

    # 手工添加并授权该软件，设置自定义别名
    original_entry = manager.add_manual_app("文本编辑器", str(fake_exe), alias="my_editor", authorized=True)
    assert original_entry.is_enabled is True
    assert original_entry.alias == "my_editor"

    # 模拟扫描重新发现了此文件（但扫描器自身生成的是未授权条目）
    scanned_entry = AppEntry(
        app_id=SoftwareScanner._generate_app_id("文本编辑器", str(fake_exe).lower()),
        display_name="文本编辑器",
        alias="文本编辑器",
        exe_path=str(fake_exe),
        source="StartMenu",
        is_enabled=False,
        is_valid=True
    )

    with patch.object(SoftwareScanner, "scan_all", return_value=[scanned_entry]):
        manager.refresh_scan(user_agreed=True)

    # 验证原条目授权状态与别名未被冲掉
    retrieved = manager.apps[original_entry.app_id]
    assert retrieved.is_enabled is True, "已授权条目在重新扫描后被错误重置为未授权"
    assert retrieved.alias == "my_editor", "用户别名在重新扫描后丢失"


# -------------------------------------------------------------------------------------------------
# 5. 软件名称变化但路径相同时不会产生重复条目
# -------------------------------------------------------------------------------------------------
def test_same_path_different_name_no_duplicate_entries(tmp_path):
    """真实物理路径相同时，即使扫描报告的名称变化，也不得产生重复条目或丢失授权"""
    cat_file = tmp_path / "catalog.json"
    manager = CatalogManager(catalog_file_path=str(cat_file))

    fake_exe = tmp_path / "player.exe"
    fake_exe.write_text("mock")

    # 现有条目名称为 "播放器 旧版"，已授权
    entry = manager.add_manual_app("播放器 旧版", str(fake_exe), alias="player", authorized=True)
    assert len(manager.apps) == 1

    # 扫描发现相同路径的新名称 "播放器 2026 新版"
    new_app_id = SoftwareScanner._generate_app_id("播放器 2026 新版", str(fake_exe).lower())
    scanned_entry = AppEntry(
        app_id=new_app_id,
        display_name="播放器 2026 新版",
        alias="播放器 2026 新版",
        exe_path=str(fake_exe),
        source="Registry",
        is_enabled=False,
        is_valid=True
    )

    with patch.object(SoftwareScanner, "scan_all", return_value=[scanned_entry]):
        manager.refresh_scan(user_agreed=True)

    # 必须去重保持唯一条目，且授权仍为 True
    assert len(manager.apps) == 1
    sole_entry = list(manager.apps.values())[0]
    assert sole_entry.is_enabled is True
    assert sole_entry.alias == "player"


# -------------------------------------------------------------------------------------------------
# 6. 安装目录不会递归扫描（绝不调用 os.walk）
# -------------------------------------------------------------------------------------------------
def test_uninstall_location_does_not_call_os_walk(tmp_path):
    """扫描 InstallLocation 时严禁使用递归 os.walk()，只能使用非递归 scandir 检查第一层"""
    install_dir = tmp_path / "VendorApp"
    install_dir.mkdir()
    sub_dir = install_dir / "subdir"
    sub_dir.mkdir()

    main_exe = install_dir / "VendorApp.exe"
    main_exe.write_text("mock")
    sub_exe = sub_dir / "NestedHidden.exe"
    sub_exe.write_text("mock")

    # 模拟 winreg 返回带有 InstallLocation 的注册表项
    class MockWinReg:
        HKEY_LOCAL_MACHINE = 1
        HKEY_CURRENT_USER = 2
        KEY_READ = 0
        KEY_WOW64_64KEY = 0
        KEY_WOW64_32KEY = 0

        @staticmethod
        def OpenKey(root, path, *args, **kwargs):
            return MagicMock()

        @staticmethod
        def QueryInfoKey(key):
            return (1, 0, 0)

        @staticmethod
        def EnumKey(key, idx):
            return "VendorAppKey"

        @staticmethod
        def QueryValueEx(key, name):
            if name == "DisplayName":
                return ("厂商软件", 1)
            elif name == "InstallLocation":
                return (str(install_dir), 1)
            raise FileNotFoundError

    entries = {}
    with patch("src.catalog.scanner.winreg", MockWinReg), \
         patch("src.catalog.scanner.os.walk") as mock_walk:

        SoftwareScanner._scan_uninstall_registry(entries)

        # 核心断言：严禁调用 os.walk
        mock_walk.assert_not_called()

    # 验证成功识别了第一层的 VendorApp.exe，但没有进入 subdir
    assert len(entries) == 1
    found_exe = list(entries.values())[0].exe_path.lower()
    assert "vendorapp.exe" in found_exe
    assert "nestedhidden.exe" not in found_exe


# -------------------------------------------------------------------------------------------------
# 7. 快捷方式深度限制真正停止遍历（5 层目录测试）
# -------------------------------------------------------------------------------------------------
def test_shortcut_depth_limit_strictly_prunes_dirs(tmp_path):
    """构建 5 层嵌套目录，证明超过 MAX_SHORTCUT_SCAN_DEPTH 的快捷方式不会被遍历发现"""
    # 构造 root/l1/l2/l3/l4/l5 目录
    l1 = tmp_path / "l1"
    l2 = l1 / "l2"
    l3 = l2 / "l3"
    l4 = l3 / "l4"
    l5 = l4 / "l5"
    l5.mkdir(parents=True)

    # 在合法深度（l1，深度为 1）放入快捷方式
    valid_lnk = l1 / "valid_tool.lnk"
    valid_lnk.write_text("mock")

    # 在深度 4（l4）放入快捷方式，已超过 MAX_SHORTCUT_SCAN_DEPTH=3
    deep_lnk = l4 / "deep_hidden.lnk"
    deep_lnk.write_text("mock")

    entries = {}
    SoftwareScanner._scan_shortcuts_in_dir(str(tmp_path), "TestDir", entries)

    found_names = [e.display_name for e in entries.values()]
    assert "valid_tool" in found_names
    assert "deep_hidden" not in found_names, "超过最大扫描深度的快捷方式被错误扫描出来"


# -------------------------------------------------------------------------------------------------
# 8. .url 不进入软件目录
# -------------------------------------------------------------------------------------------------
def test_url_files_rejected_from_catalog(tmp_path):
    """快捷方式扫描严格排除 .url 网页快捷方式，只接受 .lnk"""
    url_file = tmp_path / "baidu.url"
    url_file.write_text("[InternetShortcut]\nURL=https://www.baidu.com")

    lnk_file = tmp_path / "notepad.lnk"
    lnk_file.write_text("mock")

    entries = {}
    SoftwareScanner._scan_shortcuts_in_dir(str(tmp_path), "Desktop", entries)

    paths = [e.exe_path.lower() for e in entries.values()]
    assert any(p.endswith(".lnk") for p in paths)
    assert not any(p.endswith(".url") for p in paths), ".url 网页快捷方式不应进入软件目录"


# -------------------------------------------------------------------------------------------------
# 9. PATH 扫描不递归且只接受 .exe
# -------------------------------------------------------------------------------------------------
def test_path_scanning_non_recursive_and_exe_only(tmp_path):
    """PATH 环境变量扫描只检查目录第一层，严格仅识别 .exe，排除 .bat/.cmd/.ps1 与子目录"""
    path_dir = tmp_path / "bin"
    sub_dir = path_dir / "nested"
    sub_dir.mkdir(parents=True)

    # 第一层文件
    (path_dir / "my_cmd.exe").write_text("mock")
    (path_dir / "run.bat").write_text("@echo off")
    (path_dir / "script.cmd").write_text("@echo off")
    (path_dir / "deploy.ps1").write_text("Write-Host 1")
    (path_dir / "uninstall_tool.exe").write_text("mock")  # 排除项

    # 子目录文件（不应被扫描）
    (sub_dir / "sub_tool.exe").write_text("mock")

    entries = {}
    with patch.dict(os.environ, {"PATH": str(path_dir)}):
        SoftwareScanner._scan_path_environment(entries)

    found_exes = [os.path.basename(e.exe_path).lower() for e in entries.values()]
    assert "my_cmd.exe" in found_exes
    assert "run.bat" not in found_exes
    assert "script.cmd" not in found_exes
    assert "deploy.ps1" not in found_exes
    assert "uninstall_tool.exe" not in found_exes
    assert "sub_tool.exe" not in found_exes, "PATH 扫描错误递归进入了子目录"


# -------------------------------------------------------------------------------------------------
# 10. 手工添加拒绝 .bat, .cmd, .ps1, .url 和目录
# -------------------------------------------------------------------------------------------------
def test_manual_add_rejects_scripts_and_non_executables(tmp_path):
    """后端强制校验：add_manual_app 必须拒绝脚本、网页快捷方式与目录"""
    cat_file = tmp_path / "catalog.json"
    manager = CatalogManager(catalog_file_path=str(cat_file))

    bat_file = tmp_path / "bad.bat"
    bat_file.write_text("@echo off")
    cmd_file = tmp_path / "bad.cmd"
    cmd_file.write_text("@echo off")
    ps1_file = tmp_path / "bad.ps1"
    ps1_file.write_text("Write-Host 1")
    url_file = tmp_path / "bad.url"
    url_file.write_text("mock")

    for f in [bat_file, cmd_file, ps1_file, url_file]:
        with pytest.raises(ValueError, match="仅支持添加 .exe 或 .lnk"):
            manager.add_manual_app("测试脚本", str(f))

    # 尝试添加目录
    test_dir = tmp_path / "somedir"
    test_dir.mkdir()
    with pytest.raises(ValueError, match="不能是文件夹目录"):
        manager.add_manual_app("测试目录", str(test_dir))


# -------------------------------------------------------------------------------------------------
# 11. 未授权软件不能启动
# -------------------------------------------------------------------------------------------------
def test_unauthorized_app_cannot_launch(tmp_path):
    """未授权软件 launch_app 抛出 PermissionError"""
    cat_file = tmp_path / "catalog.json"
    manager = CatalogManager(catalog_file_path=str(cat_file))

    exe_file = tmp_path / "app.exe"
    exe_file.write_text("mock")

    entry = manager.add_manual_app("未授权程序", str(exe_file), authorized=False)
    assert entry.is_enabled is False

    with pytest.raises(PermissionError, match="尚未被用户授权启动"):
        manager.launch_app(entry)


# -------------------------------------------------------------------------------------------------
# 12. 无效路径不能启动
# -------------------------------------------------------------------------------------------------
def test_invalid_path_cannot_launch(tmp_path):
    """物理文件不存在或失效时拒绝启动"""
    cat_file = tmp_path / "catalog.json"
    manager = CatalogManager(catalog_file_path=str(cat_file))

    exe_file = tmp_path / "temp.exe"
    exe_file.write_text("mock")

    entry = manager.add_manual_app("临时程序", str(exe_file), authorized=True)
    # 删除该文件模拟失效
    os.remove(str(exe_file))

    with pytest.raises(FileNotFoundError, match="应用文件不存在|可执行文件不存在"):
        manager.launch_app(entry)


# -------------------------------------------------------------------------------------------------
# 13. 受限条目不能启动
# -------------------------------------------------------------------------------------------------
def test_restricted_entry_cannot_launch(tmp_path):
    """is_restricted=True 条目严格禁止启动，且无法被启用"""
    cat_file = tmp_path / "catalog.json"
    manager = CatalogManager(catalog_file_path=str(cat_file))

    restricted_entry = AppEntry(
        app_id="store_wt",
        display_name="Windows 终端",
        exe_path="wt.exe",
        source="Store",
        is_enabled=False,
        is_valid=False,
        is_restricted=True
    )
    manager.apps[restricted_entry.app_id] = restricted_entry

    # 尝试启用失败
    assert manager.set_app_enabled("store_wt", True) is False

    # 启动拒绝
    with pytest.raises(PermissionError, match="受限/暂不支持类型"):
        manager.launch_app(restricted_entry)


# -------------------------------------------------------------------------------------------------
# 14. 不在正式 catalog 中的伪造 AppEntry 不能启动
# -------------------------------------------------------------------------------------------------
def test_forged_app_entry_cannot_launch(tmp_path):
    """外部伪造或未在 self.apps 注册的 AppEntry 启动抛出 PermissionError"""
    cat_file = tmp_path / "catalog.json"
    manager = CatalogManager(catalog_file_path=str(cat_file))

    forged_entry = AppEntry(
        app_id="forged_id_999",
        display_name="伪造软件",
        exe_path="C:\\Windows\\System32\\cmd.exe",
        source="Hacker",
        is_enabled=True,
        is_valid=True
    )

    with pytest.raises(PermissionError, match="未在白名单目录中注册"):
        manager.launch_app(forged_entry)


# -------------------------------------------------------------------------------------------------
# 15. 已授权 .exe 使用 shell=False 启动
# -------------------------------------------------------------------------------------------------
def test_authorized_exe_launches_with_shell_false(tmp_path):
    """已授权的 .exe 启动时必须使用参数列表并严格保持 shell=False"""
    cat_file = tmp_path / "catalog.json"
    manager = CatalogManager(catalog_file_path=str(cat_file))

    safe_exe = tmp_path / "good.exe"
    safe_exe.write_text("mock")

    entry = manager.add_manual_app("安全应用", str(safe_exe), authorized=True)

    with patch("subprocess.Popen") as mock_popen:
        manager.launch_app(entry)
        mock_popen.assert_called_once()
        args, kwargs = mock_popen.call_args
        assert kwargs.get("shell") is False, "启动 .exe 必须保持 shell=False"
        assert args[0] == [str(safe_exe)]


# -------------------------------------------------------------------------------------------------
# 16. 已授权 .lnk 使用预期的 Windows 打开方式
# -------------------------------------------------------------------------------------------------
def test_authorized_lnk_uses_system_startfile(tmp_path):
    """已授权的 .lnk 快捷方式启动时在 Windows 上使用 os.startfile"""
    cat_file = tmp_path / "catalog.json"
    manager = CatalogManager(catalog_file_path=str(cat_file))

    safe_exe = tmp_path / "good.exe"
    safe_exe.write_text("mock")
    safe_lnk = tmp_path / "good.lnk"
    safe_lnk.write_text("mock")

    with patch.object(ShortcutResolver, "resolve", return_value=ShortcutInfo(
        shortcut_path=str(safe_lnk),
        target_path=str(safe_exe),
        arguments="",
        working_directory="",
        is_resolved=True
    )):
        entry = manager.add_manual_app("快捷方式应用", str(safe_lnk), authorized=True)

        if sys.platform == "win32":
            with patch("os.startfile") as mock_startfile:
                manager.launch_app(entry)
                mock_startfile.assert_called_once_with(str(safe_lnk))
        else:
            with patch("subprocess.Popen") as mock_popen:
                manager.launch_app(entry)
                mock_popen.assert_called_once()


# -------------------------------------------------------------------------------------------------
# 17. 批量授权不会启用无效或受限条目
# -------------------------------------------------------------------------------------------------
def test_batch_authorize_skips_invalid_and_restricted(tmp_path):
    """一键授权全部有效软件时，受限条目、不存在的文件或无效项绝不被启用"""
    cat_file = tmp_path / "catalog.json"
    manager = CatalogManager(catalog_file_path=str(cat_file))

    valid_exe = tmp_path / "valid.exe"
    valid_exe.write_text("mock")
    ghost_exe = tmp_path / "ghost.exe"  # 不创建此文件

    e1 = manager.add_manual_app("有效软件", str(valid_exe), authorized=False)
    e2 = AppEntry(
        app_id="ghost_app",
        display_name="幽灵软件",
        exe_path=str(ghost_exe),
        source="Test",
        is_enabled=False,
        is_valid=False
    )
    e3 = AppEntry(
        app_id="store_app",
        display_name="Store软件",
        exe_path="dummy.exe",
        source="Store",
        is_enabled=False,
        is_valid=False,
        is_restricted=True
    )
    manager.apps[e2.app_id] = e2
    manager.apps[e3.app_id] = e3

    # 执行批量授权
    count = manager.batch_set_enabled(authorized=True)
    assert count == 1, "批量授权计数应仅包含 1 个有效条目"
    assert manager.apps[e1.app_id].is_enabled is True
    assert manager.apps[e2.app_id].is_enabled is False
    assert manager.apps[e3.app_id].is_enabled is False


# -------------------------------------------------------------------------------------------------
# 18. 批量取消后 MCP 工具立即拒绝启动
# -------------------------------------------------------------------------------------------------
def test_batch_revoke_immediately_blocks_mcp_launch(tmp_path):
    """批量取消授权后，MCP 软件启动工具立即拒绝启动对应软件"""
    cat_file = tmp_path / "catalog.json"
    cfg_file = tmp_path / "config.json"
    secrets_file = tmp_path / "secrets.dat"

    cfg_mgr = ConfigManager()
    cfg_mgr.config_path = str(cfg_file)
    cfg_mgr.secrets_path = str(secrets_file)
    cfg_mgr.config = AppConfig(allow_run_apps=True)

    cat_mgr = CatalogManager(catalog_file_path=str(cat_file))
    mcp = FastMCP("TestMcp")
    register_mcp_tools(mcp=mcp, config_mgr=cfg_mgr, catalog_mgr=cat_mgr)

    launch_tool = None
    for t in mcp._tool_manager.list_tools():
        if t.name == "运行电脑端软件文件或程序":
            launch_tool = t.fn
            break
    assert launch_tool is not None

    test_exe = tmp_path / "calc.exe"
    test_exe.write_text("mock")
    cat_mgr.add_manual_app("计算器", str(test_exe), alias="calc", authorized=True)

    # 1. 授权状态下可成功检索
    assert cat_mgr.find_authorized_app("calc") is not None

    # 2. 批量取消授权
    cat_mgr.batch_set_enabled(authorized=False)

    # 3. MCP 工具立即拒绝启动
    res = launch_tool(program_name="calc")
    assert res["是否成功"] is False
    assert "未找到" in res["错误请检查路径"] or "未被用户授权" in res["错误请检查路径"]
