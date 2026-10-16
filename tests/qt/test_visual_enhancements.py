"""第二轮视觉精致化重构专项测试。

验证 ToggleSwitch 开关交互、StepProgressWidget 状态切换、导航栏选中焦点分离与无米白条带污染。
"""

from unittest.mock import MagicMock

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel

from src.catalog.models import AppEntry, LaunchType
from src.ui_qt.theme import load_stylesheet
from src.ui_qt.views.main_window import MainWindow
from src.ui_qt.widgets.stepper import StepProgressWidget
from src.ui_qt.widgets.switch import ToggleSwitch


def test_toggle_switch_interactive_state(qtbot):
    """ToggleSwitch 可正常点击切换，并正确发射 toggled 与 clicked 信号。"""
    switch = ToggleSwitch()
    qtbot.addWidget(switch)
    assert switch.isChecked() is False

    with qtbot.waitSignal(switch.toggled, timeout=1000) as signal:
        qtbot.mouseClick(switch, Qt.MouseButton.LeftButton)
    assert switch.isChecked() is True

    with qtbot.waitSignal(switch.toggled, timeout=1000) as signal:
        qtbot.mouseClick(switch, Qt.MouseButton.LeftButton)
    assert switch.isChecked() is False


def test_step_progress_widget_node_states(qtbot):
    """StepProgressWidget 在不同步骤下正确设置 done/active/upcoming 状态。"""
    stepper = StepProgressWidget(["欢迎", "连接 MCP 服务", "选择应用"])
    qtbot.addWidget(stepper)

    # 初始处于第 1 步 (索引 0)
    stepper.set_current_step(0)
    assert stepper.nodes[0].badge.text() == "1"
    assert stepper.nodes[1].badge.text() == "2"

    # 切换至第 2 步 (索引 1)
    stepper.set_current_step(1)
    assert stepper.nodes[0].badge.text() == "✓"
    assert stepper.nodes[1].badge.text() == "2"

    # 切换至第 3 步 (索引 2)
    stepper.set_current_step(2)
    assert stepper.nodes[0].badge.text() == "✓"
    assert stepper.nodes[1].badge.text() == "✓"
    assert stepper.nodes[2].badge.text() == "3"


def test_qss_does_not_set_global_qwidget_background():
    """全局 QSS 严禁向通用 QWidget 注入 background: #FBFAF7，防止卡片内文字产生脏条带。"""
    stylesheet = load_stylesheet()
    # 检查 QWidget 规则块内不含 background
    lines = stylesheet.splitlines()
    in_qwidget = False
    for line in lines:
        if "QWidget {" in line:
            in_qwidget = True
        elif in_qwidget:
            if "}" in line:
                in_qwidget = False
            else:
                assert "background" not in line, "QWidget 全局规则中不得包含 background 声明！"


def test_nav_selection_clears_focus_of_inactive_buttons(qtbot):
    """切换主导航时，非激活项必须被取消选中并清除焦点，避免残留焦点边框。"""
    cfg = MagicMock()
    cat = MagicMock()
    svc = MagicMock()
    cat.get_all_apps.return_value = []
    win = MainWindow(
        config_manager=cfg,
        catalog_manager=cat,
        service_controller=svc,
        skip_startup=True,
        enable_tray=False,
    )
    qtbot.addWidget(win)

    win._select_page(0)
    assert win.primary_navigation_buttons[0].isChecked() is True
    assert win.primary_navigation_buttons[1].isChecked() is False

    win._select_page(1)
    assert win.primary_navigation_buttons[0].isChecked() is False
    assert win.primary_navigation_buttons[1].isChecked() is True
    assert win.primary_navigation_buttons[0].hasFocus() is False


def test_applications_notice_bar_and_white_list_surface(qtbot):
    """验证安全提示条有独立对象名、限定选择器且子标签无外边框，应用列表区域使用纯白表面。"""
    from src.ui_qt.views.applications_page import ApplicationsPage

    controller = MagicMock()
    page = ApplicationsPage(controller)
    qtbot.addWidget(page)

    # 1. 安全提示条具备独立对象名
    assert hasattr(page, "notice_frame")
    assert page.notice_frame.objectName() == "applicationsNoticeBar"

    # 2. 提示条样式使用限定选择器
    stylesheet = load_stylesheet()
    assert "QFrame#applicationsNoticeBar {" in stylesheet
    assert "QFrame#applicationsNoticeBar QLabel {" in stylesheet

    # 3. 提示条内部标签不会继承边框和背景 (明确为 transparent 和 none)
    labels = page.notice_frame.findChildren(QLabel)
    assert len(labels) >= 2
    for lbl in labels:
        assert "background: transparent" in lbl.styleSheet()
        assert "border: none" in lbl.styleSheet()

    # 4. 外层卡片呈现白色圆角表面，内部滚动宿主保持透明以避免覆盖父级圆角
    assert page.list_host.objectName() == "appListHost"
    assert "QFrame#appListCard {" in stylesheet
    assert "QScrollArea#appListScrollArea" in stylesheet
    assert "QWidget#appListHost" in stylesheet
    # 确保宿主不会以不透明内联白底覆盖外层圆角
    assert "background-color: #FFFFFF" not in page.list_host.styleSheet()
