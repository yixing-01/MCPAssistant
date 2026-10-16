from unittest.mock import MagicMock

from src.ui_qt.views.applications_page import ApplicationsPage
from src.ui_qt.views.main_window import MainWindow


def test_second_destination_is_applications_page(qtbot):
    catalog = MagicMock()
    catalog.get_all_apps.return_value = []
    config = MagicMock()
    window = MainWindow(config_manager=config, catalog_manager=catalog, service_controller=MagicMock())
    qtbot.addWidget(window)
    assert isinstance(window.stack.widget(1), ApplicationsPage)
