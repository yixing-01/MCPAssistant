"""帮助与故障排查页面测试用例。

验证优先引导用户执行自愈动作（重试、检查网络、重新连接），默认隐藏技术详情。
"""

from unittest.mock import MagicMock

from src.ui_qt.views.help_page import HelpPage


def test_help_prioritizes_actions_over_logs(qtbot):
    """帮助页面应优先展示恢复动作，技术详情折叠隐藏。"""
    page = HelpPage(MagicMock())
    qtbot.addWidget(page)
    text = page.visible_copy()
    assert "立即重试" in text
    assert "检查网络" in text
    assert "技术详情" in text
    assert page.technical_panel.isVisibleTo(page) is False


def test_technical_panel_toggle(qtbot):
    """点击技术详情按钮可以切换折叠状态。"""
    page = HelpPage(MagicMock())
    qtbot.addWidget(page)
    page.show()
    assert page.technical_panel.isVisible() is False

    page.technical_button.click()
    assert page.technical_panel.isVisible() is True
    assert "⌄" in page.technical_button.text()

    page.technical_button.click()
    assert page.technical_panel.isVisible() is False
    assert "›" in page.technical_button.text()
