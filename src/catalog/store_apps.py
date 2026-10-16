"""Microsoft Store / MSIX 应用发现与原生 AUMID 激活模块"""

import ctypes
import re
import sys
import uuid
from ctypes import wintypes
from dataclasses import dataclass
from typing import Callable, Iterable, List, Optional


AUMID_PATTERN = re.compile(r"^[A-Za-z0-9._-]+![A-Za-z0-9._-]+$")
CODEX_AUMID = "OpenAI.Codex_2p2nqsd0c76g0!App"


class StoreDiscoveryError(RuntimeError):
    """Store 应用发现异常"""
    pass


class StoreActivationError(RuntimeError):
    """Store 应用激活异常"""
    pass


@dataclass(frozen=True)
class StoreAppRecord:
    """Store 应用发现结果记录"""
    display_name: str
    aumid: str


def is_valid_aumid(value: str) -> bool:
    """验证 AUMID 是否符合 Windows 规范格式 (PackageFamilyName!AppId)"""
    return bool(AUMID_PATTERN.fullmatch(str(value or "").strip()))


def default_store_aliases(display_name: str, aumid: str) -> List[str]:
    """生成 Store 应用的默认语音别名"""
    aliases = [display_name.strip()]
    if aumid.casefold() == CODEX_AUMID.casefold():
        aliases.extend(["Codex", "OpenAI Codex", "codeex"])
    result: List[str] = []
    seen = set()
    for alias in aliases:
        key = alias.casefold()
        if alias and key not in seen:
            seen.add(key)
            result.append(alias)
    return result


class StoreAppProvider:
    """Store 应用发现提供器，基于 Windows 原生 shell:AppsFolder COM 接口"""
    def __init__(self, items_loader: Optional[Callable[[], Iterable[object]]] = None):
        self._items_loader = items_loader

    @staticmethod
    def _load_apps_folder_items() -> List[object]:
        """通过 Windows COM 枚举 shell:AppsFolder 中的所有可启动对象"""
        if sys.platform != "win32":
            return []
        try:
            import pythoncom
            import win32com.client
        except ImportError as exc:
            raise StoreDiscoveryError("Store 应用发现组件不可用") from exc

        initialized = False
        try:
            pythoncom.CoInitialize()
            initialized = True
            shell = win32com.client.Dispatch("Shell.Application")
            folder = shell.Namespace("shell:AppsFolder")
            if folder is None:
                raise StoreDiscoveryError("Windows 应用列表不可用")
            return list(folder.Items())
        except StoreDiscoveryError:
            raise
        except Exception as exc:
            raise StoreDiscoveryError("读取 Windows 应用列表失败") from exc
        finally:
            if initialized:
                pythoncom.CoUninitialize()

    def enumerate_apps(self) -> List[StoreAppRecord]:
        """枚举所有已安装并具备合法 AUMID 的 Store 应用"""
        loader = self._items_loader or self._load_apps_folder_items
        records: List[StoreAppRecord] = []
        seen = set()
        for item in loader():
            name = str(getattr(item, "Name", "") or "").strip()
            aumid = str(getattr(item, "Path", "") or "").strip()
            key = aumid.casefold()
            if name and is_valid_aumid(aumid) and key not in seen:
                seen.add(key)
                records.append(StoreAppRecord(name, aumid))
        return sorted(records, key=lambda record: record.display_name.casefold())

    def is_installed(self, aumid: str) -> bool:
        """检查特定 AUMID 的 Store 应用是否仍然已安装"""
        target = str(aumid or "").strip().casefold()
        return is_valid_aumid(aumid) and any(
            record.aumid.casefold() == target for record in self.enumerate_apps()
        )


class GUID(ctypes.Structure):
    """Windows 原生 COM GUID 结构体"""
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", wintypes.BYTE * 8),
    ]

    @classmethod
    def parse(cls, value: str) -> "GUID":
        raw = uuid.UUID(value).bytes_le
        return cls(
            int.from_bytes(raw[0:4], "little"),
            int.from_bytes(raw[4:6], "little"),
            int.from_bytes(raw[6:8], "little"),
            (wintypes.BYTE * 8)(*raw[8:]),
        )


def _native_activate_application(aumid: str) -> int:
    """通过 Windows 原生 IApplicationActivationManager::ActivateApplication 激活应用"""
    if sys.platform != "win32":
        raise StoreActivationError("当前系统不支持 Store 应用启动")

    clsid = GUID.parse("45BA127D-10A8-46EA-8AB7-56EA9078943C")
    iid = GUID.parse("2E941141-7F97-4756-BA1D-9DECDE894A3D")
    ole32 = ctypes.OleDLL("ole32")
    pointer = ctypes.c_void_p()
    should_uninitialize = False

    coinit_result = ole32.CoInitializeEx(None, 0x2)
    if coinit_result in (0, 1):
        should_uninitialize = True
    elif coinit_result != -2147417850:
        raise StoreActivationError("初始化 Windows 应用启动组件失败")

    try:
        ole32.CoCreateInstance.argtypes = [
            ctypes.POINTER(GUID),
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(GUID),
            ctypes.POINTER(ctypes.c_void_p),
        ]
        ole32.CoCreateInstance.restype = ctypes.c_long
        hr = ole32.CoCreateInstance(
            ctypes.byref(clsid), None, 0x4, ctypes.byref(iid), ctypes.byref(pointer)
        )
        if hr < 0 or not pointer.value:
            raise StoreActivationError("创建 Windows 应用启动组件失败")

        vtable = ctypes.cast(
            pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))
        ).contents
        activate_type = ctypes.WINFUNCTYPE(
            ctypes.c_long,
            ctypes.c_void_p,
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
        )
        activate = activate_type(vtable[3])
        process_id = wintypes.DWORD()
        hr = activate(pointer, aumid, None, 0, ctypes.byref(process_id))
        if hr < 0:
            raise StoreActivationError("Windows 未能启动该 Store 应用")
        return int(process_id.value)
    finally:
        if pointer.value:
            try:
                vtable = ctypes.cast(
                    pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))
                ).contents
                release = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)(vtable[2])
                release(pointer)
            except Exception:
                pass
        if should_uninitialize:
            ole32.CoUninitialize()


class StoreAppLauncher:
    """Store 应用启动器，严格基于有效 AUMID 激活"""
    def __init__(self, native_activate: Callable[[str], int] = _native_activate_application):
        self._native_activate = native_activate

    def activate(self, aumid: str) -> int:
        clean = str(aumid or "").strip()
        if not is_valid_aumid(clean):
            raise StoreActivationError("Store 应用标识无效")
        return self._native_activate(clean)
