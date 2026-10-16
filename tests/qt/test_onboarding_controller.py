from unittest.mock import MagicMock

from src.core.config import AppConfig
from src.ui_qt.controllers.onboarding_controller import OnboardingController


VALID = "mcp:wss://api.xiaozhi.me/mcp/?token=GOOD_TOKEN"


def test_format_error_returns_without_worker(qtbot):
    controller = OnboardingController(MagicMock(), MagicMock(), tester=MagicMock())
    with qtbot.waitSignal(controller.validation_failed, timeout=1000) as signal:
        controller.verify("http://unsafe")
    assert signal.args == ["只允许安全的 WSS 地址"]
    controller.tester.assert_not_called()


def test_worker_exception_becomes_safe_failure(qtbot):
    controller = OnboardingController(
        MagicMock(), MagicMock(), tester=MagicMock(side_effect=RuntimeError("secret"))
    )
    with qtbot.waitSignal(controller.validation_failed, timeout=2000) as signal:
        controller.verify(VALID)
    assert signal.args == ["连接失败，请检查网络后重试。"]


def test_stale_verification_result_is_ignored(qtbot):
    controller = OnboardingController(MagicMock(), MagicMock(), tester=MagicMock())
    controller._request_id = 2
    controller._accept_verification(1, True, "ok", "wss://old")
    assert controller.pending_endpoint == ""


def test_complete_does_not_callback_when_secure_save_fails(qtbot):
    config = MagicMock()
    config.load_config.return_value = AppConfig()
    config.save_config.side_effect = RuntimeError("DPAPI failed")
    catalog = MagicMock()
    controller = OnboardingController(config, catalog, tester=MagicMock())
    controller.pending_endpoint = "wss://api.xiaozhi.me/mcp/?token=GOOD_TOKEN"
    with qtbot.waitSignal(controller.save_failed, timeout=1000):
        controller.complete(["new-1"])
    catalog.batch_set_enabled.assert_not_called()


def test_discover_publishes_only_this_scan_new_apps(qtbot):
    from src.catalog.manager import ScanResult
    from src.catalog.models import AppEntry, LaunchType

    new_app = AppEntry(
        app_id="new",
        display_name="微信",
        aliases=["微信"],
        launch_type=LaunchType.WIN32,
        launch_target="C:/apps/wechat.exe",
        source="AppPaths",
    )
    old_disabled = AppEntry(
        app_id="old",
        display_name="旧应用",
        aliases=["旧应用"],
        launch_type=LaunchType.WIN32,
        launch_target="C:/apps/old.exe",
        source="AppPaths",
        is_enabled=False,
    )
    catalog = MagicMock()
    catalog.refresh_scan.return_value = ScanResult(
        new_ids=["new"],
        all_apps=[new_app, old_disabled],
    )
    controller = OnboardingController(MagicMock(), catalog, tester=MagicMock())
    with qtbot.waitSignal(controller.new_apps_discovered, timeout=2000) as signal:
        controller.discover_apps()
    assert [app.app_id for app in signal.args[0]] == ["new"]
    catalog.refresh_scan.assert_called_once_with(user_agreed=True)


def test_discover_failure_allows_retry(qtbot):
    catalog = MagicMock()
    catalog.refresh_scan.side_effect = OSError("disk")
    controller = OnboardingController(MagicMock(), catalog, tester=MagicMock())
    with qtbot.waitSignal(controller.scan_failed, timeout=2000) as signal:
        controller.discover_apps()
    assert signal.args == ["应用列表暂时无法读取，请重试。"]

