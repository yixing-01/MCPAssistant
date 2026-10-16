"""用户级开机自启动管理模块
基于 HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run 注册表项实现，无需管理员权限。
仅在打包为正式独立可执行程序时允许注册，包含 --background 参数。
"""

import sys
import os
from typing import Optional
from src.core.logger import app_logger

if sys.platform == "win32":
    import winreg
else:
    winreg = None

AUTOSTART_KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
AUTOSTART_VALUE_NAME = "MCPAssistant"
LEGACY_AUTOSTART_VALUE_NAME = "XiaozhiMcpApp"


def is_autostart_enabled() -> bool:
    """检查当前用户注册表中是否已配置开机自启动（兼顾新项与旧测试版项）"""
    if winreg is None:
        return False

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUTOSTART_KEY_PATH, 0, winreg.KEY_READ) as key:
            try:
                winreg.QueryValueEx(key, AUTOSTART_VALUE_NAME)
                return True
            except FileNotFoundError:
                pass
            try:
                winreg.QueryValueEx(key, LEGACY_AUTOSTART_VALUE_NAME)
                return True
            except FileNotFoundError:
                return False
    except Exception as e:
        app_logger.warning(f"读取自启动注册表项异常: {type(e).__name__}")
        return False


def set_autostart(enable: bool) -> bool:
    """
    配置或取消当前用户的开机自启动设置。
    开启时写入 MCPAssistant 并安全清理旧 XiaozhiMcpApp 项；关闭时同时清理新旧两项。
    :param enable: True 为开启，False 为关闭
    :return: 操作是否成功
    """
    if winreg is None:
        return False

    # 开发源码模式防护：禁止将临时虚拟环境 Python 写入真实自启动项
    if enable and not getattr(sys, "frozen", False):
        app_logger.info("源码开发运行模式下跳过开机自启动项写入")
        return False

    try:
        if enable:
            exe_path = os.path.abspath(sys.executable)
            # 引号包裹完整路径并携带 --background 参数
            launch_cmd = f'"{exe_path}" --background'
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, AUTOSTART_KEY_PATH) as key:
                winreg.SetValueEx(key, AUTOSTART_VALUE_NAME, 0, winreg.REG_SZ, launch_cmd)
                # 清理旧项以避免新旧两项并存
                try:
                    winreg.DeleteValue(key, LEGACY_AUTOSTART_VALUE_NAME)
                    app_logger.info("已安全清理旧版开机自启动注册表项")
                except FileNotFoundError:
                    pass
            app_logger.info("已成功注册用户级开机自启动项")
            return True
        else:
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUTOSTART_KEY_PATH, 0, winreg.KEY_SET_VALUE) as key:
                    for val_name in (AUTOSTART_VALUE_NAME, LEGACY_AUTOSTART_VALUE_NAME):
                        try:
                            winreg.DeleteValue(key, val_name)
                        except FileNotFoundError:
                            pass
                app_logger.info("已成功移除用户级开机自启动项")
                return True
            except FileNotFoundError:
                return True
    except Exception as e:
        app_logger.error(f"修改开机自启动注册表项失败: {type(e).__name__}")
        return False
