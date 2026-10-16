"""软件发现与授权管理器单元测试"""

import os
import tempfile
import pytest
from src.catalog.models import AppEntry
from src.catalog.manager import CatalogManager
from src.catalog.scanner import SoftwareScanner


def test_scan_not_allowed_without_user_agreement():
    """测试未获用户同意前绝不执行扫描"""
    result = SoftwareScanner.scan_all(user_agreed=False)
    assert result == []


def test_add_or_merge_deduplication():
    """测试同一软件多个入口的去重与信息合并"""
    with tempfile.TemporaryDirectory() as temp_dir:
        # 创建一个测试 exe 文件
        app_path = os.path.join(temp_dir, "test_app.exe")
        with open(app_path, "w") as f:
            f.write("mock")

        entries = {}
        # 第一次通过注册表发现
        SoftwareScanner._add_or_merge(
            entries=entries,
            name="测试软件",
            exe_path=app_path,
            source="Registry",
            publisher="测试科技",
            version="1.0"
        )
        assert len(entries) == 1

        # 第二次通过桌面快捷方式发现相同的 exe
        SoftwareScanner._add_or_merge(
            entries=entries,
            name="测试软件桌面快捷方式",
            exe_path=app_path,
            source="Desktop",
            alias="小测"
        )
        # 应合并而非产生重复条目
        assert len(entries) == 1
        item = list(entries.values())[0]
        assert item.publisher == "测试科技"
        assert item.alias == "小测"


def test_path_with_spaces_and_chinese():
    """测试包含空格和中文字符的软件路径解析与验证"""
    with tempfile.TemporaryDirectory() as temp_dir:
        chinese_space_dir = os.path.join(temp_dir, "我的 软件 目录")
        os.makedirs(chinese_space_dir, exist_ok=True)
        exe_file = os.path.join(chinese_space_dir, "小智 播放器.exe")
        with open(exe_file, "w", encoding="utf-8") as f:
            f.write("mock")

        entries = {}
        SoftwareScanner._add_or_merge(
            entries=entries,
            name="小智 播放器",
            exe_path=exe_file,
            source="Manual"
        )
        assert len(entries) == 1
        entry = list(entries.values())[0]
        assert entry.check_validity() is True
        assert os.path.exists(entry.exe_path)


def test_app_invalidity_when_moved_or_deleted():
    """测试软件被移动或卸载后的失效校验"""
    with tempfile.TemporaryDirectory() as temp_dir:
        exe_file = os.path.join(temp_dir, "temporary.exe")
        with open(exe_file, "w") as f:
            f.write("mock")

        entry = AppEntry(
            app_id="app_123",
            display_name="临时程序",
            exe_path=exe_file,
            source="Test",
            is_enabled=True,
            is_valid=True
        )
        assert entry.check_validity() is True

        # 删除模拟被卸载
        os.remove(exe_file)
        assert entry.check_validity() is False
        assert entry.is_valid is False


def test_authorization_and_revocation():
    """测试软件授权及撤销授权后立即生效"""
    with tempfile.TemporaryDirectory() as temp_dir:
        catalog_file = os.path.join(temp_dir, "catalog.json")
        manager = CatalogManager(catalog_file_path=catalog_file)

        exe_file = os.path.join(temp_dir, "my_tool.exe")
        with open(exe_file, "w") as f:
            f.write("mock")

        # 手工添加软件并显式授权
        entry = manager.add_manual_app("实用工具", exe_file, alias="tool", authorized=True)
        assert entry.app_id in manager.apps
        assert entry.is_enabled is True

        # 验证已授权检索
        found = manager.find_authorized_app("tool")
        assert found is not None
        assert found.display_name == "实用工具"

        # 用户撤销授权（禁用）
        manager.set_app_enabled(entry.app_id, False)

        # 撤销后立即无法检索到该软件，拒绝启动
        found_revoked = manager.find_authorized_app("tool")
        assert not found_revoked
        assert found_revoked.status == "NOT_FOUND"

        # 尝试直接启动已禁用的软件抛出权限异常
        with pytest.raises(PermissionError, match="未被用户授权启动"):
            manager.launch_app(entry)


def test_invalid_registry_and_shortcut_tolerance():
    """测试对不存在的路径、损坏条目或无效项的容错性"""
    entries = {}
    # 传入不存在的虚假路径
    SoftwareScanner._add_or_merge(
        entries=entries,
        name="不存在的程序",
        exe_path=r"C:\NonExistent\Folder\ghost.exe",
        source="Registry"
    )
    # 应被安全忽略，不进入有效列表
    assert len(entries) == 0
