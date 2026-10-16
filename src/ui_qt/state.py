from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from src.bridge.pipe import ServiceStatus


class ProductStatusKind(str, Enum):
    """面向普通用户的产品状态类型。"""
    READY = "ready"
    CONNECTING = "connecting"
    RECOVERING = "recovering"
    ACTION_REQUIRED = "action_required"
    SETUP_REQUIRED = "setup_required"


@dataclass(frozen=True)
class ProductStatus:
    """产品状态数据结构。"""
    kind: ProductStatusKind
    title: str
    detail: str
    primary_action: str = ""
    retry_seconds: int | None = None


def _retry_seconds(message: str) -> int | None:
    """从重试提示消息中解析等待秒数。"""
    match = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*秒", message or "")
    return round(float(match.group(1))) if match else None


def map_service_status(
    status: ServiceStatus,
    message: str,
    *,
    has_valid_endpoint: bool,
    reconnect_attempt: int = 0,
) -> ProductStatus:
    """将底层的 ServiceStatus 及重连状态映射为普通用户可理解的产品状态。"""
    if not has_valid_endpoint:
        return ProductStatus(
            ProductStatusKind.SETUP_REQUIRED,
            "连接 MCP 服务后即可使用",
            "只需粘贴一次连接码。",
            "开始设置",
        )
    if status is ServiceStatus.CONNECTED:
        return ProductStatus(
            ProductStatusKind.READY,
            "MCP助手已准备好",
            "服务已在后台就绪，支持通过小智打开和关闭已允许的应用。",
            "试着打开一个应用",
        )
    if status is ServiceStatus.CONNECTING:
        return ProductStatus(
            ProductStatusKind.CONNECTING,
            "正在连接 MCP 服务",
            "通常只需要几秒钟。",
        )
    if status in (ServiceStatus.RECONNECTING, ServiceStatus.ERROR) and reconnect_attempt <= 3:
        return ProductStatus(
            ProductStatusKind.RECOVERING,
            "MCP 服务暂时无法连接",
            "可能是网络短暂波动，我们正在自动重新连接。",
            "立即重试",
            _retry_seconds(message),
        )
    return ProductStatus(
        ProductStatusKind.ACTION_REQUIRED,
        "需要检查连接",
        "请先确认电脑能够正常打开网页。",
        "开始故障排查",
    )
