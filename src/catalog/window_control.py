"""Windows 窗口控制与主窗口安全关闭模块。
负责枚举顶层窗口、应用身份精确核验、主窗口筛选、发送非阻塞正常关闭请求及状态统计。
严格执行脱敏与最小权限原则，不强制结束进程，不使用 Shell 注入，不使用 Alt+F4。
"""

from __future__ import annotations

import ctypes
import os
import sys
import time
from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional, Set

from src.catalog.models import AppEntry, LaunchType
from src.catalog.shortcut_resolver import ShortcutResolver
from src.core.logger import app_logger


class CloseStatus(str, Enum):
    """窗口关闭状态分类（对外脱敏）"""
    NO_WINDOWS = "NO_WINDOWS"                  # 没有可关闭的主窗口
    ALL_CLOSED = "ALL_CLOSED"                  # 全部匹配主窗口已成功关闭/隐藏
    SOME_REMAINING = "SOME_REMAINING"          # 部分主窗口仍未关闭（可能正在等待用户确认或部分发送失败）
    ALL_REMAINING = "ALL_REMAINING"            # 全部主窗口仍未关闭（可能正在等待用户确认）
    ACCESS_DENIED = "ACCESS_DENIED"            # 权限不足无法关闭（例如高完整性窗口）
    REQUEST_FAILED = "REQUEST_FAILED"          # 发送关闭请求失败
    IDENTITY_UNCERTAIN = "IDENTITY_UNCERTAIN"  # 无法安全确认身份，未执行关闭


@dataclass(frozen=True)
class WindowCloseResult:
    """结构化脱敏关闭结果"""
    status: CloseStatus
    matched_count: int = 0
    closed_count: int = 0
    remaining_count: int = 0
    failed_post_count: int = 0
    user_message: str = ""


@dataclass
class WindowTarget:
    """内部窗口属性描述（仅供系统内部与测试使用，对外严格脱敏）"""
    hwnd: int
    pid: int
    process_path: str = ""
    aumid: str = ""
    is_visible: bool = True
    is_cloaked: bool = False
    is_tool_window: bool = False
    has_owner: bool = False
    is_child: bool = False


class NativeWindowApi:
    """Windows 原生 API 适配接口，便于依赖注入与离线单元测试"""

    def enum_top_level_windows(self) -> List[int]:
        raise NotImplementedError

    def is_window(self, hwnd: int) -> bool:
        raise NotImplementedError

    def is_window_visible(self, hwnd: int) -> bool:
        raise NotImplementedError

    def is_window_cloaked(self, hwnd: int) -> bool:
        raise NotImplementedError

    def get_window_pid(self, hwnd: int) -> int:
        raise NotImplementedError

    def get_process_image_path(self, pid: int) -> str:
        raise NotImplementedError

    def get_process_aumid(self, pid: int, hwnd: int = 0) -> str:
        raise NotImplementedError

    def is_tool_window(self, hwnd: int) -> bool:
        raise NotImplementedError

    def has_owner(self, hwnd: int) -> bool:
        raise NotImplementedError

    def is_child_window(self, hwnd: int) -> bool:
        raise NotImplementedError

    def post_close_message(self, hwnd: int) -> tuple[bool, int]:
        raise NotImplementedError

    def sleep(self, seconds: float) -> None:
        raise NotImplementedError


class WindowsNativeApi(NativeWindowApi):
    """Windows 平台真实的 CTypes / Win32 API 封装实现（包含完整 64 位指针类型签名与错误码读取）"""

    def __init__(self):
        if sys.platform != "win32":
            return
        from ctypes import wintypes
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        try:
            self.dwmapi = ctypes.WinDLL("dwmapi", use_last_error=True)
        except Exception:
            self.dwmapi = None

        # 设置关键函数原型（指针/句柄均严格使用 pointer-sized Windows 类型）
        self.WNDENUMPROC = ctypes.WINFUNCTYPE(
            wintypes.BOOL, wintypes.HWND, wintypes.LPARAM
        )
        self.user32.EnumWindows.argtypes = [self.WNDENUMPROC, wintypes.LPARAM]
        self.user32.EnumWindows.restype = wintypes.BOOL

        self.user32.IsWindow.argtypes = [wintypes.HWND]
        self.user32.IsWindow.restype = wintypes.BOOL

        self.user32.IsWindowVisible.argtypes = [wintypes.HWND]
        self.user32.IsWindowVisible.restype = wintypes.BOOL

        self.user32.GetWindowThreadProcessId.argtypes = [
            wintypes.HWND,
            ctypes.POINTER(wintypes.DWORD),
        ]
        self.user32.GetWindowThreadProcessId.restype = wintypes.DWORD

        self.user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
        self.user32.GetWindowLongW.restype = wintypes.LONG

        self.user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
        self.user32.GetWindow.restype = wintypes.HWND

        self.user32.PostMessageW.argtypes = [
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        ]
        self.user32.PostMessageW.restype = wintypes.BOOL

        self.user32.EnumChildWindows.argtypes = [
            wintypes.HWND,
            self.WNDENUMPROC,
            wintypes.LPARAM,
        ]
        self.user32.EnumChildWindows.restype = wintypes.BOOL

        self.kernel32.OpenProcess.argtypes = [
            wintypes.DWORD,
            wintypes.BOOL,
            wintypes.DWORD,
        ]
        self.kernel32.OpenProcess.restype = wintypes.HANDLE

        self.kernel32.QueryFullProcessImageNameW.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.LPWSTR,
            ctypes.POINTER(wintypes.DWORD),
        ]
        self.kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL

        self.kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel32.CloseHandle.restype = wintypes.BOOL

        fn_get_aumid = getattr(self.kernel32, "GetApplicationUserModelId", None)
        if fn_get_aumid:
            fn_get_aumid.argtypes = [
                wintypes.HANDLE,
                ctypes.POINTER(wintypes.UINT),
                wintypes.LPWSTR,
            ]
            fn_get_aumid.restype = wintypes.LONG

        if self.dwmapi:
            self.dwmapi.DwmGetWindowAttribute.argtypes = [
                wintypes.HWND,
                wintypes.DWORD,
                wintypes.LPCVOID,
                wintypes.DWORD,
            ]
            self.dwmapi.DwmGetWindowAttribute.restype = ctypes.HRESULT

    def enum_top_level_windows(self) -> List[int]:
        if sys.platform != "win32":
            return []
        from ctypes import wintypes
        windows: List[int] = []

        def enum_proc(hwnd, lparam):
            windows.append(hwnd)
            return True

        cb = self.WNDENUMPROC(enum_proc)
        self.user32.EnumWindows(cb, 0)
        return windows

    def is_window(self, hwnd: int) -> bool:
        if sys.platform != "win32":
            return False
        return bool(self.user32.IsWindow(hwnd))

    def is_window_visible(self, hwnd: int) -> bool:
        if sys.platform != "win32":
            return False
        return bool(self.user32.IsWindowVisible(hwnd))

    def is_window_cloaked(self, hwnd: int) -> bool:
        if sys.platform != "win32" or not self.dwmapi:
            return False
        from ctypes import wintypes
        cloaked = wintypes.DWORD(0)
        # DWMWA_CLOAKED = 14
        hr = self.dwmapi.DwmGetWindowAttribute(
            hwnd, 14, ctypes.byref(cloaked), ctypes.sizeof(cloaked)
        )
        return hr == 0 and cloaked.value != 0

    def get_window_pid(self, hwnd: int) -> int:
        if sys.platform != "win32":
            return 0
        from ctypes import wintypes
        pid = wintypes.DWORD(0)
        self.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return int(pid.value)

    def get_process_image_path(self, pid: int) -> str:
        if sys.platform != "win32" or pid <= 0:
            return ""
        from ctypes import wintypes
        # PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        h_process = self.kernel32.OpenProcess(0x1000, False, pid)
        if not h_process:
            return ""
        try:
            buf_len = wintypes.DWORD(4096)
            buf = ctypes.create_unicode_buffer(buf_len.value)
            # QueryFullProcessImageNameW(hProcess, 0, lpExeName, lpdwSize)
            if self.kernel32.QueryFullProcessImageNameW(
                h_process, 0, buf, ctypes.byref(buf_len)
            ):
                return buf.value
            return ""
        finally:
            self.kernel32.CloseHandle(h_process)

    def get_process_aumid(self, pid: int, hwnd: int = 0) -> str:
        """从进程或窗口获取 ApplicationUserModelID (AUMID)"""
        if sys.platform != "win32" or pid <= 0:
            return ""
        from ctypes import wintypes

        # 1. 尝试通过 kernel32.GetApplicationUserModelId 查询进程 AUMID
        h_process = self.kernel32.OpenProcess(0x1000, False, pid)
        if h_process:
            try:
                fn_get_aumid = getattr(self.kernel32, "GetApplicationUserModelId", None)
                if fn_get_aumid:
                    length = wintypes.UINT(0)
                    rc = fn_get_aumid(h_process, ctypes.byref(length), None)
                    # ERROR_INSUFFICIENT_BUFFER = 122
                    if rc == 122 and length.value > 0:
                        buf = ctypes.create_unicode_buffer(length.value)
                        rc = fn_get_aumid(h_process, ctypes.byref(length), buf)
                        if rc == 0 and buf.value:
                            return buf.value
            finally:
                self.kernel32.CloseHandle(h_process)

        # 2. 尝试从 ApplicationFrameHost 顶级窗口包含的子窗口中获取 Store 应用 AUMID
        if hwnd:
            child_hwnds: List[int] = []

            def child_proc(child_hwnd, lparam):
                child_hwnds.append(child_hwnd)
                return True

            child_cb = self.WNDENUMPROC(child_proc)
            self.user32.EnumChildWindows(hwnd, child_cb, 0)
            for ch in child_hwnds:
                ch_pid = self.get_window_pid(ch)
                if ch_pid and ch_pid != pid:
                    ch_aumid = self.get_process_aumid(ch_pid, hwnd=0)
                    if ch_aumid:
                        return ch_aumid

        return ""

    def is_tool_window(self, hwnd: int) -> bool:
        if sys.platform != "win32":
            return False
        # GWL_EXSTYLE = -20
        # WS_EX_TOOLWINDOW = 0x00000080
        # WS_EX_APPWINDOW = 0x00040000
        ex_style = self.user32.GetWindowLongW(hwnd, -20)
        return bool((ex_style & 0x00000080) and not (ex_style & 0x00040000))

    def has_owner(self, hwnd: int) -> bool:
        if sys.platform != "win32":
            return False
        # GW_OWNER = 4
        owner = self.user32.GetWindow(hwnd, 4)
        return bool(owner and self.user32.IsWindow(owner))

    def is_child_window(self, hwnd: int) -> bool:
        if sys.platform != "win32":
            return False
        # GWL_STYLE = -16
        # WS_CHILD = 0x40000000
        style = self.user32.GetWindowLongW(hwnd, -16)
        return bool(style & 0x40000000)

    def post_close_message(self, hwnd: int) -> tuple[bool, int]:
        if sys.platform != "win32":
            return False, -1
        # WM_CLOSE = 0x0010
        ret = bool(self.user32.PostMessageW(hwnd, 0x0010, 0, 0))
        if ret:
            return True, 0
        err = ctypes.get_last_error()
        return False, err

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


class WindowController:
    """应用主窗口定位与受控关闭控制器"""

    def __init__(self, api: Optional[NativeWindowApi] = None):
        self.api = api or (WindowsNativeApi() if sys.platform == "win32" else NativeWindowApi())

    @staticmethod
    def _normalize_path(path: str) -> str:
        """规范化本地可执行文件物理路径，用于大小写与路径格式无关的精确匹配"""
        if not path:
            return ""
        try:
            return os.path.normcase(os.path.realpath(os.path.abspath(path)))
        except Exception:
            return os.path.normcase(os.path.abspath(path))

    def _get_target_launch_identity(self, app_entry: AppEntry) -> tuple[LaunchType, str]:
        """获取目标应用条目的稳定匹配身份标识"""
        if app_entry.launch_type == LaunchType.STORE_AUMID:
            return LaunchType.STORE_AUMID, app_entry.launch_target.strip().casefold()

        # 快捷方式：复用 ShortcutResolver 重新核验并取得真实目标
        if app_entry.launch_type == LaunchType.SHORTCUT or app_entry.launch_target.lower().endswith(".lnk"):
            target_lnk = app_entry.launch_target or app_entry.exe_path
            if target_lnk and os.path.exists(target_lnk):
                val_res = ShortcutResolver.validate(target_lnk)
                if val_res.is_allowed and val_res.info and val_res.info.target_path:
                    return LaunchType.SHORTCUT, self._normalize_path(val_res.info.target_path)
            # 若已缓存解析目标且物理文件依然存在
            if app_entry.resolved_target and os.path.exists(app_entry.resolved_target):
                return LaunchType.SHORTCUT, self._normalize_path(app_entry.resolved_target)

        # Win32 / 便携应用
        real_target = app_entry.resolved_target or app_entry.exe_path or app_entry.launch_target
        return app_entry.launch_type, self._normalize_path(real_target)

    def verify_window_identity(self, hwnd: int, target_type: LaunchType, target_identity: str) -> bool:
        """安全验证窗口所属应用身份与目标是否完全一致"""
        if not self.api.is_window(hwnd):
            return False

        pid = self.api.get_window_pid(hwnd)
        if not pid:
            return False

        if target_type == LaunchType.STORE_AUMID:
            try:
                proc_aumid = self.api.get_process_aumid(pid, hwnd=hwnd)
                return bool(proc_aumid and proc_aumid.strip().casefold() == target_identity)
            except Exception:
                return False

        # Win32 / 绿色 / 快捷方式：按可执行文件规范化物理路径精确比对
        try:
            proc_path = self.api.get_process_image_path(pid)
            if not proc_path:
                return False
            norm_proc = self._normalize_path(proc_path)
            return norm_proc == target_identity
        except Exception:
            return False

    def find_eligible_windows(self, app_entry: AppEntry) -> List[int]:
        """枚举并筛选出属于目标应用的所有可见主窗口 HWND"""
        target_type, target_identity = self._get_target_launch_identity(app_entry)
        if not target_identity:
            return []

        all_hwnds = self.api.enum_top_level_windows()
        eligible_hwnds: List[int] = []

        for hwnd in all_hwnds:
            # 1. 基础顶层窗口与可见性检查
            if not self.api.is_window(hwnd) or not self.api.is_window_visible(hwnd):
                continue

            # 2. 排除子窗口、工具浮窗、具有拥有者的从属弹窗、被 DWM cloaked 窗口
            if self.api.is_child_window(hwnd):
                continue
            if self.api.is_tool_window(hwnd):
                continue
            if self.api.has_owner(hwnd):
                continue
            if self.api.is_window_cloaked(hwnd):
                continue

            # 3. 严格验证窗口身份
            if self.verify_window_identity(hwnd, target_type, target_identity):
                eligible_hwnds.append(hwnd)

        return eligible_hwnds

    def close_app_windows(self, app_entry: AppEntry, timeout_sec: float = 1.0) -> WindowCloseResult:
        """向目标已授权应用的所有可见主窗口发送正常关闭请求并统计结果"""
        target_type, target_identity = self._get_target_launch_identity(app_entry)
        if not target_identity:
            return WindowCloseResult(
                status=CloseStatus.IDENTITY_UNCERTAIN,
                matched_count=0,
                closed_count=0,
                remaining_count=0,
                failed_post_count=0,
                user_message="无法安全确认该软件的主窗口，未执行关闭"
            )

        # 1. 查找全部匹配的可见主窗口
        matched_hwnds = self.find_eligible_windows(app_entry)
        matched_count = len(matched_hwnds)

        if matched_count == 0:
            return WindowCloseResult(
                status=CloseStatus.NO_WINDOWS,
                matched_count=0,
                closed_count=0,
                remaining_count=0,
                failed_post_count=0,
                user_message="该软件当前没有可关闭的主窗口"
            )

        # 2. 再次校验竞态，向所有匹配主窗口发送 WM_CLOSE
        posted_hwnds: Set[int] = set()
        failed_errors: List[int] = []

        for hwnd in matched_hwnds:
            # 发送前最后确认窗口依然有效且身份未变化
            if self.verify_window_identity(hwnd, target_type, target_identity):
                ok, err = self.api.post_close_message(hwnd)
                if ok:
                    posted_hwnds.add(hwnd)
                else:
                    failed_errors.append(err)
            else:
                # 身份发生变化或被重用，停止操作并记录为权限/安全拒绝 (ERROR_ACCESS_DENIED = 5)
                failed_errors.append(5)

        failed_post_count = len(failed_errors)

        # 匹配到窗口但发送全部失败时，绝不返回 NO_WINDOWS
        if not posted_hwnds:
            # ERROR_ACCESS_DENIED = 5
            if any(err == 5 for err in failed_errors):
                return WindowCloseResult(
                    status=CloseStatus.ACCESS_DENIED,
                    matched_count=matched_count,
                    closed_count=0,
                    remaining_count=matched_count,
                    failed_post_count=failed_post_count,
                    user_message="权限不足，无法向该软件主窗口发送关闭请求（可能该软件以管理员权限运行）"
                )
            else:
                return WindowCloseResult(
                    status=CloseStatus.REQUEST_FAILED,
                    matched_count=matched_count,
                    closed_count=0,
                    remaining_count=matched_count,
                    failed_post_count=failed_post_count,
                    user_message=f"向该软件主窗口发送关闭请求失败（共 {failed_post_count} 个主窗口发送失败）"
                )

        # 3. 有上限的短暂轮询（最多 timeout_sec 秒），检查主窗口是否销毁或变为不可见
        poll_interval = 0.1
        start_time = time.monotonic()
        remaining_hwnds = set(posted_hwnds)

        while time.monotonic() - start_time < timeout_sec:
            newly_closed: Set[int] = set()
            for hwnd in remaining_hwnds:
                # 若窗口已销毁、已变为不可见或被 cloaked，视为主窗口已关闭
                if (
                    not self.api.is_window(hwnd)
                    or not self.api.is_window_visible(hwnd)
                    or self.api.is_window_cloaked(hwnd)
                ):
                    newly_closed.add(hwnd)

            remaining_hwnds -= newly_closed
            if not remaining_hwnds:
                break
            self.api.sleep(poll_interval)

        closed_count = len(posted_hwnds) - len(remaining_hwnds)
        # 仍保留的窗口数 = 未关闭的已发送窗口数 + 发送失败的窗口数
        remaining_count = len(remaining_hwnds) + failed_post_count

        # 4. 统计结果并生成脱敏的友好中文提示
        if remaining_count == 0:
            return WindowCloseResult(
                status=CloseStatus.ALL_CLOSED,
                matched_count=matched_count,
                closed_count=closed_count,
                remaining_count=0,
                failed_post_count=0,
                user_message=f"已关闭该软件的 {matched_count} 个主窗口"
            )
        elif closed_count > 0:
            fail_suffix = f"（含 {failed_post_count} 个发送失败）" if failed_post_count > 0 else "，可能正在等待用户确认"
            return WindowCloseResult(
                status=CloseStatus.SOME_REMAINING,
                matched_count=matched_count,
                closed_count=closed_count,
                remaining_count=remaining_count,
                failed_post_count=failed_post_count,
                user_message=f"已关闭 {closed_count} 个主窗口，但仍有 {remaining_count} 个主窗口未关闭{fail_suffix}"
            )
        else:
            fail_suffix = f"（含 {failed_post_count} 个发送失败）" if failed_post_count > 0 else "，可能正在等待用户确认"
            return WindowCloseResult(
                status=CloseStatus.ALL_REMAINING,
                matched_count=matched_count,
                closed_count=0,
                remaining_count=remaining_count,
                failed_post_count=failed_post_count,
                user_message=f"已发送关闭请求，但仍有 {remaining_count} 个主窗口未关闭{fail_suffix}"
            )
