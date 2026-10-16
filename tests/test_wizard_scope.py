"""向导批量授权范围限制与旧状态保护测试"""

import os
import tempfile
from unittest.mock import patch
import pytest

from src.catalog.models import AppEntry
from src.catalog.manager import CatalogManager, ScanResult
from src.catalog.scanner import SoftwareScanner


def test_previously_disabled_apps_remain_disabled_on_wizard_batch(tmp_path):
    """向导批量授权只能授权本次扫描新发现的软件，以前禁用的软件必须保持禁用"""
    cat_file = tmp_path / "catalog.json"
    manager = CatalogManager(catalog_file_path=str(cat_file))

    app1_exe = tmp_path / "app1.exe"
    app1_exe.write_text("mock")
    app2_exe = tmp_path / "app2.exe"
    app2_exe.write_text("mock")

    # 1. 软件 1 原本存在，且被用户明确禁用
    e1 = manager.add_manual_app("应用1", str(app1_exe), authorized=False)
    assert e1.is_enabled is False

    # 2. 扫描发现了软件 1 和全新的软件 2
    e2_app_id = SoftwareScanner._generate_app_id("应用2", str(app2_exe).lower())
    e2 = AppEntry(
        app_id=e2_app_id,
        display_name="应用2",
        exe_path=str(app2_exe),
        source="StartMenu",
        is_enabled=False,
        is_valid=True
    )

    with patch.object(SoftwareScanner, "scan_all", return_value=[e1, e2]):
        scan_res = manager.refresh_scan(user_agreed=True)
        # 必须返回结构化结果，且 new_ids 只包含 e2
        assert hasattr(scan_res, "new_ids")
        assert e2.app_id in scan_res.new_ids
        assert e1.app_id not in scan_res.new_ids

        # 3. 向导仅对本次新发现项执行批量授权
        auth_count = manager.batch_set_enabled(authorized=True, app_ids=scan_res.new_ids)
        assert auth_count == 1

        # 验证结果：应用 1 仍为禁用，应用 2 变为启用
        assert manager.apps[e1.app_id].is_enabled is False, "以前禁用的软件被向导错误重新启用"
        assert manager.apps[e2.app_id].is_enabled is True


def test_manual_disabled_apps_not_re_enabled_by_scan(tmp_path):
    """以前手动添加且禁用的软件，即使扫描器重新扫到了，也绝不被向导批量授权"""
    cat_file = tmp_path / "catalog.json"
    manager = CatalogManager(catalog_file_path=str(cat_file))

    manual_exe = tmp_path / "manual_tool.exe"
    manual_exe.write_text("mock")

    entry = manager.add_manual_app("手工工具", str(manual_exe), authorized=False)
    assert entry.is_enabled is False

    # 扫描重新发现了该文件
    scan_item = AppEntry(
        app_id=entry.app_id,
        display_name="手工工具",
        exe_path=str(manual_exe),
        source="StartMenu",
        is_enabled=False,
        is_valid=True
    )
    with patch.object(SoftwareScanner, "scan_all", return_value=[scan_item]):
        scan_res = manager.refresh_scan(user_agreed=True)
        # 该条目属于已存在项，不能进入 new_ids
        assert entry.app_id not in scan_res.new_ids
        manager.batch_set_enabled(authorized=True, app_ids=scan_res.new_ids)

        assert manager.apps[entry.app_id].is_enabled is False


def test_catalog_view_global_batch_still_skips_invalid_and_unsafe(tmp_path):
    """软件管理页面的全局授权（app_ids=None）仍必须跳过受限、无效及不安全快捷方式"""
    cat_file = tmp_path / "catalog.json"
    manager = CatalogManager(catalog_file_path=str(cat_file))

    safe_exe = tmp_path / "safe.exe"
    safe_exe.write_text("mock")
    e_safe = manager.add_manual_app("安全程序", str(safe_exe), authorized=False)

    e_restricted = AppEntry(
        app_id="store_restricted",
        display_name="Store应用",
        exe_path="wt.exe",
        source="Store",
        is_enabled=False,
        is_valid=False,
        is_restricted=True
    )
    manager.apps[e_restricted.app_id] = e_restricted

    # 全局批量授权
    count = manager.batch_set_enabled(authorized=True, app_ids=None)
    assert count == 1
    assert manager.apps[e_safe.app_id].is_enabled is True
    assert manager.apps[e_restricted.app_id].is_enabled is False
