from unittest.mock import patch

from src import main as main_module


def test_qt_flag_selects_qt_runner(monkeypatch):
    monkeypatch.setattr(main_module.sys, "argv", ["app", "--qt-ui"])
    with patch("src.core.single_instance.SingleInstanceLock.acquire", return_value=True), \
         patch("src.core.single_instance.SingleInstanceLock.release"), \
         patch("src.ui_qt.app.run_qt", return_value=0) as run_qt:
        main_module.main()
    run_qt.assert_called_once()
