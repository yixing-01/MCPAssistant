"""Windows 元数据与图标读取工具。

提供便携式可执行文件元数据解析、友好显示名称选择与 Windows Shell 原生应用图标提取。
"""

import ctypes
from ctypes import wintypes
import os
import sys
from typing import Optional, Tuple
from PIL import Image


def choose_display_name(stem: str, file_description: str, product_name: str) -> str:
    """根据文件描述、产品名称和主干名选择最友好的显示名称。"""
    return file_description.strip() or product_name.strip() or stem.strip()


def read_executable_names(path: str) -> Tuple[str, str]:
    """读取 EXE 文件的 FileDescription 和 ProductName 元数据。"""
    if sys.platform != "win32" or not path.lower().endswith(".exe"):
        return "", ""
    try:
        import win32api

        translations = win32api.GetFileVersionInfo(path, r"\VarFileInfo\Translation")
        for language, codepage in translations:
            prefix = f"\\StringFileInfo\\{language:04X}{codepage:04X}"
            description = (
                win32api.GetFileVersionInfo(path, prefix + r"\FileDescription") or ""
            )
            product = (
                win32api.GetFileVersionInfo(path, prefix + r"\ProductName") or ""
            )
            if description or product:
                return str(description), str(product)
    except Exception:
        pass
    return "", ""


def portable_display_name(path: str) -> str:
    """获取绿色软件的推荐显示名称。"""
    stem = os.path.splitext(os.path.basename(path))[0]
    description, product = read_executable_names(path)
    return choose_display_name(stem, description, product)


# Windows GDI 与 Shell 结构体定义
class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class _BITMAPINFO(ctypes.Structure):
    _fields_ = [
        ("bmiHeader", _BITMAPINFOHEADER),
        ("bmiColors", wintypes.DWORD * 3),
    ]


class _SHFILEINFOW(ctypes.Structure):
    _fields_ = [
        ("hIcon", wintypes.HICON),
        ("iIcon", ctypes.c_int),
        ("dwAttributes", wintypes.DWORD),
        ("szDisplayName", wintypes.WCHAR * 260),
        ("szTypeName", wintypes.WCHAR * 80),
    ]


_SHGFI_ICON = 0x000000100
_SHGFI_LARGEICON = 0x000000000
_SHGFI_SMALLICON = 0x000000001
_SHGFI_PIDL = 0x000000008


class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", wintypes.BYTE * 8),
    ]


_IID_IShellItemImageFactory = _GUID(
    0xBCC18B79, 0xBA16, 0x442F, (wintypes.BYTE * 8)(0x80, 0xC4, 0x8A, 0x59, 0xC3, 0x0C, 0x46, 0x3B)
)


class _SIZE(ctypes.Structure):
    _fields_ = [("cx", wintypes.LONG), ("cy", wintypes.LONG)]


class _IShellItemImageFactoryVtbl(ctypes.Structure):
    pass


class _IShellItemImageFactory(ctypes.Structure):
    pass


_DWORD_PTR = getattr(wintypes, "DWORD_PTR", ctypes.c_ulonglong)
_ULONG_PTR = getattr(wintypes, "ULONG_PTR", ctypes.c_ulonglong)
_HRESULT = ctypes.c_long


_IShellItemImageFactoryVtbl._fields_ = [
    ("QueryInterface", ctypes.c_void_p),
    ("AddRef", ctypes.c_void_p),
    ("Release", ctypes.WINFUNCTYPE(wintypes.ULONG, ctypes.c_void_p)),
    ("GetImage", ctypes.WINFUNCTYPE(
        _HRESULT,
        ctypes.c_void_p,
        _SIZE,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.HBITMAP)
    )),
]
_IShellItemImageFactory._fields_ = [("lpVtbl", ctypes.POINTER(_IShellItemImageFactoryVtbl))]


def _setup_win32_api() -> None:
    """集中声明所有使用的 Win32 API 原型，确保在 64 位 Windows 下不会发生句柄截断或堆损坏。"""
    if sys.platform != "win32":
        return

    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32
    shell32 = ctypes.windll.shell32
    ole32 = ctypes.windll.ole32

    # user32
    user32.GetDC.argtypes = [wintypes.HWND]
    user32.GetDC.restype = wintypes.HDC

    user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    user32.ReleaseDC.restype = ctypes.c_int

    user32.DrawIconEx.argtypes = [
        wintypes.HDC,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.HICON,
        ctypes.c_int,
        ctypes.c_int,
        wintypes.UINT,
        wintypes.HBRUSH,
        wintypes.UINT,
    ]
    user32.DrawIconEx.restype = wintypes.BOOL

    user32.DestroyIcon.argtypes = [wintypes.HICON]
    user32.DestroyIcon.restype = wintypes.BOOL

    # gdi32
    gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
    gdi32.CreateCompatibleDC.restype = wintypes.HDC

    gdi32.DeleteDC.argtypes = [wintypes.HDC]
    gdi32.DeleteDC.restype = wintypes.BOOL

    gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
    gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP

    gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
    gdi32.SelectObject.restype = wintypes.HGDIOBJ

    gdi32.GetDIBits.argtypes = [
        wintypes.HDC,
        wintypes.HBITMAP,
        wintypes.UINT,
        wintypes.UINT,
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.UINT,
    ]
    gdi32.GetDIBits.restype = ctypes.c_int

    gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
    gdi32.DeleteObject.restype = wintypes.BOOL

    # shell32
    shell32.SHGetFileInfoW.argtypes = [
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.UINT,
        wintypes.UINT,
    ]
    shell32.SHGetFileInfoW.restype = _DWORD_PTR

    shell32.SHParseDisplayName.argtypes = [
        wintypes.LPCWSTR,
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p),
        wintypes.ULONG,
        ctypes.POINTER(wintypes.ULONG),
    ]
    shell32.SHParseDisplayName.restype = _HRESULT

    shell32.SHCreateItemFromParsingName.argtypes = [
        wintypes.LPCWSTR,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    shell32.SHCreateItemFromParsingName.restype = _HRESULT

    # ole32
    ole32.CoInitialize.argtypes = [ctypes.c_void_p]
    ole32.CoInitialize.restype = _HRESULT

    ole32.CoUninitialize.argtypes = []
    ole32.CoUninitialize.restype = None

    ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]
    ole32.CoTaskMemFree.restype = None


_setup_win32_api()


class _ComScope:
    """管理当前线程的 COM 初始化与反初始化生命周期。

    规则：
    - S_OK (0): 初始化成功，必须配对 CoUninitialize
    - S_FALSE (1): 已在当前线程以相同模式初始化，引用计数递增，必须配对 CoUninitialize
    - RPC_E_CHANGED_MODE (-2147417850 / 0x80010106): 模式冲突，初始化未成功，绝不得调用 CoUninitialize
    - 其他失败: 绝不得调用 CoUninitialize
    """

    def __init__(self):
        self._need_uninit = False

    def __enter__(self):
        if sys.platform != "win32":
            return self
        try:
            hr = ctypes.windll.ole32.CoInitialize(None)
            if hr in (0, 1):
                self._need_uninit = True
            else:
                self._need_uninit = False
        except Exception:
            self._need_uninit = False
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._need_uninit and sys.platform == "win32":
            try:
                ctypes.windll.ole32.CoUninitialize()
            except Exception:
                pass
            self._need_uninit = False


def _hicon_to_pillow(hicon: wintypes.HICON, size: int) -> Optional[Image.Image]:
    """将 Windows HICON 句柄转换为 Pillow RGBA 图像，并在完成后释放 GDI 句柄。

    注意：hicon 由调用者拥有并由调用者负责 DestroyIcon，本函数不释放 hicon。
    """
    if not hicon or sys.platform != "win32":
        return None

    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32

    hdc_screen = user32.GetDC(None)
    if not hdc_screen:
        return None

    hdc_mem = None
    hbm = None
    hbm_old = None

    try:
        hdc_mem = gdi32.CreateCompatibleDC(hdc_screen)
        if not hdc_mem:
            return None

        hbm = gdi32.CreateCompatibleBitmap(hdc_screen, size, size)
        if not hbm:
            return None

        hbm_old = gdi32.SelectObject(hdc_mem, hbm)
        if not hbm_old:
            return None

        # DI_NORMAL = 0x0003
        if not user32.DrawIconEx(hdc_mem, 0, 0, hicon, size, size, 0, None, 0x0003):
            return None

        bmi = _BITMAPINFO()
        bmi.bmiHeader.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
        bmi.bmiHeader.biWidth = size
        bmi.bmiHeader.biHeight = -size  # 自顶向下
        bmi.bmiHeader.biPlanes = 1
        bmi.bmiHeader.biBitCount = 32
        bmi.bmiHeader.biCompression = 0

        buf = ctypes.create_string_buffer(size * size * 4)
        if not gdi32.GetDIBits(hdc_mem, hbm, 0, size, buf, ctypes.byref(bmi), 0):
            return None

        img = Image.frombuffer("RGBA", (size, size), buf.raw, "raw", "BGRA", 0, 1)
        r, g, b, a = img.split()
        extrema = a.getextrema()
        if extrema[1] == 0:
            mask = Image.eval(img.convert("L"), lambda p: 255 if p > 0 else 0)
            img.putalpha(mask)
        return img
    except Exception:
        return None
    finally:
        if hdc_mem and hbm_old:
            try:
                gdi32.SelectObject(hdc_mem, hbm_old)
            except Exception:
                pass
        if hbm:
            try:
                gdi32.DeleteObject(hbm)
            except Exception:
                pass
        if hdc_mem:
            try:
                gdi32.DeleteDC(hdc_mem)
            except Exception:
                pass
        if hdc_screen:
            try:
                user32.ReleaseDC(None, hdc_screen)
            except Exception:
                pass


def _load_via_image_factory(path_or_aumid_url: str, size: int) -> Optional[Image.Image]:
    """使用 IShellItemImageFactory 直接从 Shell Item 提取高质量 32 位 ARGB 图标。"""
    if sys.platform != "win32":
        return None

    shell32 = ctypes.windll.shell32
    gdi32 = ctypes.windll.gdi32
    user32 = ctypes.windll.user32

    with _ComScope():
        p_factory_void = ctypes.c_void_p()
        hr = shell32.SHCreateItemFromParsingName(
            path_or_aumid_url,
            None,
            ctypes.byref(_IID_IShellItemImageFactory),
            ctypes.byref(p_factory_void)
        )
        if hr != 0 or not p_factory_void.value:
            return None

        p_factory = ctypes.cast(p_factory_void, ctypes.POINTER(_IShellItemImageFactory))
        hbitmap = wintypes.HBITMAP()
        hdc_screen = None
        try:
            sz = _SIZE(size, size)
            flags = 0x00  # SIIGBF_RESIZETOFIT
            hr = p_factory.contents.lpVtbl.contents.GetImage(
                p_factory, sz, flags, ctypes.byref(hbitmap)
            )
            if hr != 0 or not hbitmap.value:
                return None

            hdc_screen = user32.GetDC(None)
            if not hdc_screen:
                return None

            bmi = _BITMAPINFO()
            bmi.bmiHeader.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
            bmi.bmiHeader.biWidth = size
            bmi.bmiHeader.biHeight = -size
            bmi.bmiHeader.biPlanes = 1
            bmi.bmiHeader.biBitCount = 32
            bmi.bmiHeader.biCompression = 0

            buf = ctypes.create_string_buffer(size * size * 4)
            if not gdi32.GetDIBits(hdc_screen, hbitmap, 0, size, buf, ctypes.byref(bmi), 0):
                return None

            img = Image.frombuffer("RGBA", (size, size), buf.raw, "raw", "BGRA", 0, 1)
            r, g, b, a = img.split()
            if a.getextrema()[1] == 0:
                mask = Image.eval(img.convert("L"), lambda p: 255 if p > 0 else 0)
                img.putalpha(mask)
            return img
        except Exception:
            return None
        finally:
            if hdc_screen:
                try:
                    user32.ReleaseDC(None, hdc_screen)
                except Exception:
                    pass
            if hbitmap and hbitmap.value:
                try:
                    gdi32.DeleteObject(hbitmap)
                except Exception:
                    pass
            if p_factory:
                try:
                    p_factory.contents.lpVtbl.contents.Release(p_factory)
                except Exception:
                    pass


def load_shell_icon(
    icon_ref: str, launch_type: str = "win32", size: int = 36
) -> Optional[Image.Image]:
    """从本地文件或 Store AUMID 读取 Windows Shell 图标。

    安全设计：出现任何异常或平台不兼容时均静默返回 None，决不抛出异常。
    """
    if sys.platform != "win32" or not icon_ref:
        return None

    lt_str = str(getattr(launch_type, "value", launch_type) or "").lower().strip()
    shell32 = ctypes.windll.shell32
    user32 = ctypes.windll.user32
    ole32 = ctypes.windll.ole32

    if lt_str in ("store_aumid", "store"):
        # 1. 优先使用 Windows IShellItemImageFactory 获取 Store 原生清晰矢量/位图图标
        img = _load_via_image_factory(f"shell:AppsFolder\\{icon_ref}", size)
        if img:
            return img

        # 2. 备用分支：通过 PIDL + SHGetFileInfoW
        pidl = None
        hicon = None
        try:
            with _ComScope():
                pidl_ptr = ctypes.c_void_p()
                folder_path = f"shell:AppsFolder\\{icon_ref}"
                hr = shell32.SHParseDisplayName(
                    folder_path, None, ctypes.byref(pidl_ptr), 0, None
                )
                if hr == 0 and pidl_ptr.value:
                    pidl = pidl_ptr.value
                    flags = _SHGFI_PIDL | _SHGFI_ICON | (_SHGFI_LARGEICON if size > 16 else _SHGFI_SMALLICON)
                    sfi = _SHFILEINFOW()
                    res = shell32.SHGetFileInfoW(
                        pidl,
                        0,
                        ctypes.byref(sfi),
                        ctypes.sizeof(sfi),
                        flags,
                    )
                    if res and sfi.hIcon:
                        hicon = sfi.hIcon
                if hicon:
                    return _hicon_to_pillow(hicon, size)
        except Exception:
            return None
        finally:
            if hicon:
                try:
                    user32.DestroyIcon(hicon)
                except Exception:
                    pass
                hicon = None
            if pidl:
                try:
                    ole32.CoTaskMemFree(pidl)
                except Exception:
                    pass
                pidl = None
        return None
    else:
        # 普通文件路径或快捷方式
        sfi = _SHFILEINFOW()
        hicon = None
        try:
            flags = _SHGFI_ICON | (_SHGFI_LARGEICON if size > 16 else _SHGFI_SMALLICON)
            path_ptr = ctypes.cast(ctypes.c_wchar_p(icon_ref), ctypes.c_void_p).value
            res = shell32.SHGetFileInfoW(
                path_ptr, 0, ctypes.byref(sfi), ctypes.sizeof(sfi), flags
            )
            if res and sfi.hIcon:
                hicon = sfi.hIcon
            if hicon:
                return _hicon_to_pillow(hicon, size)

            # 备用：尝试通过 IShellItemImageFactory 解析路径
            return _load_via_image_factory(icon_ref, size)
        except Exception:
            return None
        finally:
            if hicon:
                try:
                    user32.DestroyIcon(hicon)
                except Exception:
                    pass
                hicon = None
    return None
