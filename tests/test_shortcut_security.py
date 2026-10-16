"""快捷方式安全解析与防护测试（红灯阶段：验证当前实现的漏洞）"""

import os
import sys
import tempfile
from unittest.mock import patch, MagicMock
import pytest

from src.catalog.models import AppEntry
from src.catalog.manager import CatalogManager
from src.catalog.shortcut_resolver import (
    ShortcutResolver,
    ShortcutInfo,
    ShortcutValidationResult,
    DANGEROUS_TARGET_NAMES,
)


def test_safe_no_args_shortcut_allowed(tmp_path):
    """安全、无参数、目标为真实普通 .exe 的快捷方式可以通过校验"""
    fake_exe = tmp_path / "valid_app.exe"
    fake_exe.write_text("mock")
    fake_lnk = tmp_path / "valid_app.lnk"
    fake_lnk.write_text("mock")

    # 模拟解析器返回安全信息
    with patch.object(ShortcutResolver, "resolve", return_value=ShortcutInfo(
        shortcut_path=str(fake_lnk),
        target_path=str(fake_exe),
        arguments="",
        working_directory=str(tmp_path),
        is_resolved=True
    )):
        res = ShortcutResolver.validate(str(fake_lnk))
        assert res.is_allowed is True
        assert res.block_reason == ""


def test_cmd_shortcut_rejected(tmp_path):
    """指向 cmd.exe 的快捷方式即使无参数也必须被拒绝"""
    fake_lnk = tmp_path / "cmd_runner.lnk"
    fake_lnk.write_text("mock")

    with patch.object(ShortcutResolver, "resolve", return_value=ShortcutInfo(
        shortcut_path=str(fake_lnk),
        target_path="C:\\Windows\\System32\\cmd.exe",
        arguments="",
        working_directory="",
        is_resolved=True
    )):
        res = ShortcutResolver.validate(str(fake_lnk))
        assert res.is_allowed is False
        assert "高危系统程序" in res.block_reason or "cmd.exe" in res.block_reason


def test_powershell_shortcut_rejected(tmp_path):
    """指向 powershell.exe / pwsh.exe 的快捷方式必须被拒绝"""
    fake_lnk = tmp_path / "ps_runner.lnk"
    fake_lnk.write_text("mock")

    for ps_name in ["powershell.exe", "pwsh.exe"]:
        with patch.object(ShortcutResolver, "resolve", return_value=ShortcutInfo(
            shortcut_path=str(fake_lnk),
            target_path=f"C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\{ps_name}",
            arguments="",
            working_directory="",
            is_resolved=True
        )):
            res = ShortcutResolver.validate(str(fake_lnk))
            assert res.is_allowed is False
            assert "高危系统程序" in res.block_reason


def test_script_and_directory_target_shortcut_rejected(tmp_path):
    """目标为脚本、目录或 URL 的快捷方式必须被拒绝"""
    fake_lnk = tmp_path / "script.lnk"
    fake_lnk.write_text("mock")

    # 1. 目标为 .bat
    with patch.object(ShortcutResolver, "resolve", return_value=ShortcutInfo(
        shortcut_path=str(fake_lnk),
        target_path=str(tmp_path / "run.bat"),
        arguments="",
        working_directory="",
        is_resolved=True
    )):
        res = ShortcutResolver.validate(str(fake_lnk))
        assert res.is_allowed is False

    # 2. 目标为文件夹目录
    with patch.object(ShortcutResolver, "resolve", return_value=ShortcutInfo(
        shortcut_path=str(fake_lnk),
        target_path=str(tmp_path),
        arguments="",
        working_directory="",
        is_resolved=True
    )):
        res = ShortcutResolver.validate(str(fake_lnk))
        assert res.is_allowed is False


def test_shortcut_with_arguments_rejected(tmp_path):
    """目标为普通 .exe 但带有命令行参数的快捷方式必须被拒绝"""
    fake_exe = tmp_path / "safe.exe"
    fake_exe.write_text("mock")
    fake_lnk = tmp_path / "safe_with_args.lnk"
    fake_lnk.write_text("mock")

    with patch.object(ShortcutResolver, "resolve", return_value=ShortcutInfo(
        shortcut_path=str(fake_lnk),
        target_path=str(fake_exe),
        arguments="--injection-flag",
        working_directory="",
        is_resolved=True
    )):
        res = ShortcutResolver.validate(str(fake_lnk))
        assert res.is_allowed is False
        assert "参数" in res.block_reason


def test_replaced_shortcut_revalidation_fails_at_launch(tmp_path):
    """授权后快捷方式被替换为危险目标，启动前最终核验必须拦截并撤销授权"""
    cat_file = tmp_path / "catalog.json"
    manager = CatalogManager(catalog_file_path=str(cat_file))

    safe_exe = tmp_path / "original_safe.exe"
    safe_exe.write_text("mock")
    target_lnk = tmp_path / "app.lnk"
    target_lnk.write_text("mock")

    # 初始状态下为安全快捷方式并授权
    with patch.object(ShortcutResolver, "resolve", return_value=ShortcutInfo(
        shortcut_path=str(target_lnk),
        target_path=str(safe_exe),
        arguments="",
        working_directory="",
        is_resolved=True
    )):
        entry = manager.add_manual_app("测试应用", str(target_lnk), authorized=True)
        assert entry.is_enabled is True

    # 模拟外部篡改：快捷方式被替换为指向 powershell.exe
    with patch.object(ShortcutResolver, "resolve", return_value=ShortcutInfo(
        shortcut_path=str(target_lnk),
        target_path="C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe",
        arguments="",
        working_directory="",
        is_resolved=True
    )), patch("os.startfile") as mock_start:
        with pytest.raises(PermissionError, match="快捷方式未通过安全校验|安全拒绝"):
            manager.launch_app(entry)
        mock_start.assert_not_called()

        # 检查该条目的授权已被撤销
        reloaded = manager.apps[entry.app_id]
        assert reloaded.is_enabled is False


def test_legacy_unsafe_shortcut_auto_blocked_on_load(tmp_path):
    """旧目录中已授权但不安全的 .lnk 在加载或重新校验后必须自动取消授权"""
    cat_file = tmp_path / "catalog.json"
    fake_lnk = tmp_path / "cmd_legacy.lnk"
    fake_lnk.write_text("mock")

    # 模拟旧 catalog.json 中存储了一个指向 cmd 的已授权 .lnk
    legacy_data = [
        {
            "app_id": "app_legacy_cmd",
            "display_name": "系统终端快捷方式",
            "alias": "cmd",
            "exe_path": str(fake_lnk),
            "source": "Manual",
            "is_enabled": True,
            "is_valid": True,
            "is_restricted": False
        }
    ]
    import json
    with open(cat_file, "w", encoding="utf-8") as f:
        json.dump(legacy_data, f)

    with patch.object(ShortcutResolver, "resolve", return_value=ShortcutInfo(
        shortcut_path=str(fake_lnk),
        target_path="C:\\Windows\\System32\\cmd.exe",
        arguments="",
        working_directory="",
        is_resolved=True
    )):
        manager = CatalogManager(catalog_file_path=str(cat_file))
        manager.revalidate_all()

        app = manager.apps["app_legacy_cmd"]
        assert app.is_enabled is False, "危险旧快捷方式在重新校验后必须被自动禁用"
        assert "高危系统程序" in getattr(app, "authorization_block_reason", "")
