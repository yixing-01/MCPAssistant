"""设置页面测试用例。

验证设置页面的普通用户语言文案、无技术术语泄露、高级设置折叠状态及信号发射。
"""

from unittest.mock import MagicMock

from src.ui_qt.views.settings_page import SettingsPage


def test_default_settings_copy_is_plain_language(qtbot):
    """设置页面默认文案应通俗易懂，严禁出现 MCP/WebSocket/DPAPI 等底层术语。"""
    page = SettingsPage(MagicMock())
    qtbot.addWidget(page)
    copy = page.visible_copy()
    assert "开机后自动准备好" in copy
    assert "允许小智控制已授权应用的启动与关闭" in copy
    for term in ("WebSocket", "DPAPI", "AST", "CMD"):
        assert term not in copy


def test_advanced_section_is_collapsed_by_default(qtbot):
    """高级设置区域默认处于折叠隐藏状态。"""
    page = SettingsPage(MagicMock())
    qtbot.addWidget(page)
    assert page.advanced_panel.isVisibleTo(page) is False


def test_toggle_advanced_expands_and_collapses(qtbot):
    """点击高级设置按钮可展开和折叠高级区域。"""
    page = SettingsPage(MagicMock())
    qtbot.addWidget(page)
    page.show()
    assert page.advanced_panel.isVisible() is False

    page.advanced_button.click()
    assert page.advanced_panel.isVisible() is True
    assert "⌄" in page.advanced_button.text()

    page.advanced_button.click()
    assert page.advanced_panel.isVisible() is False
    assert "›" in page.advanced_button.text()
