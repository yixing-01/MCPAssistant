import os
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# 严格执行规则：自动化测试必须使用隔离的临时 APPDATA 与 LOCALAPPDATA
_temp_appdata = tempfile.mkdtemp(prefix="qt_test_appdata_")
os.environ["APPDATA"] = _temp_appdata
os.environ["LOCALAPPDATA"] = _temp_appdata

import pytest
from PySide6.QtWidgets import QApplication

from src.ui_qt.app import configure_application


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    configure_application(app)
    return app
