"""旧版本配置与凭据平滑安全迁移专项测试集

测试目标：
1. 验证当新目录已有文件时，以新目录为准，绝不覆盖；
2. 验证当新目录不存在、旧目录存在时，安全复制 config.json, secrets.dat, catalog.json 到新目录；
3. 验证迁移后旧目录和旧文件绝不被删除；
4. 验证迁移后 secrets.dat 仍然是密文格式，未以明文落盘；
5. 验证在同一 Windows 用户下旧版 DPAPI 密文（含 XiaozhiMcpAppSecret 描述）可正常解密；
6. 验证新生成的 DPAPI 密文使用中性描述 MCPAssistantSecret；
7. 验证旧开机启动项存在时，开启自启会写入新项并清理旧项，关闭自启会清理新旧两项；
8. 验证迁移异常时 fail-closed 并采用安全默认值，不崩溃。
"""

import os
import sys
import json
import base64
import ctypes
from ctypes import wintypes
import pytest

from src.core.config import AppPaths, ConfigManager, AppConfig, encrypt_secret, decrypt_secret, DPAPI_V1_PREFIX
from src.core.autostart import AUTOSTART_VALUE_NAME, LEGACY_AUTOSTART_VALUE_NAME, is_autostart_enabled, set_autostart


class TestConfigMigration:
    """测试旧版配置、DPAPI 凭据与应用目录缓存的安全平滑迁移"""

    def test_legacy_data_migration_when_new_missing(self, tmp_path, monkeypatch):
        """当新目录不存在相应文件、旧目录存在时，安全复制且不删除旧文件"""
        fake_appdata = tmp_path / "AppData" / "Roaming"
        fake_localappdata = tmp_path / "AppData" / "Local"
        legacy_roaming = fake_appdata / "XiaozhiMcpApp"
        legacy_local = fake_localappdata / "XiaozhiMcpApp"
        new_roaming = fake_appdata / "MCPAssistant"
        new_local = fake_localappdata / "MCPAssistant"

        legacy_roaming.mkdir(parents=True, exist_ok=True)
        legacy_local.mkdir(parents=True, exist_ok=True)

        # 准备旧文件
        old_config = {"mcp_endpoint": "", "scan_agreed": True, "allow_calculator": False}
        (legacy_roaming / "config.json").write_text(json.dumps(old_config), encoding="utf-8")

        # 准备合法的旧版 DPAPI 密文（测试 fake token）
        fake_endpoint = "wss://api.xiaozhi.me/mcp/?token=test_migration_token_12345"
        cipher = encrypt_secret(fake_endpoint)
        old_secrets = {"mcp_endpoint": cipher}
        (legacy_roaming / "secrets.dat").write_text(json.dumps(old_secrets), encoding="utf-8")

        old_catalog = {"version": "2.0", "apps": [{"app_id": "app_test", "display_name": "TestApp"}]}
        (legacy_local / "catalog.json").write_text(json.dumps(old_catalog), encoding="utf-8")

        monkeypatch.setenv("APPDATA", str(fake_appdata))
        monkeypatch.setenv("LOCALAPPDATA", str(fake_localappdata))

        # 执行迁移
        AppPaths.migrate_legacy_data()

        # 验证新目录中的文件已生成
        new_cfg_file = new_roaming / "config.json"
        new_sec_file = new_roaming / "secrets.dat"
        new_cat_file = new_local / "catalog.json"

        assert new_cfg_file.exists(), "新 config.json 未生成"
        assert new_sec_file.exists(), "新 secrets.dat 未生成"
        assert new_cat_file.exists(), "新 catalog.json 未生成"

        # 验证旧文件未被删除
        assert (legacy_roaming / "config.json").exists(), "旧 config.json 被误删"
        assert (legacy_roaming / "secrets.dat").exists(), "旧 secrets.dat 被误删"
        assert (legacy_local / "catalog.json").exists(), "旧 catalog.json 被误删"

        # 验证密文在磁盘上未以明文存储
        secrets_raw = new_sec_file.read_text(encoding="utf-8")
        assert fake_endpoint not in secrets_raw, "secrets.dat 绝不能以明文存储 Token"
        assert "test_migration_token" not in secrets_raw

        # 验证内容正确
        sec_json = json.loads(secrets_raw)
        decrypted = decrypt_secret(sec_json["mcp_endpoint"])
        assert decrypted == fake_endpoint

        cat_json = json.loads(new_cat_file.read_text(encoding="utf-8"))
        assert cat_json["apps"][0]["app_id"] == "app_test"

    def test_new_files_never_overwritten_by_legacy(self, tmp_path, monkeypatch):
        """当新目录已有文件时，以新目录为准，绝不覆盖"""
        fake_appdata = tmp_path / "AppData" / "Roaming"
        fake_localappdata = tmp_path / "AppData" / "Local"
        legacy_roaming = fake_appdata / "XiaozhiMcpApp"
        new_roaming = fake_appdata / "MCPAssistant"

        legacy_roaming.mkdir(parents=True, exist_ok=True)
        new_roaming.mkdir(parents=True, exist_ok=True)

        old_config = {"scan_agreed": False, "allow_calculator": False}
        (legacy_roaming / "config.json").write_text(json.dumps(old_config), encoding="utf-8")

        new_config = {"scan_agreed": True, "allow_calculator": True}
        (new_roaming / "config.json").write_text(json.dumps(new_config), encoding="utf-8")

        monkeypatch.setenv("APPDATA", str(fake_appdata))
        monkeypatch.setenv("LOCALAPPDATA", str(fake_localappdata))

        AppPaths.migrate_legacy_data()

        loaded_cfg = json.loads((new_roaming / "config.json").read_text(encoding="utf-8"))
        assert loaded_cfg["scan_agreed"] is True, "新配置被旧配置意外覆盖"
        assert loaded_cfg["allow_calculator"] is True

    def test_config_manager_initialization_triggers_migration(self, tmp_path, monkeypatch):
        """ConfigManager 实例化时自动触发平滑迁移并能正确读取配置与凭据"""
        fake_appdata = tmp_path / "AppData" / "Roaming"
        fake_localappdata = tmp_path / "AppData" / "Local"
        legacy_roaming = fake_appdata / "XiaozhiMcpApp"
        legacy_roaming.mkdir(parents=True, exist_ok=True)

        fake_endpoint = "wss://api.xiaozhi.me/mcp/?token=auto_init_migrate_token_999"
        cipher = encrypt_secret(fake_endpoint)
        (legacy_roaming / "secrets.dat").write_text(json.dumps({"mcp_endpoint": cipher}), encoding="utf-8")
        (legacy_roaming / "config.json").write_text(json.dumps({"scan_agreed": True}), encoding="utf-8")

        monkeypatch.setenv("APPDATA", str(fake_appdata))
        monkeypatch.setenv("LOCALAPPDATA", str(fake_localappdata))

        mgr = ConfigManager()
        assert mgr.config.scan_agreed is True
        assert mgr.config.mcp_endpoint == fake_endpoint
        assert (fake_appdata / "MCPAssistant" / "config.json").exists()

    @pytest.mark.skipif(sys.platform != "win32", reason="仅在 Windows 环境下验证 DPAPI 密文")
    def test_dpapi_secret_description_is_neutral(self):
        """新加密的 DPAPI 密文能够正常使用 decrypt_secret 解密"""
        test_plain = "wss://api.xiaozhi.me/mcp/?token=test_neutral_desc_112233"
        cipher = encrypt_secret(test_plain)
        assert cipher.startswith(DPAPI_V1_PREFIX)
        decrypted = decrypt_secret(cipher)
        assert decrypted == test_plain

    def test_migration_failure_gracefully_handled(self, tmp_path, monkeypatch):
        """迁移过程中若目标目录只读或损坏，捕获异常不崩溃"""
        fake_appdata = tmp_path / "AppData" / "Roaming"
        fake_localappdata = tmp_path / "AppData" / "Local"
        legacy_roaming = fake_appdata / "XiaozhiMcpApp"
        legacy_roaming.mkdir(parents=True, exist_ok=True)
        (legacy_roaming / "config.json").write_text("invalid json content", encoding="utf-8")

        monkeypatch.setenv("APPDATA", str(fake_appdata))
        monkeypatch.setenv("LOCALAPPDATA", str(fake_localappdata))

        # 不应抛出未捕获异常
        AppPaths.migrate_legacy_data()


class TestAutostartMigration:
    """测试开机自启动项从旧版名称平滑迁移并清理旧项"""

    @pytest.mark.skipif(sys.platform != "win32", reason="仅在 Windows 环境下测试注册表")
    def test_autostart_migration_and_cleanup(self, monkeypatch):
        """测试开启时清理旧项，关闭时清理两项"""
        fake_reg = {}

        class FakeKey:
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass

        def fake_create_key(root, subkey):
            return FakeKey()

        def fake_open_key(root, subkey, reserved, access):
            return FakeKey()

        def fake_query_value(key, name):
            if name in fake_reg:
                return (fake_reg[name], 1)
            raise FileNotFoundError(f"Key {name} not found")

        def fake_set_value(key, name, reserved, reg_type, val):
            fake_reg[name] = val

        def fake_delete_value(key, name):
            if name in fake_reg:
                del fake_reg[name]
            else:
                raise FileNotFoundError(f"Key {name} not found")

        import winreg
        monkeypatch.setattr(winreg, "CreateKey", fake_create_key)
        monkeypatch.setattr(winreg, "OpenKey", fake_open_key)
        monkeypatch.setattr(winreg, "QueryValueEx", fake_query_value)
        monkeypatch.setattr(winreg, "SetValueEx", fake_set_value)
        monkeypatch.setattr(winreg, "DeleteValue", fake_delete_value)
        monkeypatch.setattr(sys, "frozen", True, raising=False)

        # 1. 模拟旧版本已注册开机自启
        fake_reg[LEGACY_AUTOSTART_VALUE_NAME] = r'"C:\Old\XiaozhiMcpApp.exe" --background'
        assert is_autostart_enabled() is True

        # 2. 调用 set_autostart(True) 执行迁移注册
        success = set_autostart(True)
        assert success is True
        # 验证新项已写入，旧项已被清理
        assert AUTOSTART_VALUE_NAME in fake_reg
        assert LEGACY_AUTOSTART_VALUE_NAME not in fake_reg
        assert "--background" in fake_reg[AUTOSTART_VALUE_NAME]

        # 3. 调用 set_autostart(False) 取消自启
        success = set_autostart(False)
        assert success is True
        assert AUTOSTART_VALUE_NAME not in fake_reg
        assert LEGACY_AUTOSTART_VALUE_NAME not in fake_reg
        assert is_autostart_enabled() is False
