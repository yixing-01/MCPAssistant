from unittest.mock import MagicMock
from src.ui_qt.views.applications_page import ApplicationsPage


def test_page_has_three_user_buckets_and_no_path_labels(qtbot):
    controller = MagicMock()
    page = ApplicationsPage(controller)
    qtbot.addWidget(page)
    assert [button.text().split()[0] for button in page.bucket_buttons] == ["已允许", "未允许", "需要处理"]
    assert "路径" not in page.visible_copy()
    assert "AUMID" not in page.visible_copy()
