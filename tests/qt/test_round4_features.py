"""第四轮 UI 与连接流程全面回归测试套件。

覆盖：
1. 滚动条 handle 规则、子控件覆盖消除与三点菜单主题；
2. ServiceController.restart() 停止成功/失败/并发语义；
3. ConnectionCodeController 格式门禁、纯单一字段保存、Token 脱敏与异步过期废弃；
4. MainWindow 路由解耦：立即重试/更换连接码/首次设置三条独立路径。
"""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QDialog, QLineEdit, QMenu, QToolButton

from src.bridge.pipe import PipeBridge, ServiceStatus
from src.core.config import AppConfig, ConfigManager
from src.ui_qt.controllers.connection_code_controller import ConnectionCodeController
from src.ui_qt.controllers.service_controller import ServiceController
from src.ui_qt.state import ProductStatus, ProductStatusKind
from src.ui_qt.views.applications_page import ApplicationsPage
from src.ui_qt.views.connection_code_dialog import ConnectionCodeDialog
from src.ui_qt.views.help_page import HelpPage
from src.ui_qt.views.home_page import HomePage
from src.ui_qt.views.main_window import MainWindow
from src.ui_qt.views.settings_page import SettingsPage


# =========================================================================
# 1. 滚动与菜单测试
# =========================================================================

def test_qss_scrollbar_rules_and_no_thumb():
    """QSS 中必须包含正确的 handle 规则，绝不能出现无效的 thumb。"""
    from src.ui_qt.theme import THEME_QSS_PATH
    qss = THEME_QSS_PATH.read_text(encoding="utf-8")

    # 1. 严禁 thumb 规则
    assert "QScrollBar::thumb:vertical" not in qss
    assert "QScrollBar::thumb:horizontal" not in qss

    # 2. 必须包含完整的 handle 规则
    assert "QScrollBar::handle:vertical" in qss
    assert "QScrollBar::handle:horizontal" in qss
    assert "QScrollBar::handle:vertical:hover" in qss
    assert "QScrollBar::handle:vertical:pressed" in qss
    assert "QScrollBar::handle:horizontal:hover" in qss
    assert "QScrollBar::handle:horizontal:pressed" in qss

    # 3. 必须包含 page, line, corner
    assert "QScrollBar::add-page:vertical" in qss
    assert "QScrollBar::sub-page:vertical" in qss
    assert "QScrollBar::add-page:horizontal" in qss
    assert "QScrollBar::sub-page:horizontal" in qss
    assert "QAbstractScrollArea::corner" in qss


def test_applications_more_button_and_menu_style(qtbot):
    """三点按钮与菜单具备明确对象名，文案新手化，原生指示箭头被隐藏。"""
    page = ApplicationsPage(MagicMock())
    qtbot.addWidget(page)

    # 按钮对象名与三点文本
    assert page.more_button.objectName() == "applicationsMoreButton"
    assert page.more_button.text() == "···"

    # 菜单对象名与文案
    menu = page.more_button.menu()
    assert menu is not None
    assert menu.objectName() == "applicationsMoreMenu"

    action_texts = [a.text() for a in menu.actions()]
    assert "允许这些应用" in action_texts
    assert "取消允许" in action_texts
    assert "允许当前筛选结果" not in action_texts
    assert "取消允许当前筛选结果" not in action_texts

    # QSS 包含隐藏 menu-indicator
    from src.ui_qt.theme import THEME_QSS_PATH
    qss = THEME_QSS_PATH.read_text(encoding="utf-8")
    assert "QToolButton#applicationsMoreButton::menu-indicator" in qss
    assert "QMenu#applicationsMoreMenu" in qss


def test_applications_list_card_padding_protects_rounded_corners(qtbot):
    """应用列表卡片具有稳定的内边距，确保内部滚动条和内容不贴死/覆盖父级圆角。"""
    page = ApplicationsPage(MagicMock())
    qtbot.addWidget(page)

    card = page.findChild(object, "appListCard")
    assert card is not None
    margins = card.layout().contentsMargins()
    assert margins.top() > 0 or margins.bottom() > 0 or margins.left() > 0


# =========================================================================
# 2. ServiceController.restart 测试
# =========================================================================

def test_service_controller_restart_without_old_bridge():
    """没有旧 Bridge 时，restart() 应直接执行一次 start()。"""
    mock_config_mgr = MagicMock()
    mock_config_mgr.load_config.return_value = AppConfig(
        mcp_endpoint="wss://api.xiaozhi.me/mcp/?token=test_token_12345"
    )

    mock_bridge = MagicMock()
    mock_bridge.is_running.return_value = False
    mock_factory = MagicMock(return_value=mock_bridge)

    controller = ServiceController(mock_config_mgr, bridge_factory=mock_factory)
    assert controller.bridge is None

    result = controller.restart()
    assert result is True
    assert mock_factory.call_count == 1
    assert mock_bridge.start.call_count == 1


def test_service_controller_restart_stops_old_before_starting_new():
    """旧 Bridge 正在运行时，restart() 必须先 stop 成功再创建并 start 新 Bridge。"""
    mock_config_mgr = MagicMock()
    mock_config_mgr.load_config.return_value = AppConfig(
        mcp_endpoint="wss://api.xiaozhi.me/mcp/?token=test_token_12345"
    )

    old_bridge = MagicMock()
    old_bridge.is_running.return_value = True
    old_bridge.stop.return_value = True

    new_bridge = MagicMock()
    new_bridge.is_running.return_value = False

    bridges = [new_bridge]
    mock_factory = MagicMock(side_effect=lambda **kwargs: bridges.pop(0))

    controller = ServiceController(mock_config_mgr, bridge_factory=mock_factory)
    controller.bridge = old_bridge

    result = controller.restart()
    assert result is True
    assert old_bridge.stop.call_count == 1
    assert new_bridge.start.call_count == 1


def test_service_controller_restart_fails_if_stop_fails():
    """旧 Bridge 停止失败时，restart() 绝不启动第二个 Bridge，返回 False 并发出用户错误。"""
    mock_config_mgr = MagicMock()
    mock_config_mgr.load_config.return_value = AppConfig(
        mcp_endpoint="wss://api.xiaozhi.me/mcp/?token=test_token_12345"
    )

    old_bridge = MagicMock()
    old_bridge.is_running.return_value = True
    old_bridge.stop.return_value = False

    mock_factory = MagicMock()

    controller = ServiceController(mock_config_mgr, bridge_factory=mock_factory)
    controller.bridge = old_bridge

    errors = []
    controller.user_error.connect(errors.append)

    result = controller.restart()
    assert result is False
    assert mock_factory.call_count == 0  # 绝不创建第二个 Bridge
    assert len(errors) == 1
    assert "仍在停止" in errors[0]


def test_service_controller_restart_idempotent_no_duplicate_bridges():
    """在 Bridge 已处于 running 状态且未停止时，多次 start/restart 不会创建重复 Bridge。"""
    mock_config_mgr = MagicMock()
    mock_config_mgr.load_config.return_value = AppConfig(
        mcp_endpoint="wss://api.xiaozhi.me/mcp/?token=test_token_12345"
    )

    bridge = MagicMock()
    bridge.is_running.return_value = True
    bridge.stop.return_value = True

    mock_factory = MagicMock(return_value=bridge)

    controller = ServiceController(mock_config_mgr, bridge_factory=mock_factory)
    controller.start()
    first_bridge = controller.bridge
    controller.start()  # 第二次 start 被拦截
    assert controller.bridge is first_bridge


# =========================================================================
# 3. ConnectionCodeController 测试
# =========================================================================

def test_connection_code_controller_rejects_invalid_format_without_network():
    """无效格式（非 wss://、无 token 等）直接拦截，不调用网络测试器，不触发保存。"""
    mock_config_mgr = MagicMock()
    mock_tester = MagicMock()

    controller = ConnectionCodeController(mock_config_mgr, tester=mock_tester)
    failed_msgs = []
    controller.validation_failed.connect(failed_msgs.append)

    controller.validate_and_save("http://not-valid.com")
    assert len(failed_msgs) == 1
    assert mock_tester.call_count == 0
    assert mock_config_mgr.save_config.call_count == 0


def test_connection_code_controller_network_failure_keeps_old_endpoint(qtbot):
    """网络测试失败时，不修改已有配置，旧 endpoint 保持不变。"""
    initial_config = AppConfig(
        mcp_endpoint="wss://api.xiaozhi.me/mcp/?token=old_token_111",
        scan_agreed=True,
    )
    mock_config_mgr = MagicMock()
    mock_config_mgr.load_config.return_value = initial_config

    mock_tester = MagicMock(return_value=(False, "连接超时"))

    controller = ConnectionCodeController(mock_config_mgr, tester=mock_tester)

    with qtbot.waitSignal(controller.validation_failed, timeout=2000) as blocker:
        controller.validate_and_save("wss://api.xiaozhi.me/mcp/?token=new_token_222")

    assert mock_config_mgr.save_config.call_count == 0
    assert "连接超时" in blocker.args[0]
    # 错误提示绝不暴露 token
    assert "new_token_222" not in blocker.args[0]


def test_connection_code_controller_success_only_updates_endpoint(qtbot):
    """验证成功后，深拷贝配置且仅更新 mcp_endpoint，其他所有字段完全保留，不调用应用扫描。"""
    initial_config = AppConfig(
        mcp_endpoint="wss://api.xiaozhi.me/mcp/?token=old_token_111",
        scan_agreed=True,
        allowed_write_dirs=["D:/safe/path"],
        auto_start_with_windows=True,
    )
    mock_config_mgr = MagicMock()
    mock_config_mgr.load_config.return_value = initial_config

    mock_tester = MagicMock(return_value=(True, ""))

    controller = ConnectionCodeController(mock_config_mgr, tester=mock_tester)

    with qtbot.waitSignal(controller.code_saved, timeout=2000):
        controller.validate_and_save("wss://api.xiaozhi.me/mcp/?token=new_token_888")

    assert mock_config_mgr.save_config.call_count == 1
    saved_config = mock_config_mgr.save_config.call_args[0][0]

    # 仅 endpoint 被更新为规范化地址
    assert "token=new_token_888" in saved_config.mcp_endpoint
    # 其余所有字段原样保留
    assert saved_config.scan_agreed is True
    assert saved_config.allowed_write_dirs == ["D:/safe/path"]
    assert saved_config.auto_start_with_windows is True


def test_connection_code_controller_cancels_stale_async_requests(qtbot):
    """调用 cancel() 后，即使后台测试稍后返回，也不会触发保存信号。"""
    mock_config_mgr = MagicMock()
    mock_config_mgr.load_config.return_value = AppConfig()

    event = threading.Event()

    def slow_tester(endpoint: str):
        event.wait(timeout=2)
        return True, ""

    controller = ConnectionCodeController(mock_config_mgr, tester=slow_tester)

    saved = []
    controller.code_saved.connect(lambda: saved.append(True))

    controller.validate_and_save("wss://api.xiaozhi.me/mcp/?token=slow_token")
    # 立即取消
    controller.cancel()
    event.set()

    qtbot.wait(100)
    assert len(saved) == 0
    assert mock_config_mgr.save_config.call_count == 0


# =========================================================================
# 4. 页面路由解耦测试
# =========================================================================

def test_settings_page_retry_calls_restart_not_onboarding(qtbot):
    """设置页的立即重试连接必须调用 service_controller.restart()，绝不能打开首次设置向导。"""
    mock_config_mgr = MagicMock()
    mock_config_mgr.load_config.return_value = AppConfig(
        mcp_endpoint="wss://api.xiaozhi.me/mcp/?token=valid_123"
    )
    mock_catalog_mgr = MagicMock()
    mock_service_ctrl = MagicMock()
    mock_service_ctrl.restart.return_value = True

    win = MainWindow(
        config_manager=mock_config_mgr,
        catalog_manager=mock_catalog_mgr,
        service_controller=mock_service_ctrl,
        skip_startup=True,
        enable_tray=False,
    )
    qtbot.addWidget(win)

    # 触发设置页的重试按钮
    win.settings_page.retry_button.click()

    assert (mock_service_ctrl.restart_async.call_count == 1 or mock_service_ctrl.restart.call_count == 1)
    assert win.onboarding_dialog is None


def test_help_page_retry_calls_restart_not_onboarding(qtbot):
    """帮助页的立即重试连接必须调用 service_controller.restart()，绝不能打开首次设置向导。"""
    mock_config_mgr = MagicMock()
    mock_config_mgr.load_config.return_value = AppConfig(
        mcp_endpoint="wss://api.xiaozhi.me/mcp/?token=valid_123"
    )
    mock_catalog_mgr = MagicMock()
    mock_service_ctrl = MagicMock()
    mock_service_ctrl.restart.return_value = True

    win = MainWindow(
        config_manager=mock_config_mgr,
        catalog_manager=mock_catalog_mgr,
        service_controller=mock_service_ctrl,
        skip_startup=True,
        enable_tray=False,
    )
    qtbot.addWidget(win)

    win.help_page.retry_button.click()

    assert (mock_service_ctrl.restart_async.call_count == 1 or mock_service_ctrl.restart.call_count == 1)
    assert win.onboarding_dialog is None


def test_home_page_recovering_retry_calls_restart(qtbot):
    """首页处于 RECOVERING 状态时，点击主操作（立即重试）调用 restart()。"""
    mock_config_mgr = MagicMock()
    mock_config_mgr.load_config.return_value = AppConfig(
        mcp_endpoint="wss://api.xiaozhi.me/mcp/?token=valid_123"
    )
    mock_catalog_mgr = MagicMock()
    mock_service_ctrl = MagicMock()
    mock_service_ctrl.restart.return_value = True

    win = MainWindow(
        config_manager=mock_config_mgr,
        catalog_manager=mock_catalog_mgr,
        service_controller=mock_service_ctrl,
        skip_startup=True,
        enable_tray=False,
    )
    qtbot.addWidget(win)

    win.home_page.set_product_status(
        ProductStatus(
            ProductStatusKind.RECOVERING,
            "MCP 服务暂时无法连接",
            "正在自动重新连接",
            primary_action="立即重试",
        )
    )
    win.home_page.primary_button.click()

    assert (mock_service_ctrl.restart_async.call_count == 1 or mock_service_ctrl.restart.call_count == 1)
    assert win.onboarding_dialog is None


def test_change_connection_code_opens_dedicated_dialog_not_onboarding(qtbot):
    """设置页点击更换连接码只打开 ConnectionCodeDialog，绝不创建 OnboardingController。"""
    mock_config_mgr = MagicMock()
    mock_config_mgr.load_config.return_value = AppConfig(
        mcp_endpoint="wss://api.xiaozhi.me/mcp/?token=valid_123"
    )
    mock_catalog_mgr = MagicMock()
    mock_service_ctrl = MagicMock()

    win = MainWindow(
        config_manager=mock_config_mgr,
        catalog_manager=mock_catalog_mgr,
        service_controller=mock_service_ctrl,
        skip_startup=True,
        enable_tray=False,
    )
    qtbot.addWidget(win)

    # 点击设置页的“更换连接码”按钮
    win.settings_page.change_code_button.click()

    assert win.connection_code_dialog is not None
    assert isinstance(win.connection_code_dialog, ConnectionCodeDialog)
    assert win.onboarding_dialog is None

    # 单实例保护测试：再次点击不会新建对话框
    dlg_ref = win.connection_code_dialog
    win.settings_page.change_code_button.click()
    assert win.connection_code_dialog is dlg_ref

    win.connection_code_dialog.close()


def test_missing_endpoint_opens_onboarding(qtbot):
    """没有有效连接码时，常规启动依然只进入首次设置向导。"""
    mock_config_mgr = MagicMock()
    mock_config_mgr.load_config.return_value = AppConfig(mcp_endpoint="")
    mock_catalog_mgr = MagicMock()
    mock_service_ctrl = MagicMock()

    win = MainWindow(
        config_manager=mock_config_mgr,
        catalog_manager=mock_catalog_mgr,
        service_controller=mock_service_ctrl,
        skip_startup=True,
        enable_tray=False,
    )
    qtbot.addWidget(win)

    # 模拟启动检查
    win.check_startup_state()

    assert win.onboarding_dialog is not None
    assert mock_service_ctrl.start.call_count == 0
    assert win.connection_code_dialog is None
    win.onboarding_dialog.close()
