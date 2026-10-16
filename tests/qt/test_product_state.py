from src.bridge.pipe import ServiceStatus
from src.ui_qt.state import ProductStatusKind, map_service_status


def test_connected_becomes_ready():
    state = map_service_status(ServiceStatus.CONNECTED, "", has_valid_endpoint=True)
    assert state.kind is ProductStatusKind.READY
    assert state.title == "MCP助手已准备好"


def test_reconnecting_hides_protocol_message_and_exposes_wait():
    state = map_service_status(
        ServiceStatus.RECONNECTING,
        "将在 8.0 秒后重试 (第 3 次)...",
        has_valid_endpoint=True,
    )
    assert state.kind is ProductStatusKind.RECOVERING
    assert state.retry_seconds == 8
    assert "WebSocket" not in state.detail


def test_invalid_endpoint_requires_setup():
    state = map_service_status(ServiceStatus.STOPPED, "", has_valid_endpoint=False)
    assert state.kind is ProductStatusKind.SETUP_REQUIRED
    assert state.primary_action == "开始设置"


def test_many_failures_require_user_action():
    state = map_service_status(
        ServiceStatus.ERROR,
        "连接异常",
        has_valid_endpoint=True,
        reconnect_attempt=4,
    )
    assert state.kind is ProductStatusKind.ACTION_REQUIRED
