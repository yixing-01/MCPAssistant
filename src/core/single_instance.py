"""Windows 用户级单实例保护互斥体
通过 Windows 原生命名互斥锁实现，防止重复启动多个 GUI 进程与多条 WebSocket 桥接。
注：仅在 GUI 模式下检测，严禁影响 --mcp-service 子进程启动。
"""

import sys
import ctypes
from ctypes import wintypes
from typing import Optional

ERROR_ALREADY_EXISTS = 183
DEFAULT_MUTEX_NAME = "Local\\MCPAssistant_SingleInstance_Mutex"
LEGACY_MUTEX_NAME = "Local\\XiaozhiMcpApp_SingleInstance_Mutex"

if sys.platform == "win32":
    kernel32 = ctypes.windll.kernel32
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL


class SingleInstanceLock:
    """Windows 命名互斥锁单实例控制器"""

    def __init__(self, name: str = DEFAULT_MUTEX_NAME):
        self.name = name
        self.handle: Optional[wintypes.HANDLE] = None
        self._is_owner = False

    def acquire(self) -> bool:
        """尝试获取单实例互斥锁。若已有运行中的实例，返回 False"""
        if sys.platform != "win32":
            return True

        if self.handle:
            return True

        kernel32 = ctypes.windll.kernel32
        kernel32.SetLastError(0)
        # CreateMutexW(lpMutexAttributes, bInitialOwner, lpName)
        handle = kernel32.CreateMutexW(None, False, self.name)
        last_error = kernel32.GetLastError()

        if not handle:
            return False

        if last_error == ERROR_ALREADY_EXISTS:
            # 互斥体已存在，说明有前置实例正在运行
            kernel32.CloseHandle(handle)
            self.handle = None
            self._is_owner = False
            return False

        self.handle = handle
        self._is_owner = True
        return True

    def release(self):
        """释放并关闭互斥体句柄，支持多次调用不崩溃"""
        if sys.platform == "win32" and self.handle:
            try:
                kernel32 = ctypes.windll.kernel32
                kernel32.CloseHandle(self.handle)
            except Exception:
                pass
            self.handle = None
            self._is_owner = False

    def __enter__(self):
        return self.acquire()

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.release()
