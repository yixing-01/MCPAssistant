from src.ui_qt.views.main_window import MainWindow


def test_shell_has_only_three_primary_destinations(qtbot):
    window = MainWindow(start_in_background=False)
    qtbot.addWidget(window)
    labels = [button.text() for button in window.primary_navigation_buttons]
    assert labels == ["首页", "我的应用", "设置"]


def test_shell_does_not_show_developer_terms(qtbot):
    window = MainWindow(start_in_background=False)
    qtbot.addWidget(window)
    visible = " ".join(label.text() for label in window.findChildren(type(window.page_title)))
    for term in ("WebSocket", "DPAPI", "AST", "CMD", "JSON-RPC"):
        assert term not in visible
