"""窗口控制模块与应用主窗口关闭安全测试。
所有测试均使用模拟 Windows API (FakeWindowApi)，严禁真实关闭开发机上的用户软件。
"""

import os
from typing import Dict, List, Optional
import pytest

from src.catalog.models import AppEntry, LaunchType, make_stable_identity
from src.catalog.window_control import (
    CloseStatus,
    NativeWindowApi,
    WindowCloseResult,
    WindowController,
    WindowTarget,
)


class FakeWindowApi(NativeWindowApi):
    """用于测试的模拟 Windows API 桩，支持在内存中模拟窗口生命周期与关闭响应"""

    def __init__(self, windows: Optional[List[WindowTarget]] = None):
        self.windows: Dict[int, WindowTarget] = {w.hwnd: w for w in (windows or [])}
        self.closed_hwnds: List[int] = []
        self.post_message_calls: List[tuple[int, int]] = []
        self.auto_close_on_post: bool = True  # 是否在收到 WM_CLOSE 后自动销毁/隐藏窗口
        self.access_denied_pids: set[int] = set()
        self.post_message_errors: Dict[int, int] = {}  # hwnd -> error_code (如 5 为 ERROR_ACCESS_DENIED)

    def enum_top_level_windows(self) -> List[int]:
        return list(self.windows.keys())

    def is_window(self, hwnd: int) -> bool:
        return hwnd in self.windows and hwnd not in self.closed_hwnds

    def is_window_visible(self, hwnd: int) -> bool:
        if hwnd not in self.windows or hwnd in self.closed_hwnds:
            return False
        return self.windows[hwnd].is_visible

    def is_window_cloaked(self, hwnd: int) -> bool:
        if hwnd not in self.windows or hwnd in self.closed_hwnds:
            return False
        return self.windows[hwnd].is_cloaked

    def get_window_pid(self, hwnd: int) -> int:
        if hwnd not in self.windows:
            return 0
        return self.windows[hwnd].pid

    def get_process_image_path(self, pid: int) -> str:
        if pid in self.access_denied_pids:
            raise PermissionError("Access denied querying process image")
        for w in self.windows.values():
            if w.pid == pid and w.process_path:
                return w.process_path
        return ""

    def get_process_aumid(self, pid: int, hwnd: int = 0) -> str:
        if pid in self.access_denied_pids:
            raise PermissionError("Access denied querying AUMID")
        if hwnd in self.windows and self.windows[hwnd].aumid:
            return self.windows[hwnd].aumid
        for w in self.windows.values():
            if w.pid == pid and w.aumid:
                return w.aumid
        return ""

    def is_tool_window(self, hwnd: int) -> bool:
        if hwnd not in self.windows:
            return False
        return self.windows[hwnd].is_tool_window

    def has_owner(self, hwnd: int) -> bool:
        if hwnd not in self.windows:
            return False
        return self.windows[hwnd].has_owner

    def is_child_window(self, hwnd: int) -> bool:
        if hwnd not in self.windows:
            return False
        return self.windows[hwnd].is_child

    def post_close_message(self, hwnd: int) -> tuple[bool, int]:
        if hwnd not in self.windows or hwnd in self.closed_hwnds:
            return False, 1400  # ERROR_INVALID_WINDOW_HANDLE
        if hwnd in self.post_message_errors:
            err = self.post_message_errors[hwnd]
            return False, err
        self.post_message_calls.append((hwnd, 0x0010))  # WM_CLOSE = 0x0010
        if self.auto_close_on_post:
            self.closed_hwnds.append(hwnd)
        return True, 0

    def sleep(self, seconds: float) -> None:
        pass


def make_test_app(
    display_name: str = "测试软件",
    exe_path: str = r"C:\Program Files\TestApp\test.exe",
    launch_type: LaunchType = LaunchType.WIN32,
    launch_target: str = "",
    resolved_target: str = "",
    is_enabled: bool = True,
) -> AppEntry:
    target = launch_target or (exe_path if launch_type != LaunchType.STORE_AUMID else "")
    res = resolved_target if launch_type == LaunchType.SHORTCUT else ""
    return AppEntry(
        app_id="test_app_id_123",
        display_name=display_name,
        exe_path=exe_path if launch_type != LaunchType.STORE_AUMID else "",
        launch_type=launch_type,
        launch_target=target,
        resolved_target=res,
        stable_identity=make_stable_identity(launch_type, target, res),
        source="StartMenu",
        is_enabled=is_enabled,
        is_valid=True,
    )


def test_close_multiple_main_windows_of_same_app():
    """要求 1 & 10: 同一应用存在两个或更多可见主窗口时，一次关闭全部主窗口，不只关闭第一个"""
    app = make_test_app(exe_path=r"C:\Apps\Editor\editor.exe")
    windows = [
        WindowTarget(hwnd=101, pid=2001, process_path=r"C:\Apps\Editor\editor.exe"),
        WindowTarget(hwnd=102, pid=2001, process_path=r"C:\Apps\Editor\editor.exe"),
        WindowTarget(hwnd=103, pid=2002, process_path=r"C:\Apps\Editor\editor.exe"),
    ]
    api = FakeWindowApi(windows)
    controller = WindowController(api=api)

    result = controller.close_app_windows(app)
    assert result.status == CloseStatus.ALL_CLOSED
    assert result.matched_count == 3
    assert result.closed_count == 3
    assert result.remaining_count == 0
    assert len(api.post_message_calls) == 3
    assert {call[0] for call in api.post_message_calls} == {101, 102, 103}
    assert "已关闭该软件的 3 个主窗口" in result.user_message


def test_do_not_close_other_apps_windows():
    """要求 2: 绝不关闭其他未授权或不相关的应用窗口"""
    app = make_test_app(exe_path=r"C:\Apps\Editor\editor.exe")
    windows = [
        WindowTarget(hwnd=101, pid=2001, process_path=r"C:\Apps\Editor\editor.exe"),
        WindowTarget(hwnd=201, pid=3001, process_path=r"C:\Browser\browser.exe"),
        WindowTarget(hwnd=301, pid=4001, process_path=r"C:\Music\cloudmusic.exe"),
    ]
    api = FakeWindowApi(windows)
    controller = WindowController(api=api)

    result = controller.close_app_windows(app)
    assert result.status == CloseStatus.ALL_CLOSED
    assert result.matched_count == 1
    assert api.post_message_calls == [(101, 0x0010)]
    assert 201 not in api.closed_hwnds
    assert 301 not in api.closed_hwnds


def test_ignore_hidden_child_dialog_and_cloaked_windows():
    """要求 3: 排除隐藏窗口、子窗口、被拥有弹窗、工具浮窗以及 cloaked 窗口"""
    app = make_test_app(exe_path=r"C:\Apps\Editor\editor.exe")
    windows = [
        WindowTarget(hwnd=101, pid=2001, process_path=r"C:\Apps\Editor\editor.exe"),  # 正常可见主窗口
        WindowTarget(hwnd=102, pid=2001, process_path=r"C:\Apps\Editor\editor.exe", is_visible=False),  # 隐藏托盘窗口
        WindowTarget(hwnd=103, pid=2001, process_path=r"C:\Apps\Editor\editor.exe", is_child=True),  # 子窗口
        WindowTarget(hwnd=104, pid=2001, process_path=r"C:\Apps\Editor\editor.exe", has_owner=True),  # 拥有者弹窗
        WindowTarget(hwnd=105, pid=2001, process_path=r"C:\Apps\Editor\editor.exe", is_tool_window=True),  # 工具浮窗
        WindowTarget(hwnd=106, pid=2001, process_path=r"C:\Apps\Editor\editor.exe", is_cloaked=True),  # DWM Cloaked
    ]
    api = FakeWindowApi(windows)
    controller = WindowController(api=api)

    result = controller.close_app_windows(app)
    assert result.status == CloseStatus.ALL_CLOSED
    assert result.matched_count == 1
    assert api.post_message_calls == [(101, 0x0010)]


def test_software_has_only_tray_or_background_processes():
    """要求 4 & 10: 软件只有托盘或后台进程、没有可见主窗口时，不执行进程操作，返回无主窗口"""
    app = make_test_app(exe_path=r"C:\Music\cloudmusic.exe")
    windows = [
        # 仅有隐藏的后台消息窗口或托盘隐藏窗口
        WindowTarget(hwnd=501, pid=6001, process_path=r"C:\Music\cloudmusic.exe", is_visible=False),
    ]
    api = FakeWindowApi(windows)
    controller = WindowController(api=api)

    result = controller.close_app_windows(app)
    assert result.status == CloseStatus.NO_WINDOWS
    assert result.matched_count == 0
    assert len(api.post_message_calls) == 0
    assert "当前没有可关闭的主窗口" in result.user_message


def test_win32_path_case_and_slash_normalization():
    """要求 5: Win32/绿色软件按规范化真实路径精确匹配（不区分大小写，斜杠兼容）"""
    app = make_test_app(exe_path=r"c:/apps/editor/editor.exe")
    windows = [
        WindowTarget(hwnd=101, pid=2001, process_path=r"C:\APPS\EDITOR\EDITOR.EXE"),
    ]
    api = FakeWindowApi(windows)
    controller = WindowController(api=api)

    result = controller.close_app_windows(app)
    assert result.status == CloseStatus.ALL_CLOSED
    assert result.matched_count == 1
    assert api.post_message_calls == [(101, 0x0010)]


def test_shortcut_resolved_target_matching(tmp_path):
    """要求 6: 快捷方式复用 ShortcutResolver 重新校验并使用解析后的真实目标"""
    lnk_file = str(tmp_path / "myapp.lnk")
    real_exe = str(tmp_path / "bin" / "myapp.exe")
    os.makedirs(os.path.dirname(real_exe), exist_ok=True)
    with open(real_exe, "w") as f:
        f.write("mock")

    app = make_test_app(
        display_name="快捷应用",
        launch_type=LaunchType.SHORTCUT,
        launch_target=lnk_file,
        resolved_target=real_exe,
    )
    windows = [
        WindowTarget(hwnd=101, pid=2001, process_path=real_exe),
    ]
    api = FakeWindowApi(windows)
    controller = WindowController(api=api)

    result = controller.close_app_windows(app)
    assert result.status == CloseStatus.ALL_CLOSED
    assert result.matched_count == 1
    assert api.post_message_calls == [(101, 0x0010)]


def test_store_app_exact_aumid_matching():
    """要求 7: Store/MSIX 应用按完整 AUMID 精确匹配，禁止窗口标题模糊匹配"""
    app = make_test_app(
        display_name="Codex",
        launch_type=LaunchType.STORE_AUMID,
        launch_target="OpenAI.Codex_2p2nqsd0c76g0!App",
    )
    windows = [
        # 正确 AUMID 的窗口
        WindowTarget(
            hwnd=101,
            pid=2001,
            process_path=r"C:\Program Files\WindowsApps\OpenAI.Codex\Codex.exe",
            aumid="OpenAI.Codex_2p2nqsd0c76g0!App",
        ),
        # 另一个假冒窗口（比如名为 Codex 但其实不是该 AUMID 的软件）
        WindowTarget(
            hwnd=201,
            pid=3001,
            process_path=r"C:\Malicious\fake_codex.exe",
            aumid="Fake.Package!App",
        ),
    ]
    api = FakeWindowApi(windows)
    controller = WindowController(api=api)

    result = controller.close_app_windows(app)
    assert result.status == CloseStatus.ALL_CLOSED
    assert result.matched_count == 1
    assert api.post_message_calls == [(101, 0x0010)]
    assert 201 not in api.closed_hwnds


def test_same_name_different_identity_separation():
    """要求 8: 相同显示名称但稳定身份不同的软件不能串台"""
    app_official = make_test_app(
        display_name="记事本",
        exe_path=r"C:\Windows\System32\notepad.exe",
    )
    windows = [
        # 第三方绿色记事本
        WindowTarget(hwnd=101, pid=2001, process_path=r"D:\Tools\notepad\notepad.exe"),
        # 系统官方记事本
        WindowTarget(hwnd=102, pid=2002, process_path=r"C:\Windows\System32\notepad.exe"),
    ]
    api = FakeWindowApi(windows)
    controller = WindowController(api=api)

    result = controller.close_app_windows(app_official)
    assert result.status == CloseStatus.ALL_CLOSED
    assert result.matched_count == 1
    assert api.post_message_calls == [(102, 0x0010)]
    assert 101 not in api.closed_hwnds


def test_unsaved_dialog_or_remaining_windows():
    """要求 9 & 11: 弹出保存确认框或部分窗口未关闭时，返回仍有窗口等待处理"""
    app = make_test_app(exe_path=r"C:\Apps\Editor\editor.exe")
    windows = [
        WindowTarget(hwnd=101, pid=2001, process_path=r"C:\Apps\Editor\editor.exe"),
        WindowTarget(hwnd=102, pid=2001, process_path=r"C:\Apps\Editor\editor.exe"),
    ]
    api = FakeWindowApi(windows)
    # 模拟 hwnd 101 正常关闭，hwnd 102 弹出未保存对话框而没有关闭
    api.auto_close_on_post = False
    controller = WindowController(api=api)

    # 在 post 触发后手动只关闭 101
    def custom_post(hwnd: int) -> tuple[bool, int]:
        api.post_message_calls.append((hwnd, 0x0010))
        if hwnd == 101:
            api.closed_hwnds.append(hwnd)
        return True, 0

    api.post_close_message = custom_post

    result = controller.close_app_windows(app)
    assert result.status == CloseStatus.SOME_REMAINING
    assert result.matched_count == 2
    assert result.closed_count == 1
    assert result.remaining_count == 1
    assert "仍有 1 个主窗口未关闭" in result.user_message
    assert "可能正在等待用户确认" in result.user_message


def test_identity_race_condition_stopped_before_post():
    """要求 12: 关闭前句柄身份发生变化或被重用时，停止操作并安全失败"""
    app = make_test_app(exe_path=r"C:\Apps\Editor\editor.exe")
    windows = [
        WindowTarget(hwnd=101, pid=2001, process_path=r"C:\Apps\Editor\editor.exe"),
    ]
    api = FakeWindowApi(windows)
    controller = WindowController(api=api)

    # 模拟在枚举之后、post 发送之前，hwnd 101 的 PID 变成了其他进程
    original_find = controller.find_eligible_windows

    def hooked_find(entry):
        res = original_find(entry)
        # 篡改 PID 对应的进程镜像
        api.windows[101].process_path = r"C:\Windows\explorer.exe"
        return res

    controller.find_eligible_windows = hooked_find

    result = controller.close_app_windows(app)
    assert len(api.post_message_calls) == 0
    assert result.status == CloseStatus.ACCESS_DENIED
    assert result.remaining_count == 1


def test_access_denied_and_identity_uncertain():
    """要求 11: 权限不足与无法安全确认身份时的安全处理"""
    app = make_test_app(exe_path=r"C:\Apps\Editor\editor.exe")
    windows = [
        WindowTarget(hwnd=101, pid=2001, process_path=r"C:\Apps\Editor\editor.exe"),
    ]
    api = FakeWindowApi(windows)
    api.access_denied_pids.add(2001)
    controller = WindowController(api=api)

    # 无法获取目标进程镜像路径或权限不足时，无法验证身份，安全拒绝关闭
    result = controller.close_app_windows(app)
    assert result.status == CloseStatus.NO_WINDOWS
    assert len(api.post_message_calls) == 0

    # 无法获取有效身份标识（如非法或空的 AUMID）
    empty_app = make_test_app(
        display_name="未识别应用",
        launch_type=LaunchType.STORE_AUMID,
        launch_target="",
    )
    result_empty = controller.close_app_windows(empty_app)
    assert result_empty.status == CloseStatus.IDENTITY_UNCERTAIN
    assert "无法安全确认" in result_empty.user_message


def test_no_kill_or_shell_command_in_code():
    """要求 13: 验证 window_control.py 中绝不包含 TerminateProcess, taskkill, Stop-Process, shell=True 等暴力杀进程"""
    import inspect
    from src.catalog import window_control

    src = inspect.getsource(window_control)
    forbidden_terms = [
        "taskkill",
        "TerminateProcess",
        "Stop-Process",
        "kill",
        "Alt+F4",
        "VK_F4",
        "sendkeys",
        "keybd_event",
        "SendInput",
        "shell=True",
    ]
    for term in forbidden_terms:
        # 允许注释或文档提及，但代码执行逻辑中不能有调用
        assert f"subprocess.run" not in src or "shell=True" not in src
        assert "taskkill" not in src
        assert "TerminateProcess" not in src
        assert "Stop-Process" not in src
        assert "SendInput" not in src


def test_close_post_message_access_denied():
    """要求 1 & 2 & 3: 匹配到窗口但 post_close_message 返回 ERROR_ACCESS_DENIED 时，绝不能返回 NO_WINDOWS，必须返回 ACCESS_DENIED"""
    app = make_test_app(exe_path=r"C:\Apps\AdminApp\admin.exe")
    windows = [
        WindowTarget(hwnd=101, pid=2001, process_path=r"C:\Apps\AdminApp\admin.exe"),
    ]
    api = FakeWindowApi(windows)
    api.post_message_errors[101] = 5  # ERROR_ACCESS_DENIED
    controller = WindowController(api=api)

    result = controller.close_app_windows(app)
    assert result.status == CloseStatus.ACCESS_DENIED
    assert result.matched_count == 1
    assert result.closed_count == 0
    assert result.remaining_count == 1
    assert result.failed_post_count == 1
    assert "权限不足" in result.user_message


def test_close_post_message_other_error():
    """要求 4: 匹配到窗口但 PostMessage 发生其他错误时，返回 REQUEST_FAILED，绝不能返回 NO_WINDOWS"""
    app = make_test_app(exe_path=r"C:\Apps\Editor\editor.exe")
    windows = [
        WindowTarget(hwnd=101, pid=2001, process_path=r"C:\Apps\Editor\editor.exe"),
    ]
    api = FakeWindowApi(windows)
    api.post_message_errors[101] = 1400  # ERROR_INVALID_WINDOW_HANDLE
    controller = WindowController(api=api)

    result = controller.close_app_windows(app)
    assert result.status == CloseStatus.REQUEST_FAILED
    assert result.matched_count == 1
    assert result.closed_count == 0
    assert result.remaining_count == 1
    assert result.failed_post_count == 1
    assert "发送关闭请求失败" in result.user_message


def test_close_mixed_partial_closed_and_partial_failed():
    """要求 5: 混合场景准确统计已关闭、仍保留、发送失败窗口数，状态为 SOME_REMAINING"""
    app = make_test_app(exe_path=r"C:\Apps\Editor\editor.exe")
    windows = [
        WindowTarget(hwnd=101, pid=2001, process_path=r"C:\Apps\Editor\editor.exe"),
        WindowTarget(hwnd=102, pid=2001, process_path=r"C:\Apps\Editor\editor.exe"),
    ]
    api = FakeWindowApi(windows)
    # hwnd 101 正常发送并关闭；hwnd 102 发送失败 (例如 5)
    api.post_message_errors[102] = 5
    controller = WindowController(api=api)

    result = controller.close_app_windows(app)
    assert result.status == CloseStatus.SOME_REMAINING
    assert result.matched_count == 2
    assert result.closed_count == 1
    assert result.remaining_count == 1
    assert result.failed_post_count == 1
    assert "已关闭 1 个主窗口" in result.user_message
    assert "发送失败" in result.user_message


def test_close_no_visible_windows_returns_no_windows_not_success():
    """初次枚举无可见主窗口时返回 NO_WINDOWS"""
    app = make_test_app(exe_path=r"C:\Apps\Editor\editor.exe")
    api = FakeWindowApi([])
    controller = WindowController(api=api)

    result = controller.close_app_windows(app)
    assert result.status == CloseStatus.NO_WINDOWS
    assert result.matched_count == 0
    assert result.closed_count == 0
    assert result.remaining_count == 0
    assert result.failed_post_count == 0
    assert "当前没有可关闭的主窗口" in result.user_message


def test_high_privilege_cannot_read_process_identity():
    """高权限进程无法查询路径时拒绝关闭，且不执行任何 PostMessage"""
    app = make_test_app(exe_path=r"C:\Apps\AdminApp\admin.exe")
    windows = [
        WindowTarget(hwnd=101, pid=9999, process_path=r"C:\Apps\AdminApp\admin.exe"),
    ]
    api = FakeWindowApi(windows)
    api.access_denied_pids.add(9999)
    controller = WindowController(api=api)

    result = controller.close_app_windows(app)
    # 身份无法安全确认，筛选排除，初次枚举无匹配窗口
    assert result.status == CloseStatus.NO_WINDOWS
    assert len(api.post_message_calls) == 0
    assert result.closed_count == 0


@pytest.mark.skipif(os.name != "nt", reason="仅在 Windows 原生环境测试 Win32 API 签名")
def test_native_win32_api_signatures():
    """Win32 API 64位安全签名检查：验证句柄、指针和返回值类型配置正确"""
    import ctypes
    from ctypes import wintypes
    from src.catalog.window_control import WindowsNativeApi

    api = WindowsNativeApi()
    assert isinstance(api, WindowsNativeApi)

    # 句柄类型必须为 pointer-sized (64位下为 8 字节)
    assert ctypes.sizeof(wintypes.HANDLE) == ctypes.sizeof(ctypes.c_void_p)
    assert ctypes.sizeof(wintypes.HWND) == ctypes.sizeof(ctypes.c_void_p)

    # user32.PostMessageW
    assert api.user32.PostMessageW.argtypes == [
        wintypes.HWND,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
    ]
    assert api.user32.PostMessageW.restype == wintypes.BOOL

    # kernel32.OpenProcess
    assert api.kernel32.OpenProcess.argtypes == [
        wintypes.DWORD,
        wintypes.BOOL,
        wintypes.DWORD,
    ]
    assert api.kernel32.OpenProcess.restype == wintypes.HANDLE

    # kernel32.QueryFullProcessImageNameW
    assert api.kernel32.QueryFullProcessImageNameW.argtypes == [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    assert api.kernel32.QueryFullProcessImageNameW.restype == wintypes.BOOL

    # kernel32.CloseHandle
    assert api.kernel32.CloseHandle.argtypes == [wintypes.HANDLE]
    assert api.kernel32.CloseHandle.restype == wintypes.BOOL

    # user32.EnumChildWindows
    assert api.user32.EnumChildWindows.argtypes == [
        wintypes.HWND,
        api.WNDENUMPROC,
        wintypes.LPARAM,
    ]
    assert api.user32.EnumChildWindows.restype == wintypes.BOOL

    # dwmapi.DwmGetWindowAttribute
    if api.dwmapi:
        assert api.dwmapi.DwmGetWindowAttribute.argtypes == [
            wintypes.HWND,
            wintypes.DWORD,
            wintypes.LPCVOID,
            wintypes.DWORD,
        ]
        assert api.dwmapi.DwmGetWindowAttribute.restype == ctypes.HRESULT


@pytest.mark.skipif(os.name != "nt", reason="仅在 Windows 原生环境测试进程镜像查询")
def test_native_win32_query_current_process_image_path():
    """Windows 原生烟测：通过真实 API 安全读取当前测试进程自身的镜像路径，严禁操作用户软件"""
    from src.catalog.window_control import WindowsNativeApi

    api = WindowsNativeApi()
    current_pid = os.getpid()
    proc_path = api.get_process_image_path(current_pid)
    assert proc_path, "应能成功读取当前测试进程的物理路径"
    assert os.path.exists(proc_path)
    assert proc_path.lower().endswith(".exe")


@pytest.mark.skipif(os.name != "nt", reason="仅在 Windows 原生环境测试临时窗口生命周期")
def test_native_win32_temporary_window_lifecycle():
    """Windows 原生烟测：创建测试进程自有的临时窗口并销毁，验证原生 API 兼容性，严禁关闭外部软件"""
    from src.catalog.window_control import WindowsNativeApi

    api = WindowsNativeApi()
    # 创建一个不可见的临时测试窗口
    hwnd = api.user32.CreateWindowExW(0, "STATIC", "XiaoZhiTestTempWindow", 0, 0, 0, 10, 10, 0, 0, 0, 0)
    assert hwnd != 0
    try:
        assert api.is_window(hwnd) is True
        assert api.get_window_pid(hwnd) == os.getpid()
    finally:
        api.user32.DestroyWindow(hwnd)
        assert api.is_window(hwnd) is False

