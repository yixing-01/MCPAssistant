"""打包产物 Qt 图形界面与运行环境测试。

验证生成的独立 EXE 在子进程中执行 --qt-smoke 时正常退出，
并校验 _internal 目录包含必要的 Qt 平台插件与核心 DLL。
"""

import subprocess
from pathlib import Path


def test_packaged_qt_smoke_exits_cleanly():
    """打包 EXE 执行 --qt-smoke 烟测应在无窗口死锁的情况下正常退出。"""
    exe = Path("dist/MCPAssistant/MCPAssistant.exe")
    assert exe.exists(), f"未找到打包产物: {exe}"
    completed = subprocess.run([str(exe), "--qt-smoke"], timeout=20)
    assert completed.returncode == 0


def test_packaged_qt_platform_plugin_exists():
    """打包产物 _internal 目录应包含 Qt 平台插件 qwindows.dll 及核心运行库。"""
    root = Path("dist/MCPAssistant/_internal")
    assert list(root.rglob("qwindows.dll")), "未在 _internal 找到 qwindows.dll 平台插件"
    assert list(root.rglob("Qt6Core.dll")), "未在 _internal 找到 Qt6Core.dll 核心动态库"
