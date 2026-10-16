"""设置控制器测试用例。

验证事务一致性、开机自启注册表同步、失败回滚及白名单字段保护。
"""

from unittest.mock import MagicMock, patch

from src.core.config import AppConfig
from src.ui_qt.controllers.settings_controller import SettingsController


def test_autostart_registry_failure_does_not_save_config(qtbot):
    """开机自启写入注册表失败时，不应保存配置。"""
    config = MagicMock()
    config.load_config.return_value = AppConfig(auto_start_with_windows=False)
    controller = SettingsController(
        config,
        get_autostart=lambda: False,
        set_autostart=lambda value: False,
    )
    with qtbot.waitSignal(controller.save_failed, timeout=1000):
        controller.save({"auto_start_with_windows": True})
    config.save_config.assert_not_called()


def test_config_failure_rolls_registry_back(qtbot):
    """保存配置失败时，应将注册表自启状态回滚。"""
    calls = []
    config = MagicMock()
    config.load_config.return_value = AppConfig(auto_start_with_windows=False)
    config.save_config.side_effect = OSError("disk")
    controller = SettingsController(
        config,
        get_autostart=lambda: False,
        set_autostart=lambda value: calls.append(value) or True,
    )
    with qtbot.waitSignal(controller.save_failed, timeout=1000):
        controller.save({"auto_start_with_windows": True})
    assert calls == [True, False]


def test_success_updates_only_supported_user_fields(qtbot):
    """成功保存时，仅更新白名单中的普通用户字段，忽略敏感或内部字段。"""
    config = MagicMock()
    config.load_config.return_value = AppConfig()
    controller = SettingsController(config, lambda: False, lambda value: True)
    with qtbot.waitSignal(controller.saved, timeout=1000):
        controller.save({"allow_run_apps": False, "mcp_endpoint": "ignored"})
    saved = config.save_config.call_args.args[0]
    assert saved.allow_run_apps is False
    assert saved.mcp_endpoint == ""


def test_file_write_requires_an_existing_user_selected_folder(qtbot, tmp_path):
    """允许写入文件时，必须指定已存在的用户所选目录。"""
    config = MagicMock()
    config.load_config.return_value = AppConfig()
    controller = SettingsController(config, lambda: False, lambda value: True)
    with qtbot.waitSignal(controller.save_failed, timeout=1000) as signal:
        controller.save({"allow_file_write": True, "allowed_write_dirs": []})
    assert signal.args == ["请先选择允许通过 MCP 保存文件的文件夹。"]
    config.save_config.assert_not_called()


def test_load_emits_supported_values(qtbot, tmp_path):
    """加载配置时应发出当前支持的设置值字典。"""
    config = MagicMock()
    test_dir = str(tmp_path)
    config.load_config.return_value = AppConfig(
        auto_start_with_windows=True,
        minimize_to_tray_on_close=False,
        allow_run_apps=True,
        allow_open_url=False,
        allow_file_write=True,
        allowed_write_dirs=[test_dir],
    )
    controller = SettingsController(config, lambda: True, lambda value: True)
    with patch("src.ui_qt.controllers.settings_controller.PathSandbox.is_safe_write_directory", return_value=(True, "")):
        with qtbot.waitSignal(controller.loaded, timeout=1000) as signal:
            controller.load()
    values = signal.args[0]
    assert values["auto_start_with_windows"] is True
    assert values["minimize_to_tray_on_close"] is False
    assert values["allow_run_apps"] is True
    assert values["allow_open_url"] is False
    assert values["allow_file_write"] is True
    assert values["allowed_write_dirs"] == [test_dir]
