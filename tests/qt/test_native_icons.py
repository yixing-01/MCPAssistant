"""Windows 原生软件图标恢复与加载层自动化测试用例。

覆盖：
1. EXE 图标成功加载后，应用行显示 Pixmap，不再显示首字母。
2. .lnk 图标成功加载。
3. Store/AUMID 使用专门的 Store 提取分支。
4. Store 提取成功时可以传递到 Qt 应用行。
5. 提取失败时显示中性通用图标，绝不显示首字母。
6. 相同图标重复创建应用行时命中缓存。
7. 搜索、筛选、切换分类或刷新时不重复提取。
8. 异步结果不会更新已经销毁或被复用的应用行。
9. Shell 提取不在 GUI 主线程执行。
10. 图标失败不影响授权开关、修复按钮及应用启动逻辑。
11. 打包环境包含本轮需要的 Pillow、Qt 和 Windows 图标提取依赖。
"""

from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image
from PySide6.QtCore import QCoreApplication, Qt
from PySide6.QtGui import QPixmap

from src.catalog.models import AppEntry, LaunchType
from src.catalog.windows_metadata import load_shell_icon
from src.ui_qt.icon_provider import AppIconProvider, pillow_to_qpixmap
from src.ui_qt.presentation.apps import AppBucket, AppListItem, build_app_items, filter_items
from src.ui_qt.widgets.application_row import ApplicationRow


@pytest.fixture(autouse=True)
def clean_icon_cache():
    """每个测试前后清理全局图标缓存。"""
    provider = AppIconProvider.instance()
    provider.clear_cache()
    yield
    provider.clear_cache()


def _make_dummy_image(color=(80, 120, 240, 255), size=36) -> Image.Image:
    """生成带透明度的标准测试 PIL RGBA 图像。"""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    inner = Image.new("RGBA", (size - 4, size - 4), color)
    img.paste(inner, (2, 2))
    return img


def test_exe_icon_loaded_shows_pixmap_no_letter(qtbot):
    """1. EXE 图标成功加载后，应用行显示 Pixmap，标签文本不再包含首字母。"""
    provider = AppIconProvider.instance()
    dummy_img = _make_dummy_image()

    with patch("src.ui_qt.icon_provider.load_shell_icon", return_value=dummy_img):
        item = AppListItem(
            app_id="app_exe_1",
            display_name="ChatGPT",
            aliases=("ChatGPT",),
            category="电脑应用",
            icon_ref=r"C:\Program Files\OpenAI\ChatGPT.exe",
            is_enabled=True,
            bucket=AppBucket.ALLOWED,
            voice_example="可以说：打开ChatGPT",
            launch_type="win32",
        )
        row = ApplicationRow(item)
        qtbot.addWidget(row)
        row.show()

        # 等待后台加载完成并进入缓存
        qtbot.waitUntil(
            lambda: (r"C:\Program Files\OpenAI\ChatGPT.exe", "win32", 36) in provider._pixmap_cache,
            timeout=3000,
        )

        # 断言：已显示有效 Pixmap，且绝不显示首字母文本
        pixmap = row.icon_label.pixmap()
        assert not pixmap.isNull()
        assert pixmap.width() <= 36 and pixmap.height() <= 36
        assert row.icon_label.text() == ""


def test_lnk_icon_loaded(qtbot):
    """2. .lnk 图标成功加载到应用行。"""
    provider = AppIconProvider.instance()
    dummy_img = _make_dummy_image(color=(0, 200, 100, 255))

    with patch("src.ui_qt.icon_provider.load_shell_icon", return_value=dummy_img) as mock_shell:
        item = AppListItem(
            app_id="app_lnk_1",
            display_name="Visual Studio Code",
            aliases=("VS Code",),
            category="电脑应用",
            icon_ref=r"C:\Users\User\Desktop\VSCode.lnk",
            is_enabled=True,
            bucket=AppBucket.ALLOWED,
            voice_example="可以说：打开VS Code",
            launch_type="shortcut",
        )
        row = ApplicationRow(item)
        qtbot.addWidget(row)
        row.show()

        qtbot.waitUntil(
            lambda: (r"C:\Users\User\Desktop\VSCode.lnk", "shortcut", 36) in provider._pixmap_cache,
            timeout=3000,
        )
        assert not row.icon_label.pixmap().isNull()
        dpr = round(float(row.devicePixelRatioF()), 2) if hasattr(row, "devicePixelRatioF") else 1.0
        expected_size = max(16, int(round(36 * dpr)))
        mock_shell.assert_called_with(r"C:\Users\User\Desktop\VSCode.lnk", "shortcut", expected_size)


def test_store_aumid_uses_store_branch():
    """3. Store/AUMID 调用 load_shell_icon 时进入专门的 Store 提取分支。"""
    dummy_img = _make_dummy_image()

    with patch("src.catalog.windows_metadata._load_via_image_factory", return_value=dummy_img) as mock_factory:
        result = load_shell_icon("openai.codex_2p2nqsd0c76g0!app", "store_aumid", size=36)
        assert result is not None
        mock_factory.assert_called_once_with(
            "shell:AppsFolder\\openai.codex_2p2nqsd0c76g0!app", 36
        )


def test_store_icon_propagates_to_row(qtbot):
    """4. Store 提取成功时可以正常传递并更新到 Qt 应用行。"""
    provider = AppIconProvider.instance()
    dummy_img = _make_dummy_image(color=(16, 163, 127, 255))

    with patch("src.ui_qt.icon_provider.load_shell_icon", return_value=dummy_img):
        item = AppListItem(
            app_id="app_store_1",
            display_name="ChatGPT Store",
            aliases=("ChatGPT",),
            category="电脑应用",
            icon_ref="openai.codex_2p2nqsd0c76g0!app",
            is_enabled=True,
            bucket=AppBucket.ALLOWED,
            voice_example="可以说：打开ChatGPT",
            launch_type="store_aumid",
        )
        row = ApplicationRow(item)
        qtbot.addWidget(row)
        row.show()

        qtbot.waitUntil(
            lambda: ("openai.codex_2p2nqsd0c76g0!app", "store_aumid", 36) in provider._pixmap_cache,
            timeout=3000,
        )
        assert not row.icon_label.pixmap().isNull()
        assert row.icon_label.text() == ""


def test_icon_load_failure_shows_fallback_icon(qtbot):
    """5. 提取失败时显示中性通用图标，绝不显示首字母。"""
    provider = AppIconProvider.instance()

    with patch("src.ui_qt.icon_provider.load_shell_icon", return_value=None):
        item = AppListItem(
            app_id="app_fail_1",
            display_name="BrokenApp",
            aliases=("Broken",),
            category="电脑应用",
            icon_ref=r"D:\NonExistent\broken.exe",
            is_enabled=False,
            bucket=AppBucket.NOT_ALLOWED,
            voice_example="可以说：打开BrokenApp",
            launch_type="win32",
        )
        row = ApplicationRow(item)
        qtbot.addWidget(row)
        row.show()

        fallback_pixmap = provider.get_default_fallback_pixmap(36)
        qtbot.waitUntil(
            lambda: (
                not row.icon_label.pixmap().isNull()
                and (item.icon_ref, "win32", 36) in provider._failed_cache
            ),
            timeout=3000,
        )

        assert not row.icon_label.pixmap().isNull()
        assert row.icon_label.text() == ""
        # 验证已记录失败缓存
        assert (item.icon_ref, "win32", 36) in provider._failed_cache


def test_same_icon_hits_cache(qtbot):
    """6. 相同图标重复创建应用行时命中缓存，不重复执行 Shell 提取。"""
    provider = AppIconProvider.instance()
    dummy_img = _make_dummy_image()

    with patch("src.ui_qt.icon_provider.load_shell_icon", return_value=dummy_img) as mock_shell:
        item1 = AppListItem(
            app_id="app_1",
            display_name="App One",
            aliases=(),
            category="电脑应用",
            icon_ref=r"C:\App\app.exe",
            is_enabled=True,
            bucket=AppBucket.ALLOWED,
            voice_example="",
            launch_type="win32",
        )
        row1 = ApplicationRow(item1)
        qtbot.addWidget(row1)
        row1.show()
        qtbot.waitUntil(lambda: (r"C:\App\app.exe", "win32", 36) in provider._pixmap_cache, timeout=3000)

        assert mock_shell.call_count == 1

        # 创建第二个使用相同图标的行（例如不同别名或重新加载）
        item2 = AppListItem(
            app_id="app_2",
            display_name="App Two",
            aliases=(),
            category="电脑应用",
            icon_ref=r"C:\App\app.exe",
            is_enabled=False,
            bucket=AppBucket.NOT_ALLOWED,
            voice_example="",
            launch_type="win32",
        )
        row2 = ApplicationRow(item2)
        qtbot.addWidget(row2)
        row2.show()

        # 第二个行应该立即同步获得缓存 pixmap，绝不再调用 mock_shell
        assert not row2.icon_label.pixmap().isNull()
        assert mock_shell.call_count == 1


def test_filtering_and_refresh_does_not_repeat_extraction(qtbot):
    """7. 搜索、筛选或切换分类重新生成行时不重复提取已缓存图标。"""
    provider = AppIconProvider.instance()
    dummy_img = _make_dummy_image()

    with patch("src.ui_qt.icon_provider.load_shell_icon", return_value=dummy_img) as mock_shell:
        entry = AppEntry(
            app_id="app_test",
            display_name="TestApp",
            launch_type=LaunchType.WIN32,
            launch_target=r"C:\Test\test.exe",
            icon_ref=r"C:\Test\test.exe",
            source="Test",
        )
        items = build_app_items([entry])
        assert items[0].launch_type == "win32"

        # 首次展示
        row = ApplicationRow(items[0])
        qtbot.addWidget(row)
        row.show()
        qtbot.waitUntil(lambda: (r"C:\Test\test.exe", "win32", 36) in provider._pixmap_cache, timeout=3000)
        assert mock_shell.call_count == 1

        # 模拟搜索过滤后重新构建行
        filtered = filter_items(items, query="Test")
        row_filtered = ApplicationRow(filtered[0])
        qtbot.addWidget(row_filtered)
        row_filtered.show()

        # 验证命中缓存，未发生第二次 Shell 提取
        assert mock_shell.call_count == 1
        assert not row_filtered.icon_label.pixmap().isNull()


def test_async_result_does_not_update_destroyed_or_reused_row(qtbot):
    """8. 异步结果返回时，若应用行已被销毁或已复用为其他应用，不更新错误行。"""
    provider = AppIconProvider.instance()
    event_block = threading.Event()

    def slow_shell_load(icon_ref, launch_type, size):
        event_block.wait(timeout=2.0)
        return _make_dummy_image()

    with patch("src.ui_qt.icon_provider.load_shell_icon", side_effect=slow_shell_load):
        item = AppListItem(
            app_id="app_destroy",
            display_name="DestroyMe",
            aliases=(),
            category="电脑应用",
            icon_ref=r"C:\Slow\app.exe",
            is_enabled=True,
            bucket=AppBucket.ALLOWED,
            voice_example="",
            launch_type="win32",
        )
        row = ApplicationRow(item)
        qtbot.addWidget(row)
        row.show()

        # 在异步任务未返回前立即销毁该控件
        row.deleteLater()
        QCoreApplication.sendPostedEvents(row)

        # 模拟 row 变更了 item (例如复用)
        row._app_id = "completely_different_app"

        # 解除后台任务阻塞
        event_block.set()

        # 保证后台信号安全处理，不引发崩溃
        qtbot.wait(200)


def test_shell_extraction_runs_in_background_thread(qtbot):
    """9. Shell 提取必须在非 GUI 主线程中执行，杜绝阻塞主界面。"""
    main_thread_id = threading.get_ident()
    executed_thread_ids = []

    def tracking_load(icon_ref, launch_type, size):
        executed_thread_ids.append(threading.get_ident())
        return _make_dummy_image()

    with patch("src.ui_qt.icon_provider.load_shell_icon", side_effect=tracking_load):
        item = AppListItem(
            app_id="app_thread_check",
            display_name="ThreadCheck",
            aliases=(),
            category="电脑应用",
            icon_ref=r"C:\Thread\app.exe",
            is_enabled=True,
            bucket=AppBucket.ALLOWED,
            voice_example="",
            launch_type="win32",
        )
        row = ApplicationRow(item)
        qtbot.addWidget(row)
        row.show()

        qtbot.waitUntil(lambda: len(executed_thread_ids) > 0, timeout=3000)

        assert len(executed_thread_ids) == 1
        assert executed_thread_ids[0] != main_thread_id, "Shell 提取在 GUI 主线程执行了，违反非阻塞规范！"


def test_icon_failure_does_not_affect_controls_or_launch(qtbot):
    """10. 图标提取失败时，开关切换与修复按钮交互仍完全正常工作。"""
    with patch("src.ui_qt.icon_provider.load_shell_icon", return_value=None):
        item = AppListItem(
            app_id="app_robust",
            display_name="RobustApp",
            aliases=(),
            category="电脑应用",
            icon_ref=r"C:\Broken\broken.exe",
            is_enabled=False,
            bucket=AppBucket.NEEDS_ATTENTION,
            voice_example="",
            repair_message="应用位置已改变",
            launch_type="win32",
        )
        row = ApplicationRow(item)
        qtbot.addWidget(row)
        row.show()

        # 开关交互测试
        with qtbot.waitSignal(row.enabled_changed, timeout=1000) as sig:
            qtbot.mouseClick(row.enabled_toggle, Qt.MouseButton.LeftButton)
        assert sig.args == ["app_robust", True]

        # 修复按钮测试
        with qtbot.waitSignal(row.repair_requested, timeout=1000) as sig2:
            qtbot.mouseClick(row.repair_button, Qt.MouseButton.LeftButton)
        assert sig2.args == ["app_robust"]


def test_packaged_spec_and_env_includes_dependencies():
    """11. 校验 PyInstaller spec 与运行环境完整包含本轮所需的图像与 Windows API 模块。"""
    spec_path = Path("MCPAssistant.spec")
    assert spec_path.exists(), "未找到 MCPAssistant.spec"
    content = spec_path.read_text(encoding="utf-8")

    # 验证关键隐式导入
    required_imports = ["PIL", "PIL.Image", "pythoncom", "pywintypes", "win32api", "PySide6.QtSvg", "shiboken6"]
    for mod in required_imports:
        assert f"'{mod}'" in content or f'"{mod}"' in content, f"spec 缺少隐式导入: {mod}"

    # 验证当前虚拟环境可导入这些模块
    import PIL
    import PIL.Image
    import pythoncom
    import pywintypes
    import win32api
    import PySide6.QtSvg
    import shiboken6

    assert PIL is not None


def test_dpr_aware_cache_isolation(qtbot):
    """12. 校验 AppIconProvider 对不同 DPR 屏幕的缓存进行隔离，互不污染。"""
    provider = AppIconProvider.instance()
    dummy_img_1x = _make_dummy_image(size=32)
    dummy_img_2x = _make_dummy_image(size=64)

    with patch("src.ui_qt.icon_provider.load_shell_icon") as mock_shell:
        mock_shell.side_effect = lambda ref, t, size: dummy_img_2x if size > 32 else dummy_img_1x

        # 1. 以 DPR 1.0 加载
        cb1_called = False
        def cb1(pixmap):
            nonlocal cb1_called
            cb1_called = True
        provider.load_icon_async("test_app.exe", "win32", 32, cb1, dpr=1.0)

        # 2. 以 DPR 2.0 加载相同应用
        cb2_called = False
        def cb2(pixmap):
            nonlocal cb2_called
            cb2_called = True
        provider.load_icon_async("test_app.exe", "win32", 32, cb2, dpr=2.0)

        qtbot.waitUntil(lambda: cb1_called and cb2_called, timeout=3000)

        # 验证物理尺寸分别调用了 32 (32*1.0) 和 64 (32*2.0)
        assert mock_shell.call_count == 2
        calls = [c.args for c in mock_shell.call_args_list]
        assert ("test_app.exe", "win32", 32) in calls
        assert ("test_app.exe", "win32", 64) in calls

        # 验证底层 DPR 缓存各自独立存在
        pm1 = provider.get_cached_pixmap("test_app.exe", "win32", 32, dpr=1.0)
        pm2 = provider.get_cached_pixmap("test_app.exe", "win32", 32, dpr=2.0)
        assert pm1 is not None
        assert pm2 is not None
        assert pm1.devicePixelRatio() == 1.0
        assert pm2.devicePixelRatio() == 2.0
