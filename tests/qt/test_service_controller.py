from unittest.mock import MagicMock

from src.bridge.pipe import ServiceStatus
from src.ui_qt.controllers.service_controller import ServiceController


VALID = "wss://api.xiaozhi.me/mcp/?token=VALID_TOKEN"


def test_start_rejects_invalid_endpoint_without_bridge(qtbot):
    config = MagicMock()
    config.load_config.return_value.mcp_endpoint = "bad"
    bridge_factory = MagicMock()
    controller = ServiceController(config, bridge_factory=bridge_factory)
    with qtbot.waitSignal(controller.user_error, timeout=1000) as signal:
        controller.start()
    assert signal.args == ["连接码无效，请重新配置 MCP 连接。"]
    bridge_factory.assert_not_called()


def test_start_is_idempotent_when_bridge_is_running():
    config = MagicMock()
    config.load_config.return_value.mcp_endpoint = VALID
    bridge = MagicMock()
    bridge.is_running.return_value = True
    factory = MagicMock(return_value=bridge)
    controller = ServiceController(config, bridge_factory=factory)
    controller.start()
    controller.start()
    factory.assert_called_once()
    bridge.start.assert_called_once()


def test_bridge_callback_is_translated_to_product_state(qtbot):
    config = MagicMock()
    config.load_config.return_value.mcp_endpoint = VALID
    bridge = MagicMock()
    bridge.is_running.return_value = False
    factory = MagicMock(return_value=bridge)
    controller = ServiceController(config, bridge_factory=factory)
    controller.start()
    callback = factory.call_args.kwargs["on_status_change"]
    with qtbot.waitSignal(controller.product_status_changed, timeout=1000) as signal:
        callback(ServiceStatus.CONNECTED, "raw protocol detail")
    assert signal.args[0].title == "MCP助手已准备好"
