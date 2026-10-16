"""第十七轮 Qt 界面缺陷专项测试集 (Round 17 Qt Defects)

覆盖问题：
- 问题一：退出与异步重启并发竞态 (shutdown_async, 退出等待 Bridge 确认停止)
- 问题三：批量操作移至后台 worker，Qt 事件循环持续响应，控件保护
- 问题四：单项授权失败后移入“需要处理”、自动切换 Tab、显示“选择主程序”
"""

import time
import threading
from unittest.mock import MagicMock, patch
import pytest

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication, QMessageBox, QFileDialog

from src.catalog.models import AppEntry, LaunchType
from src.core.config import AppConfig
from src.ui_qt.controllers.applications_controller import ApplicationsController
from src.ui_qt.controllers.service_controller import ServiceController
from src.ui_qt.presentation.apps import AppBucket, build_app_items
from src.ui_qt.views.applications_page import ApplicationsPage
from src.ui_qt.views.main_window import MainWindow


class ControlledDummyBridge:
    """可精确控制停止耗时的 Bridge 替身。"""

    def __init__(self, stop_delay: float = 0.2, stop_result: bool = True):
        self.stop_delay = stop_delay
        self.stop_result = stop_result
        self.stop_calls = 0
        self.start_calls = 0
        self._running = True

    def start(self):
        self.start_calls += 1
        self._running = True

    def stop(self) -> bool:
        self.stop_calls += 1
        if self.stop_delay > 0:
            time.sleep(self.stop_delay)
        if self.stop_result:
            self._running = False
        return self.stop_result

    def is_running(self) -> bool:
        return self._running


# =================================================================================================
# 1. 退出与重启并发竞态测试 (Item 1)
# =================================================================================================

def test_shutdown_async_during_restart_busy_aborts_new_bridge(qtbot):
    """当控制器正在执行 restart_async() 时调用 shutdown_async()，shutdown 请求不能丢失，且绝不能启动新 Bridge。"""
    cfg = MagicMock()
    cfg.load_config.return_value = AppConfig(mcp_endpoint="wss://api.xiaozhi.me/mcp/?token=VALID")

    bridge = ControlledDummyBridge(stop_delay=0.15)
    controller = ServiceController(cfg, bridge_factory=lambda **kw: ControlledDummyBridge())
    controller.bridge = bridge

    # 1. 发起异步重启
    assert controller.restart_async() is True
    assert controller.is_busy == True

    # 2. 在重启忙碌中立刻发起 shutdown_async
    shutdown_sig = []
    controller.shutdown_finished.connect(shutdown_sig.append)
    assert controller.shutdown_async() is True

    # 3. 等待 shutdown_finished 信号
    with qtbot.waitSignal(controller.shutdown_finished, timeout=3000):
        pass

    assert len(shutdown_sig) == 1
    assert shutdown_sig[0] is True
    # 核心安全断言：Bridge 必须为 None，busy 必须为 False，新 Bridge 绝未启动
    assert controller.bridge is None
    assert controller.is_busy == False
    assert bridge.stop_calls == 1


def test_main_window_exit_waits_for_shutdown_finished_no_premature_quit(qtbot):
    """MainWindow.request_exit() 必须等待 shutdown_finished(True) 后才能调用 QApplication.quit()。
    模拟 stop 耗时长于 0.3 秒，在停止前 quit 绝不能被调用。"""
    cfg = MagicMock()
    cfg.load_config.return_value = AppConfig(minimize_to_tray_on_close=False)
    cat = MagicMock()
    cat.apps = {}

    bridge = ControlledDummyBridge(stop_delay=0.3)
    controller = ServiceController(cfg)
    controller.bridge = bridge

    win = MainWindow(
        config_manager=cfg,
        catalog_manager=cat,
        service_controller=controller,
        skip_startup=True,
        enable_tray=False,
    )
    qtbot.addWidget(win)

    quit_called = []
    with patch.object(QApplication, "quit", side_effect=lambda: quit_called.append(True)):
        win.request_exit()

        # 刚发起时 quit 绝不能被调用
        assert len(quit_called) == 0
        assert getattr(win, "_exit_in_progress", False) is True

        # 等待后台关闭确认完成
        with qtbot.waitSignal(controller.shutdown_finished, timeout=3000):
            pass

        assert len(quit_called) == 1
        assert controller.bridge is None


def test_shutdown_failure_restores_window_and_cancels_exit(qtbot):
    """如果底层 Bridge 停止失败，shutdown_finished(False) 必须阻止 quit，恢复窗口并提示用户。"""
    cfg = MagicMock()
    cfg.load_config.return_value = AppConfig(minimize_to_tray_on_close=False)
    cat = MagicMock()
    cat.apps = {}

    # stop 始终返回 False
    bridge = ControlledDummyBridge(stop_delay=0.05, stop_result=False)
    controller = ServiceController(cfg)
    controller.bridge = bridge

    win = MainWindow(
        config_manager=cfg,
        catalog_manager=cat,
        service_controller=controller,
        skip_startup=True,
        enable_tray=False,
    )
    qtbot.addWidget(win)

    quit_called = []
    with patch.object(QApplication, "quit", side_effect=lambda: quit_called.append(True)), \
         patch.object(QMessageBox, "warning") as mock_warn:

        win.request_exit()

        with qtbot.waitSignal(controller.shutdown_finished, timeout=3000):
            pass

        # 失败时不得退出
        assert len(quit_called) == 0
        # 窗口恢复状态
        assert getattr(win, "_exit_in_progress", False) is False
        mock_warn.assert_called_once()
        assert "未能" in mock_warn.call_args[0][1] or "未能" in mock_warn.call_args[0][2] or "失败" in mock_warn.call_args[0][2]
        QApplication.processEvents()


def test_duplicate_request_exit_is_idempotent(qtbot):
    """连续多次点击退出只启动一次 shutdown 流程。"""
    cfg = MagicMock()
    cfg.load_config.return_value = AppConfig(minimize_to_tray_on_close=False)
    cat = MagicMock()
    cat.apps = {}

    bridge = ControlledDummyBridge(stop_delay=0.2)
    controller = ServiceController(cfg)
    controller.bridge = bridge

    win = MainWindow(
        config_manager=cfg,
        catalog_manager=cat,
        service_controller=controller,
        skip_startup=True,
        enable_tray=False,
    )
    qtbot.addWidget(win)

    with patch.object(controller, "shutdown_async", wraps=controller.shutdown_async) as mock_shut, \
         patch.object(QApplication, "quit"):
        win.request_exit()
        win.request_exit()
        win.request_exit()

        assert mock_shut.call_count == 1

        with qtbot.waitSignal(controller.shutdown_finished, timeout=3000):
            pass


# =================================================================================================
# 2. 批量允许/撤销移至后台 worker 测试 (Item 3)
# =================================================================================================

def test_batch_set_visible_is_non_blocking_and_pumps_events(qtbot):
    """批量操作执行耗时任务时，Qt 主线程不得被阻塞，QTimer 能够正常触发响应。"""
    cat_mock = MagicMock()
    cat_mock.get_all_apps.return_value = []

    def slow_batch(authorized, app_ids=None):
        time.sleep(0.2)
        return len(app_ids or [])

    cat_mock.batch_set_enabled.side_effect = slow_batch

    controller = ApplicationsController(cat_mock, MagicMock())
    controller._visible_ids = ["app-1", "app-2", "app-3"]

    timer_ticks = []
    probe_timer = QTimer()
    probe_timer.setInterval(40)
    probe_timer.timeout.connect(lambda: timer_ticks.append(True))
    probe_timer.start()

    start = time.perf_counter()
    controller.batch_set_visible(True)
    returned_time = time.perf_counter() - start

    # 方法自身必须在 100ms 内非阻塞立即返回
    assert returned_time < 0.1, f"batch_set_visible 同步阻塞了主线程: {returned_time}s"
    assert controller._is_busy is True

    # 等待后台 worker 完成
    with qtbot.waitSignal(controller.busy_changed, timeout=2000):
        pass

    probe_timer.stop()
    # 验证事件循环在执行期间正常调度，timer 被触发了多次
    assert len(timer_ticks) >= 2, f"Qt 事件循环被卡死，timer 只触发了 {len(timer_ticks)} 次"


def test_batch_set_visible_idempotent_when_busy(qtbot):
    """busy 期间重复触发 batch_set_visible，不得创建多余 worker 且只调用底层一次。"""
    cat_mock = MagicMock()
    cat_mock.get_all_apps.return_value = []
    cat_mock.batch_set_enabled.side_effect = lambda auth, app_ids: (time.sleep(0.15) or 1)

    controller = ApplicationsController(cat_mock, MagicMock())
    controller._visible_ids = ["app-1"]

    controller.batch_set_visible(True)
    # 重复调用
    controller.batch_set_visible(True)
    controller.batch_set_visible(False)

    with qtbot.waitSignal(controller.busy_changed, timeout=2000):
        pass

    assert cat_mock.batch_set_enabled.call_count == 1


# =================================================================================================
# 3. 授权失败后移入“需要处理”、分组自动切换与修复测试 (Item 4)
# =================================================================================================

def test_invalid_automatic_apps_not_dangerous_enter_needs_attention():
    """普通 invalid 但非 dangerous/restricted 的自动条目（如带参数快捷方式）必须进入 NEEDS_ATTENTION。"""
    entry = AppEntry(
        app_id="auto-invalid",
        display_name="联想电脑管家",
        exe_path=r"C:\fake\lenovo.lnk",
        launch_target=r"C:\fake\lenovo.lnk",
        launch_type=LaunchType.SHORTCUT,
        source="StartMenu",
        is_user_managed=False,
        is_enabled=False,
        is_valid=False,
        requires_user_attention=True,
        is_restricted=False,
        validation_status="invalid",
        authorization_block_reason="快捷方式包含启动参数，为避免参数注入，禁止授权和启动",
    )
    items = build_app_items([entry])
    assert len(items) == 1
    assert items[0].bucket is AppBucket.NEEDS_ATTENTION
    assert items[0].display_name == "联想电脑管家"
    assert getattr(items[0], "repair_action_label", "") == "选择主程序"


def test_dangerous_automatic_apps_remain_hidden():
    """is_restricted=True 或 validation_status='dangerous' 的高危条目（如 cmd/powershell）必须保持彻底隐藏。"""
    dangerous_entries = [
        AppEntry(
            app_id="auto-cmd",
            display_name="命令提示符",
            exe_path=r"C:\Windows\System32\cmd.exe",
            launch_type=LaunchType.WIN32,
            source="StartMenu",
            is_valid=False,
            is_restricted=True,
            validation_status="dangerous",
        ),
        AppEntry(
            app_id="auto-taskmgr",
            display_name="任务管理器",
            exe_path=r"C:\Windows\System32\taskmgr.exe",
            launch_type=LaunchType.WIN32,
            source="BuiltinTools",
            is_valid=False,
            is_restricted=False,
            validation_status="dangerous",
        ),
    ]
    items = build_app_items(dangerous_entries)
    assert len(items) == 0, "高危与受限条目必须被彻底隐藏"


def test_failed_authorization_switches_to_needs_attention_tab(qtbot):
    """用户点击单项允许后校验失败：弹窗提示、自动切换至‘需要处理’分组、计数增加、条目可见。"""
    cat_mock = MagicMock()
    # 模拟条目：初始在“未允许”
    entry = AppEntry(
        app_id="app-shortcut",
        display_name="带参数应用",
        exe_path=r"C:\fake\param.lnk",
        launch_target=r"C:\fake\param.lnk",
        launch_type=LaunchType.SHORTCUT,
        source="StartMenu",
        is_user_managed=False,
        is_enabled=False,
        is_valid=True,
    )
    cat_mock.get_all_apps.return_value = [entry]

    def fake_set_enabled(app_id, enabled):
        if enabled:
            # 校验失败并更新条目状态
            entry.is_valid = False
            entry.is_enabled = False
            entry.validation_status = "invalid"
            entry.authorization_block_reason = "快捷方式包含启动参数"
            return False
        return True

    cat_mock.set_app_enabled.side_effect = fake_set_enabled

    controller = ApplicationsController(cat_mock, MagicMock())
    page = ApplicationsPage(controller)
    qtbot.addWidget(page)

    # 初始切换到“未允许”
    page._select_bucket(AppBucket.NOT_ALLOWED)
    assert len(page.rows) == 1

    # 用户点击开关允许
    with patch.object(QMessageBox, "warning") as mock_warn:
        page._on_row_enabled_changed("app-shortcut", True)

        mock_warn.assert_called_once()
        # 弹窗提示必须真实指出已移到需要处理并引导选择主程序
        warn_text = mock_warn.call_args[0][2]
        assert "需要处理" in warn_text
        assert "选择" in warn_text or "主程序" in warn_text

    # 验证页面已自动切换至“需要处理”
    assert controller._bucket is AppBucket.NEEDS_ATTENTION
    # 验证“需要处理”计数为 1 且条目仍然可见
    assert len(page.rows) == 1
    assert page.rows[0].item.display_name == "带参数应用"
    assert page.rows[0].repair_button.text() == "选择主程序"


def test_repair_button_selects_file_and_replaces_target(qtbot, tmp_path):
    """点击选择主程序后，选定合法新 EXE，调用 replace_invalid_app_target 并成功修复。"""
    cat_mock = MagicMock()
    cat_mock.apps = {
        "auto-lenovo": AppEntry(
            app_id="auto-lenovo",
            display_name="联想电脑管家",
            exe_path=r"C:\fake\lenovo.lnk",
            launch_target=r"C:\fake\lenovo.lnk",
            launch_type=LaunchType.SHORTCUT,
            source="StartMenu",
            is_user_managed=False,
            is_enabled=False,
            is_valid=False,
            validation_status="invalid",
        )
    }
    cat_mock.get_all_apps.return_value = list(cat_mock.apps.values())

    controller = ApplicationsController(cat_mock, MagicMock())
    page = ApplicationsPage(controller)
    qtbot.addWidget(page)

    valid_exe = tmp_path / "LenovoReal.exe"
    valid_exe.write_text("dummy")

    with patch("PySide6.QtWidgets.QFileDialog.getOpenFileName", return_value=(str(valid_exe), "")):
        page._repair_manual("auto-lenovo")

    cat_mock.replace_invalid_app_target.assert_called_once_with("auto-lenovo", str(valid_exe))
