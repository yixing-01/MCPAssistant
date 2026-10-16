"""剪映主程序识别、快捷方式安全参数白名单、软件目录虚高治理与旧目录清理专项测试。
遵循严格 TDD 原则：先验证失败红灯，再实施最小修复。
测试全程使用临时隔离目录，绝不读取、改写用户真实配置与原版目录，绝不真实启动外部程序。
"""

import os
import sys
import json
from unittest.mock import patch, MagicMock
import pytest

from src.catalog.models import AppEntry
from src.catalog.scanner import SoftwareScanner, is_main_program_candidate
from src.catalog.shortcut_resolver import ShortcutResolver, ShortcutInfo, ShortcutValidationResult
from src.catalog.manager import CatalogManager, LookupStatus


# ==========================================
# 1. 统一主程序候选过滤规则测试
# ==========================================

def test_main_app_candidate_filter_maintenance_and_path(tmp_path):
    """验证集中式 is_main_program_candidate 规则：准确拦截卸载器、安装器、更新器和临时路径，放行合法主程序。"""
    # 1. 拦截各类卸载程序
    assert not is_main_program_candidate(r"C:\Program Files\JianyingPro\uninst.exe")
    assert not is_main_program_candidate(r"C:\App\unins000.exe")
    assert not is_main_program_candidate(r"C:\App\uninstall.exe")
    assert not is_main_program_candidate(r"C:\App\uninstall_helper.exe")

    # 2. 拦截安装与升级维护程序
    assert not is_main_program_candidate(r"C:\Downloads\setup.exe")
    assert not is_main_program_candidate(r"C:\App\Setup_v1.0.exe")
    assert not is_main_program_candidate(r"C:\App\my_installer.exe")
    assert not is_main_program_candidate(r"C:\App\updater.exe")
    assert not is_main_program_candidate(r"C:\App\app_update.exe")
    assert not is_main_program_candidate(r"C:\App\repair.exe")
    assert not is_main_program_candidate(r"C:\App\RemoveAccess.exe")
    assert not is_main_program_candidate(r"C:\App\downloader.exe")
    assert not is_main_program_candidate(r"C:\App\diffupgrade.exe")

    # 3. 拦截辅助与后台报告进程
    assert not is_main_program_candidate(r"C:\App\crashreporter.exe")
    assert not is_main_program_candidate(r"C:\App\crash_report.exe")
    assert not is_main_program_candidate(r"C:\App\feedback.exe")
    assert not is_main_program_candidate(r"C:\App\helper.exe")
    assert not is_main_program_candidate(r"C:\App\tray.exe")
    assert not is_main_program_candidate(r"C:\App\daemon.exe")
    assert not is_main_program_candidate(r"C:\App\service.exe")
    assert not is_main_program_candidate(r"C:\App\bootstrap.exe")

    # 4. 拦截敏感与临时下载路径
    assert not is_main_program_candidate(r"C:\ProgramData\Package Cache\install.exe")
    assert not is_main_program_candidate(r"C:\Users\User\AppData\Local\Temp\mytool.exe")
    assert not is_main_program_candidate(r"C:\Users\User\Downloads\tool.exe")

    # 5. 放行合法普通主程序
    assert is_main_program_candidate(r"D:\Program Files (x86)\JianyingPro\JianyingPro.exe")
    assert is_main_program_candidate(r"C:\Program Files\Netease\CloudMusic\cloudmusic.exe")
    assert is_main_program_candidate(r"C:\Windows\System32\notepad.exe")
    assert is_main_program_candidate(r"C:\Program Files\Google\Chrome\Application\chrome.exe")


# ==========================================
# 2. 剪映快捷方式参数安全白名单校验测试
# ==========================================

def test_jianying_shortcut_whitelist_args_allowed(tmp_path):
    """剪映主程序快捷方式携带 --src1 或 --src3 或 --srcN (1-3位数字) 白名单参数时，允许通过安全校验。"""
    jianying_exe = tmp_path / "JianyingPro.exe"
    jianying_exe.write_text("mock")
    lnk_file = tmp_path / "剪映专业版.lnk"
    lnk_file.write_text("mock")

    for allowed_arg in ["--src1", "--src3", "--src99", "--src123"]:
        info = ShortcutInfo(
            shortcut_path=str(lnk_file),
            target_path=str(jianying_exe),
            arguments=allowed_arg,
            is_resolved=True
        )
        res = ShortcutResolver.validate(str(lnk_file), info=info)
        assert res.is_allowed is True, f"参数 {allowed_arg} 应该被白名单允许，实际被拒绝: {res.block_reason}"
        assert res.block_reason == ""


def test_jianying_shortcut_rejects_invalid_args_and_spoofing(tmp_path):
    """非剪映程序携带 --srcN，或者剪映携带非法参数、注入字符时，必须被严格拒绝。"""
    jianying_exe = tmp_path / "JianyingPro.exe"
    jianying_exe.write_text("mock")
    notepad_exe = tmp_path / "notepad.exe"
    notepad_exe.write_text("mock")
    lnk_file = tmp_path / "test.lnk"
    lnk_file.write_text("mock")

    # 1. 非剪映程序即使参数完全匹配 --src1 也必须严格拒绝
    info_spoof = ShortcutInfo(
        shortcut_path=str(lnk_file),
        target_path=str(notepad_exe),
        arguments="--src1",
        is_resolved=True
    )
    res_spoof = ShortcutResolver.validate(str(lnk_file), info=info_spoof)
    assert res_spoof.is_allowed is False
    assert "启动参数" in res_spoof.block_reason

    # 2. 剪映程序携带畸变或恶意注入参数必须被严格拒绝
    invalid_args = [
        "--src",            # 无数字
        "--src1234",        # 超过 3 位数字
        "--src3 & calc.exe",# 命令注入
        "--src3 --evil",    # 多参数
        '"--src3"',         # 带引号
        "--src3\n--other",  # 换行注入
        "https://evil.com", # URL
        "-src1",            # 错误前缀
    ]
    for bad_arg in invalid_args:
        info_bad = ShortcutInfo(
            shortcut_path=str(lnk_file),
            target_path=str(jianying_exe),
            arguments=bad_arg,
            is_resolved=True
        )
        res_bad = ShortcutResolver.validate(str(lnk_file), info=info_bad)
        assert res_bad.is_allowed is False, f"非法参数 {bad_arg} 应该被拒绝，实际通过了"


# ==========================================
# 3. 卸载注册表扫描收紧：排除 uninst.exe
# ==========================================

def test_scan_uninstall_registry_rejects_uninst_display_icon(tmp_path):
    """当卸载注册表中的 DisplayIcon 指向 uninst.exe（如剪映的真实卸载项）时，严禁将其作为主程序收录。"""
    app_dir = tmp_path / "JianyingPro"
    app_dir.mkdir()
    uninst_exe = app_dir / "uninst.exe"
    uninst_exe.write_text("mock uninstaller")

    entries = {}

    # 模拟读取注册表项：DisplayName="剪映专业版", DisplayIcon指向 uninst.exe
    class MockWinReg:
        HKEY_LOCAL_MACHINE = 1
        HKEY_CURRENT_USER = 2
        KEY_READ = 1
        KEY_WOW64_64KEY = 0
        KEY_WOW64_32KEY = 0

        @staticmethod
        def OpenKey(root, sub, reserved=0, access=0):
            mock_key = MagicMock()
            mock_key.__enter__.return_value = mock_key
            mock_key.__exit__.return_value = None
            return mock_key

        @staticmethod
        def QueryInfoKey(key):
            return (1, 0, 0)

        @staticmethod
        def EnumKey(key, idx):
            if idx == 0:
                return "JianyingPro"
            raise OSError("No more keys")

        @staticmethod
        def QueryValueEx(key, val_name):
            if val_name == "DisplayName":
                return ("剪映专业版", 1)
            elif val_name == "DisplayIcon":
                return (str(uninst_exe), 1)
            elif val_name == "UninstallString":
                return (str(uninst_exe), 1)
            elif val_name == "SystemComponent":
                return (0, 4)
            raise OSError("Not found")

    with patch("src.catalog.scanner.winreg", MockWinReg):
        SoftwareScanner._scan_uninstall_registry(entries)

    # 核心断言：未收录 uninst.exe，entries 保持为空！
    assert len(entries) == 0, f"uninst.exe 不应被收录进普通软件列表，实际被收录为: {list(entries.keys())}"


# ==========================================
# 4. 普通软件扫描停止默认扫描 PATH 环境变量
# ==========================================

def test_scan_all_does_not_include_path_environment(tmp_path, monkeypatch):
    """默认 scan_all 流程不得再将系统 PATH 环境变量中的命令扫描进普通软件列表。"""
    fake_path_dir = tmp_path / "FakeSystem32"
    fake_path_dir.mkdir()
    cmd1 = fake_path_dir / "ipconfig.exe"
    cmd1.write_text("mock")
    cmd2 = fake_path_dir / "ping.exe"
    cmd2.write_text("mock")

    monkeypatch.setenv("PATH", str(fake_path_dir))

    # mock 掉其他扫描项，仅观察 scan_all 的调用链
    with patch.object(SoftwareScanner, "_scan_app_paths"), \
         patch.object(SoftwareScanner, "_scan_uninstall_registry"), \
         patch.object(SoftwareScanner, "_scan_start_menu_shortcuts"), \
         patch.object(SoftwareScanner, "_scan_desktop_shortcuts"), \
         patch.object(SoftwareScanner, "_scan_builtin_tools"), \
         patch.object(SoftwareScanner, "_scan_path_environment") as mock_scan_path:

        discovered = SoftwareScanner.scan_all(user_agreed=True)

        # 核心断言：_scan_path_environment 绝不能在默认 scan_all 中被调用
        assert not mock_scan_path.called, "SoftwareScanner.scan_all 不得调用 _scan_path_environment"


# ==========================================
# 5. 高可信批量授权收紧测试
# ==========================================

def test_batch_authorize_only_grants_trusted_main_candidates(tmp_path):
    """批量授权只允许授权高可信来源（AppPaths/StartMenu/Desktop/BuiltinTools）且通过主程序候选校验的条目，排除卸载器与 Path 来源。"""
    jianying_exe = tmp_path / "JianyingPro.exe"
    jianying_exe.write_text("mock")
    uninst_exe = tmp_path / "uninst.exe"
    uninst_exe.write_text("mock")
    git_exe = tmp_path / "git.exe"
    git_exe.write_text("mock")
    notepad_exe = tmp_path / "notepad.exe"
    notepad_exe.write_text("mock")

    manager = CatalogManager(catalog_file_path=str(tmp_path / "catalog.json"))

    # 1. 高可信桌面剪映
    app_jy = AppEntry(
        app_id="app_jy",
        display_name="剪映专业版",
        exe_path=str(jianying_exe),
        source="Desktop",
        is_enabled=False,
        is_valid=True
    )
    # 2. 内置记事本
    app_np = AppEntry(
        app_id="app_np",
        display_name="记事本",
        exe_path=str(notepad_exe),
        source="BuiltinTools",
        is_enabled=False,
        is_valid=True
    )
    # 3. 错误收录的卸载程序
    app_uninst = AppEntry(
        app_id="app_uninst",
        display_name="剪映卸载程序",
        exe_path=str(uninst_exe),
        source="Registry",
        is_enabled=False,
        is_valid=True
    )
    # 4. Path 来源工具
    app_path = AppEntry(
        app_id="app_path",
        display_name="git",
        exe_path=str(git_exe),
        source="Path",
        is_enabled=False,
        is_valid=True
    )

    manager.apps = {
        app_jy.app_id: app_jy,
        app_np.app_id: app_np,
        app_uninst.app_id: app_uninst,
        app_path.app_id: app_path,
    }

    # 执行批量授权
    count = manager.batch_set_enabled(authorized=True)

    # 断言：仅 2 个高可信主程序被授权
    assert count == 2, f"应该只授权 2 款高可信主程序，实际授权了 {count} 款"
    assert manager.apps["app_jy"].is_enabled is True
    assert manager.apps["app_np"].is_enabled is True
    assert manager.apps["app_uninst"].is_enabled is False
    assert manager.apps["app_path"].is_enabled is False


# ==========================================
# 6. 加载旧目录自动撤销维护/卸载程序授权
# ==========================================

def test_load_catalog_revokes_legacy_maintenance_entries(tmp_path):
    """加载旧 catalog.json 时，若发现已授权的 uninst.exe 或安装维护程序，自动撤销其授权并标记拦截原因。"""
    uninst_exe = tmp_path / "uninst.exe"
    uninst_exe.write_text("mock uninstaller")

    old_entries = [
        AppEntry(
            app_id="app_legacy_uninst",
            display_name="剪映专业版",
            exe_path=str(uninst_exe),
            source="Registry",
            is_enabled=True,   # 旧版本曾被错误授权
            is_valid=True
        )
    ]

    catalog_path = tmp_path / "catalog.json"
    catalog_path.write_text(
        json.dumps([e.model_dump() for e in old_entries], ensure_ascii=False),
        encoding="utf-8"
    )

    manager = CatalogManager(catalog_file_path=str(catalog_path))

    loaded_app = manager.apps["app_legacy_uninst"]
    # 核心断言：旧 uninst.exe 的授权被强制撤销为 False，并记录拦截原因
    assert loaded_app.is_enabled is False, "旧已授权的 uninst.exe 必须被强制撤销授权"
    assert "维护" in loaded_app.authorization_block_reason or "卸载" in loaded_app.authorization_block_reason
    assert loaded_app.validation_status == "invalid"


# ==========================================
# 7. 启动前终验防御：严禁启动任何维护/卸载程序
# ==========================================

def test_launch_app_blocks_maintenance_program_defense_in_depth(tmp_path):
    """即便条目被意外标记为 is_enabled=True，launch_app 终验必须坚决拦截 uninst.exe 并拒绝启动。"""
    uninst_exe = tmp_path / "uninst.exe"
    uninst_exe.write_text("mock uninstaller")

    catalog_path = tmp_path / "catalog.json"
    manager = CatalogManager(catalog_file_path=str(catalog_path))

    fake_entry = AppEntry(
        app_id="app_bad",
        display_name="恶意伪造或误授权卸载器",
        exe_path=str(uninst_exe),
        source="Manual",
        is_enabled=True,
        is_valid=True
    )
    manager.apps[fake_entry.app_id] = fake_entry

    with patch("subprocess.Popen") as mock_popen, patch("os.startfile") as mock_startfile:
        with pytest.raises(PermissionError) as exc_info:
            manager.launch_app(fake_entry)

        assert "安全拒绝" in str(exc_info.value)
        assert not mock_popen.called
        assert not mock_startfile.called


# ==========================================
# 8. 授权不继承与同目标快捷方式合并
# ==========================================

def test_legacy_uninst_authorization_not_inherited_by_new_jianying(tmp_path):
    """旧 uninst.exe 的授权绝对不能转移或继承给重新发现的 JianyingPro.exe 主程序条目。"""
    uninst_exe = tmp_path / "uninst.exe"
    uninst_exe.write_text("mock")
    jianying_exe = tmp_path / "JianyingPro.exe"
    jianying_exe.write_text("mock")

    catalog_path = tmp_path / "catalog.json"
    manager = CatalogManager(catalog_file_path=str(catalog_path))

    # 旧目录中存在已授权的 uninst.exe
    legacy_app = AppEntry(
        app_id="app_old_uninst",
        display_name="剪映专业版",
        exe_path=str(uninst_exe),
        source="Registry",
        is_enabled=True,
        is_valid=True
    )
    manager.apps[legacy_app.app_id] = legacy_app

    # 扫描发现了新主程序 JianyingPro.exe
    new_app = AppEntry(
        app_id="app_new_jianying",
        display_name="剪映专业版",
        exe_path=str(jianying_exe),
        source="Desktop",
        is_enabled=False,
        is_valid=True
    )

    with patch("src.catalog.manager.SoftwareScanner.scan_all", return_value=[new_app]):
        manager.refresh_scan(user_agreed=True)

    # 核心断言：新剪映主程序必须是未授权（is_enabled=False），旧 uninst 自动条目已被删除或未授权
    assert manager.apps["app_new_jianying"].is_enabled is False
    assert "app_old_uninst" not in manager.apps or manager.apps["app_old_uninst"].is_enabled is False


def test_equivalent_shortcuts_merge_for_jianying(tmp_path):
    """桌面快捷方式（带 --src1）与开始菜单快捷方式（带 --src3）指向同一个 JianyingPro.exe 时，合并为一个有效条目。"""
    jianying_exe = tmp_path / "JianyingPro.exe"
    jianying_exe.write_text("mock")
    lnk_desktop = tmp_path / "desktop_jy.lnk"
    lnk_desktop.write_text("mock")
    lnk_menu = tmp_path / "menu_jy.lnk"
    lnk_menu.write_text("mock")

    def mock_validate(path):
        arg = "--src1" if "desktop" in path else "--src3"
        return ShortcutValidationResult(
            is_allowed=True,
            info=ShortcutInfo(
                shortcut_path=path,
                target_path=str(jianying_exe),
                arguments=arg,
                is_resolved=True
            )
        )

    entries = {}
    with patch("src.catalog.scanner.ShortcutResolver.validate", side_effect=mock_validate):
        SoftwareScanner._add_or_merge(
            entries=entries,
            name="剪映专业版",
            exe_path=str(lnk_desktop),
            source="Desktop"
        )
        SoftwareScanner._add_or_merge(
            entries=entries,
            name="剪映专业版",
            exe_path=str(lnk_menu),
            source="StartMenu"
        )

    # 核心断言：等价目标合并为 1 个条目
    assert len(entries) == 1
    sole = next(iter(entries.values()))
    assert sole.resolved_target == str(jianying_exe)
