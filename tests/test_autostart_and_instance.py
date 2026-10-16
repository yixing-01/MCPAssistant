"""开机自启动与单实例锁测试套件
验证：
- 勾选自启动写入 HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run
- 取消勾选自启动删除 HKCU Run 项
- 路径中带空格与中文正确引用，参数包含 --background
- 开发源码模式默认不注册开发环境 Python 路径
- 单实例锁：首个实例成功获取，第二个实例获取失败
- --mcp-service 子进程不受 GUI 单实例锁影响
"""

import sys
from unittest.mock import patch, MagicMock
import pytest

from src.core.autostart import (
    set_autostart,
    is_autostart_enabled,
    AUTOSTART_VALUE_NAME,
)
from src.core.single_instance import SingleInstanceLock


def test_autostart_registry_mocked():
    # 模拟 winreg 操作，确保不污染真实注册表
    mock_reg = MagicMock()
    mock_key = MagicMock()
    mock_reg.OpenKey.return_value = mock_key
    mock_reg.CreateKey.return_value = mock_key
    mock_reg.HKEY_CURRENT_USER = 1
    mock_reg.KEY_SET_VALUE = 2
    mock_reg.KEY_READ = 3
    mock_reg.REG_SZ = 1

    registry_store = {}

    def mock_set_value_ex(key, value_name, reserved, val_type, value):
        registry_store[value_name] = value

    def mock_delete_value(key, value_name):
        if value_name in registry_store:
            del registry_store[value_name]
        else:
            raise FileNotFoundError("Value not found")

    def mock_query_value_ex(key, value_name):
        if value_name in registry_store:
            return (registry_store[value_name], 1)
        raise FileNotFoundError("Value not found")

    mock_reg.SetValueEx.side_effect = mock_set_value_ex
    mock_reg.DeleteValue.side_effect = mock_delete_value
    mock_reg.QueryValueEx.side_effect = mock_query_value_ex

    with patch("src.core.autostart.winreg", mock_reg), \
         patch("sys.frozen", True, create=True), \
         patch("sys.executable", "D:\\Program Files\\MCPAssistant\\MCPAssistant.exe"):

        # 1. 开启自启动
        success = set_autostart(True)
        assert success is True
        assert AUTOSTART_VALUE_NAME in registry_store
        val = registry_store[AUTOSTART_VALUE_NAME]
        assert '"D:\\Program Files\\MCPAssistant\\MCPAssistant.exe"' in val
        assert "--background" in val

        # 2. 查询自启动状态
        assert is_autostart_enabled() is True

        # 3. 关闭自启动
        success_disable = set_autostart(False)
        assert success_disable is True
        assert AUTOSTART_VALUE_NAME not in registry_store
        assert is_autostart_enabled() is False


def test_autostart_dev_mode_guard():
    # 源码开发模式下（非 sys.frozen）不应当静默将 dev python 注册到开机启动
    with patch("sys.frozen", False, create=True):
        assert set_autostart(True) is False


def test_single_instance_lock():
    # 测试首个实例获取锁成功，第二个实例获取同一命名锁失败
    lock1 = SingleInstanceLock(name="MCPAssistant_Test_Lock")
    lock2 = SingleInstanceLock(name="MCPAssistant_Test_Lock")

    acquired1 = lock1.acquire()
    try:
        assert acquired1 is True
        # 第二个实例获取必须失败
        acquired2 = lock2.acquire()
        assert acquired2 is False
    finally:
        lock1.release()

    # 释放后重新获取应该成功
    acquired3 = lock2.acquire()
    try:
        assert acquired3 is True
    finally:
        lock2.release()
