"""第十八轮核心缺陷回归测试：Win32 API 原型安全、COM 生命周期与“需要处理”状态机。"""

import ctypes
from ctypes import wintypes
import json
import os
import subprocess
import sys
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from src.catalog.models import AppEntry, LaunchType, make_stable_identity
from src.catalog.manager import CatalogManager
from src.catalog.windows_metadata import (
    _ComScope,
    _setup_win32_api,
    _DWORD_PTR,
    _HRESULT,
    _hicon_to_pillow,
    load_shell_icon,
)
from src.ui_qt.presentation.apps import AppBucket, build_app_items


# =================================================================================================
# A. Win32 / COM 图标 API 安全与生命周期测试
# =================================================================================================

def test_win32_api_prototypes_declared_explicitly():
    """验证关键 Win32/COM API 的 argtypes 和 restype 均已显式声明，杜绝 64 位默认截断。"""
    if sys.platform != "win32":
        pytest.skip("仅在 Windows 平台验证原生 API 声明")

    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32
    shell32 = ctypes.windll.shell32
    ole32 = ctypes.windll.ole32

    # 1. user32
    assert user32.GetDC.argtypes == [wintypes.HWND]
    assert user32.GetDC.restype == wintypes.HDC
    assert user32.ReleaseDC.argtypes == [wintypes.HWND, wintypes.HDC]
    assert user32.ReleaseDC.restype == ctypes.c_int
    assert user32.DrawIconEx.argtypes == [
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
    assert user32.DrawIconEx.restype == wintypes.BOOL
    assert user32.DestroyIcon.argtypes == [wintypes.HICON]
    assert user32.DestroyIcon.restype == wintypes.BOOL

    # 2. gdi32
    assert gdi32.CreateCompatibleDC.argtypes == [wintypes.HDC]
    assert gdi32.CreateCompatibleDC.restype == wintypes.HDC
    assert gdi32.DeleteDC.argtypes == [wintypes.HDC]
    assert gdi32.DeleteDC.restype == wintypes.BOOL
    assert gdi32.CreateCompatibleBitmap.argtypes == [wintypes.HDC, ctypes.c_int, ctypes.c_int]
    assert gdi32.CreateCompatibleBitmap.restype == wintypes.HBITMAP
    assert gdi32.SelectObject.argtypes == [wintypes.HDC, wintypes.HGDIOBJ]
    assert gdi32.SelectObject.restype == wintypes.HGDIOBJ
    assert gdi32.GetDIBits.argtypes == [
        wintypes.HDC,
        wintypes.HBITMAP,
        wintypes.UINT,
        wintypes.UINT,
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.UINT,
    ]
    assert gdi32.GetDIBits.restype == ctypes.c_int
    assert gdi32.DeleteObject.argtypes == [wintypes.HGDIOBJ]
    assert gdi32.DeleteObject.restype == wintypes.BOOL

    # 3. shell32
    assert shell32.SHGetFileInfoW.argtypes == [
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.UINT,
        wintypes.UINT,
    ]
    assert shell32.SHGetFileInfoW.restype == _DWORD_PTR
    assert shell32.SHParseDisplayName.argtypes == [
        wintypes.LPCWSTR,
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p),
        wintypes.ULONG,
        ctypes.POINTER(wintypes.ULONG),
    ]
    assert shell32.SHParseDisplayName.restype == _HRESULT
    assert shell32.SHCreateItemFromParsingName.argtypes == [
        wintypes.LPCWSTR,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    assert shell32.SHCreateItemFromParsingName.restype == _HRESULT

    # 4. ole32
    assert ole32.CoInitialize.argtypes == [ctypes.c_void_p]
    assert ole32.CoInitialize.restype == _HRESULT
    assert ole32.CoUninitialize.argtypes == []
    assert ole32.CoUninitialize.restype is None
    assert ole32.CoTaskMemFree.argtypes == [ctypes.c_void_p]
    assert ole32.CoTaskMemFree.restype is None


def test_pointer_width_in_64bit():
    """验证在 64 位 Python 环境中，指针与 DWORD_PTR 宽度均为 8 字节。"""
    if sys.maxsize > 2**32:
        assert ctypes.sizeof(ctypes.c_void_p) == 8
        assert ctypes.sizeof(_DWORD_PTR) == 8
        assert ctypes.sizeof(wintypes.HDC) == 8
        assert ctypes.sizeof(wintypes.HBITMAP) == 8
        assert ctypes.sizeof(wintypes.HICON) == 8


def test_com_scope_lifecycle_rules():
    """验证 _ComScope 严格遵守 COM 生命周期规则：
    - S_OK (0) 与 S_FALSE (1) 必须配对 CoUninitialize
    - RPC_E_CHANGED_MODE (-2147417850) 绝不反初始化
    - 其它失败绝不反初始化
    """
    if sys.platform != "win32":
        pytest.skip("仅在 Windows 平台测试")

    ole32 = ctypes.windll.ole32

    # 1. 成功初始化 S_OK
    with patch.object(ole32, "CoInitialize", return_value=0) as mock_init, \
         patch.object(ole32, "CoUninitialize") as mock_uninit:
        with _ComScope():
            pass
        mock_init.assert_called_once()
        mock_uninit.assert_called_once()

    # 2. 嵌套/已初始化 S_FALSE (1)
    with patch.object(ole32, "CoInitialize", return_value=1) as mock_init, \
         patch.object(ole32, "CoUninitialize") as mock_uninit:
        with _ComScope():
            pass
        mock_init.assert_called_once()
        mock_uninit.assert_called_once()

    # 3. 冲突模式 RPC_E_CHANGED_MODE
    with patch.object(ole32, "CoInitialize", return_value=-2147417850) as mock_init, \
         patch.object(ole32, "CoUninitialize") as mock_uninit:
        with _ComScope():
            pass
        mock_init.assert_called_once()
        mock_uninit.assert_not_called()

    # 4. 其他错误失败 E_OUTOFMEMORY (-2147024882)
    with patch.object(ole32, "CoInitialize", return_value=-2147024882) as mock_init, \
         patch.object(ole32, "CoUninitialize") as mock_uninit:
        with _ComScope():
            pass
        mock_init.assert_called_once()
        mock_uninit.assert_not_called()


def test_hicon_to_pillow_resource_cleanup_on_failure():
    """模拟 _hicon_to_pillow 各环节失败，验证 GDI 资源正确释放且不重复释放。"""
    if sys.platform != "win32":
        pytest.skip("仅在 Windows 平台测试")

    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32

    # 模拟在 GetDIBits 处失败
    with patch.object(user32, "GetDC", return_value=1001), \
         patch.object(user32, "ReleaseDC") as mock_rel_dc, \
         patch.object(gdi32, "CreateCompatibleDC", return_value=1002), \
         patch.object(gdi32, "DeleteDC") as mock_del_dc, \
         patch.object(gdi32, "CreateCompatibleBitmap", return_value=1003), \
         patch.object(gdi32, "DeleteObject") as mock_del_obj, \
         patch.object(gdi32, "SelectObject", side_effect=[9999, 9999]) as mock_sel, \
         patch.object(user32, "DrawIconEx", return_value=True), \
         patch.object(gdi32, "GetDIBits", return_value=0):  # 模拟失败

        res = _hicon_to_pillow(5555, 36)
        assert res is None

        # 验证原位图被选回
        assert mock_sel.call_count == 2
        mock_sel.assert_called_with(1002, 9999)
        # 验证位图被删除一次
        mock_del_obj.assert_called_once_with(1003)
        # 验证内存 DC 被删除一次
        mock_del_dc.assert_called_once_with(1002)
        # 验证屏幕 DC 被释放一次
        mock_rel_dc.assert_called_once_with(None, 1001)


def test_subprocess_icon_stress_loop():
    """在独立子进程中循环提取普通 EXE 图标 200 次，验证无 0xc0000374 堆损坏与 access violation。"""
    if sys.platform != "win32":
        pytest.skip("仅在 Windows 平台执行原生压力测试")

    script = """
import sys
import os
from src.catalog.windows_metadata import load_shell_icon

exe_path = os.path.expandvars(r"%SystemRoot%\\System32\\notepad.exe")
if not os.path.exists(exe_path):
    exe_path = sys.executable

for i in range(200):
    img = load_shell_icon(exe_path, "win32", 36)
    if img is None:
        sys.exit(2)

print("SUCCESS_200")
sys.exit(0)
"""
    cmd = [sys.executable, "-c", script]
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=os.getcwd(), timeout=30)
    assert proc.returncode == 0, f"子进程崩溃或失败 (退出码 {hex(proc.returncode & 0xFFFFFFFF)}):\n{proc.stderr}"
    assert "SUCCESS_200" in proc.stdout


def test_subprocess_store_branch_stress_loop():
    """在独立子进程中对 Store/Shell Item 分支进行多次提取，验证异常捕获与资源安全。"""
    if sys.platform != "win32":
        pytest.skip("仅在 Windows 平台执行原生压力测试")

    script = """
import sys
from src.catalog.windows_metadata import load_shell_icon

for i in range(50):
    # 使用 Windows 常见 AUMID 或虚拟引用
    img = load_shell_icon("Microsoft.WindowsCalculator_8wekyb3d8bbwe!App", "store_aumid", 36)

print("STORE_DONE")
sys.exit(0)
"""
    cmd = [sys.executable, "-c", script]
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=os.getcwd(), timeout=30)
    assert proc.returncode == 0, f"Store 分支压力测试异常:\n{proc.stderr}"
    assert "STORE_DONE" in proc.stdout


# =================================================================================================
# B. “需要处理” (requires_user_attention) 状态机测试
# =================================================================================================

def test_passive_invalid_scanned_apps_remain_hidden():
    """验证 58 个普通被动扫描 invalid 条目保持隐藏，绝不污染‘需要处理’。"""
    entries = []
    # 模拟 58 个不同类型的被动扫描无效条目
    for i in range(58):
        name = f"SystemTool_{i}" if i < 20 else f"Helper_Doc_{i}"
        entries.append(
            AppEntry(
                app_id=f"passive-{i}",
                display_name=name,
                exe_path=rf"C:\fake\path_{i}.lnk",
                launch_target=rf"C:\fake\path_{i}.lnk",
                launch_type=LaunchType.SHORTCUT,
                source="StartMenu",
                is_user_managed=False,
                is_enabled=False,
                is_valid=False,
                requires_user_attention=False,
                is_restricted=False,
                validation_status="invalid",
                authorization_block_reason="快捷方式包含启动参数或目标无法解析",
            )
        )

    items = build_app_items(entries)
    # 58 条全部保持隐藏
    assert len(items) == 0, f"被动 invalid 条目应当全部隐藏，但出现了 {len(items)} 条"


def test_user_authorization_failure_sets_attention_and_persists(tmp_path):
    """验证用户主动在未允许中点击允许，校验失败后设置 requires_user_attention=True 并持久化。"""
    cat_file = tmp_path / "catalog.json"
    entry = AppEntry(
        app_id="app-test-toggle",
        display_name="未允许测试软件",
        exe_path=r"C:\fake\test.lnk",
        launch_target=r"C:\fake\test.lnk",
        launch_type=LaunchType.SHORTCUT,
        source="StartMenu",
        is_user_managed=False,
        is_enabled=False,
        is_valid=True,
        requires_user_attention=False,
    )
    with open(cat_file, "w", encoding="utf-8") as f:
        json.dump([entry.model_dump()], f)

    mgr = CatalogManager(catalog_file_path=str(cat_file))
    assert mgr.apps["app-test-toggle"].requires_user_attention is False

    # 模拟校验失败（例如快捷方式包含参数）
    with patch.object(mgr, "_validate_launchable", return_value=(False, "快捷方式包含启动参数")):
        res = mgr.set_app_enabled("app-test-toggle", True)
        assert res is False

    # 验证 requires_user_attention 被置为 True
    assert mgr.apps["app-test-toggle"].requires_user_attention is True
    assert mgr.apps["app-test-toggle"].is_valid is False
    assert mgr.apps["app-test-toggle"].is_enabled is False

    # 重新从磁盘加载（模拟重启应用）
    mgr_reloaded = CatalogManager(catalog_file_path=str(cat_file))
    reloaded_entry = mgr_reloaded.apps["app-test-toggle"]
    assert reloaded_entry.requires_user_attention is True

    # 验证 build_app_items 呈现为 NEEDS_ATTENTION
    items = build_app_items(mgr_reloaded.get_all_apps())
    assert len(items) == 1
    assert items[0].bucket is AppBucket.NEEDS_ATTENTION
    assert items[0].can_toggle is False


def test_dangerous_app_never_enters_attention_even_if_operated(tmp_path):
    """危险或受限应用即使用户尝试操作，也绝不得进入 requires_user_attention，保持永久隐藏。"""
    cat_file = tmp_path / "catalog.json"
    entry = AppEntry(
        app_id="app-cmd",
        display_name="命令提示符",
        exe_path=r"C:\Windows\System32\cmd.exe",
        launch_target=r"C:\Windows\System32\cmd.exe",
        launch_type=LaunchType.WIN32,
        source="StartMenu",
        is_user_managed=False,
        is_enabled=False,
        is_valid=True,
        requires_user_attention=False,
        is_restricted=True,
        validation_status="dangerous",
    )
    with open(cat_file, "w", encoding="utf-8") as f:
        json.dump([entry.model_dump()], f)

    mgr = CatalogManager(catalog_file_path=str(cat_file))
    with patch.object(mgr, "_validate_launchable", return_value=(False, "高危系统程序")):
        res = mgr.set_app_enabled("app-cmd", True)
        assert res is False

    # 危险程序绝不标记 attention
    assert mgr.apps["app-cmd"].requires_user_attention is False
    items = build_app_items([mgr.apps["app-cmd"]])
    assert len(items) == 0


def test_user_manual_invalid_enters_attention():
    """用户手动添加的应用一旦失效，直接进入‘需要处理’。"""
    entry = AppEntry(
        app_id="manual-invalid",
        display_name="绿色应用",
        exe_path=r"C:\fake\deleted.exe",
        launch_target=r"C:\fake\deleted.exe",
        launch_type=LaunchType.PORTABLE,
        source="Manual",
        is_user_managed=True,
        is_enabled=False,
        is_valid=False,
        requires_user_attention=False,
    )
    items = build_app_items([entry])
    assert len(items) == 1
    assert items[0].bucket is AppBucket.NEEDS_ATTENTION
    assert items[0].repair_action_label == "重新选择"


def test_previously_enabled_app_becoming_invalid_enters_attention(tmp_path):
    """以前已被允许的应用，后来因为文件移动普通失效，重验时进入‘需要处理’。"""
    cat_file = tmp_path / "catalog.json"
    entry = AppEntry(
        app_id="app-moved",
        display_name="已允许应用",
        exe_path=r"C:\fake\moved.exe",
        launch_target=r"C:\fake\moved.exe",
        launch_type=LaunchType.WIN32,
        source="StartMenu",
        is_user_managed=False,
        is_enabled=True,
        is_valid=True,
        requires_user_attention=False,
    )
    with open(cat_file, "w", encoding="utf-8") as f:
        json.dump([entry.model_dump()], f)

    mgr = CatalogManager(catalog_file_path=str(cat_file))
    # 模拟重验：文件已不存在
    with patch.object(mgr, "_validate_launchable", return_value=(False, "应用文件不存在")):
        mgr.revalidate_all()

    assert mgr.apps["app-moved"].is_valid is False
    assert mgr.apps["app-moved"].is_enabled is False
    assert mgr.apps["app-moved"].requires_user_attention is True

    items = build_app_items(mgr.get_all_apps())
    assert len(items) == 1
    assert items[0].bucket is AppBucket.NEEDS_ATTENTION


def test_successful_repair_clears_attention_and_moves_to_not_allowed(tmp_path):
    """修复成功后必须清除 attention 状态，保持未授权，并转入‘未允许’。"""
    cat_file = tmp_path / "catalog.json"
    entry = AppEntry(
        app_id="app-repair-target",
        display_name="失效扫描条目",
        exe_path=r"C:\fake\old.lnk",
        launch_target=r"C:\fake\old.lnk",
        launch_type=LaunchType.SHORTCUT,
        source="StartMenu",
        is_user_managed=False,
        is_enabled=False,
        is_valid=False,
        requires_user_attention=True,
        validation_status="invalid",
    )
    with open(cat_file, "w", encoding="utf-8") as f:
        json.dump([entry.model_dump()], f)

    mgr = CatalogManager(catalog_file_path=str(cat_file))
    new_exe = tmp_path / "RealApp.exe"
    new_exe.write_text("binary")

    repaired = mgr.replace_invalid_app_target("app-repair-target", str(new_exe))
    assert repaired.is_valid is True
    assert repaired.is_enabled is False
    assert repaired.requires_user_attention is False
    assert repaired.is_user_managed is True

    items = build_app_items([repaired])
    assert len(items) == 1
    assert items[0].bucket is AppBucket.NOT_ALLOWED
    assert items[0].can_toggle is True


def test_refresh_scan_preserves_legitimate_attention_only(tmp_path):
    """refresh_scan 合并条目时，保留合法的 attention 状态，绝不把普通扫描垃圾误标为 attention。"""
    cat_file = tmp_path / "catalog.json"
    entry1 = AppEntry(
        app_id="app-att",
        display_name="待处理应用",
        exe_path=r"C:\fake\app1.lnk",
        launch_target=r"C:\fake\app1.lnk",
        launch_type=LaunchType.SHORTCUT,
        stable_identity="file:c:/fake/app1.lnk",
        source="StartMenu",
        is_valid=False,
        requires_user_attention=True,
    )
    with open(cat_file, "w", encoding="utf-8") as f:
        json.dump([entry1.model_dump()], f)

    mgr = CatalogManager(catalog_file_path=str(cat_file))

    # 扫描发现条目（新条目与普通垃圾条目）
    scanned_same = AppEntry(
        app_id="new-id-1",
        display_name="待处理应用",
        exe_path=r"C:\fake\app1.lnk",
        launch_target=r"C:\fake\app1.lnk",
        launch_type=LaunchType.SHORTCUT,
        stable_identity="file:c:/fake/app1.lnk",
        source="StartMenu",
        is_valid=False,
    )
    scanned_trash = AppEntry(
        app_id="trash-id",
        display_name="控制面板",
        exe_path=r"C:\fake\control.lnk",
        launch_target=r"C:\fake\control.lnk",
        launch_type=LaunchType.SHORTCUT,
        stable_identity="file:c:/fake/control.lnk",
        source="StartMenu",
        is_valid=False,
    )

    with patch("src.catalog.scanner.SoftwareScanner.scan_all", return_value=[scanned_same, scanned_trash]):
        res = mgr.refresh_scan(user_agreed=True)

    # 相同身份的合法条目保留 attention
    assert mgr.apps["app-att"].requires_user_attention is True
    # 普通新垃圾条目保持 attention=False
    assert mgr.apps["trash-id"].requires_user_attention is False
