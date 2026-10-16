from __future__ import annotations

import ctypes
import sys
from typing import Any, Optional

# Windows Per-Monitor DPI Awareness v2 上下文常量
PER_MONITOR_AWARE_V2 = -4


def enable_per_monitor_v2(
    platform: Optional[str] = None,
    user32: Optional[Any] = None,
) -> bool:
    """在 Windows 平台上启用 Per-Monitor DPI Awareness v2。"""
    current_platform = platform or sys.platform
    if current_platform != "win32":
        return False
    target = user32 or ctypes.windll.user32
    try:
        context = ctypes.c_void_p(PER_MONITOR_AWARE_V2)
        return bool(target.SetProcessDpiAwarenessContext(context))
    except Exception:
        return False
