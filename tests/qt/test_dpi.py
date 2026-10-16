import ctypes
from unittest.mock import MagicMock

from src.ui_qt.dpi import enable_per_monitor_v2


def test_enable_per_monitor_v2_uses_windows_context():
    user32 = MagicMock()
    assert enable_per_monitor_v2(platform="win32", user32=user32) is True
    user32.SetProcessDpiAwarenessContext.assert_called_once()
    context = user32.SetProcessDpiAwarenessContext.call_args.args[0]
    assert context.value == ctypes.c_void_p(-4).value


def test_enable_per_monitor_v2_is_safe_off_windows():
    assert enable_per_monitor_v2(platform="linux", user32=None) is False
