"""图标与可访问性测试用例。

验证所有主导航按钮均配置了矢量 SVG 图标，资源目录严格无 emoji，可交互按钮具备名称。
"""

from pathlib import Path

from src.ui_qt.views.main_window import MainWindow


def test_navigation_uses_vector_icons(qtbot):
    """主导航按钮应全部使用非空的矢量 SVG 图标，图标目录包含 5 个 svg 文件。"""
    window = MainWindow(start_in_background=False)
    qtbot.addWidget(window)
    assert all(not button.icon().isNull() for button in window.primary_navigation_buttons)
    icons = Path("src/ui_qt/resources/icons")
    assert sorted(path.suffix for path in icons.iterdir()) == [".svg"] * 5


def test_interactive_controls_have_accessible_names(qtbot):
    """可见的可交互按钮必须具备可访问名称或可见文本，确保屏幕阅读与无障碍可用。"""
    window = MainWindow(start_in_background=False)
    qtbot.addWidget(window)
    window.show()
    unnamed = [
        widget for widget in window.findChildren(type(window.primary_navigation_buttons[0]))
        if widget.isVisibleTo(window) and not (widget.accessibleName() or widget.text())
    ]
    assert unnamed == []
