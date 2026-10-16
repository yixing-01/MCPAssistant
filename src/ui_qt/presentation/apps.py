from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable

from src.catalog.models import AppEntry


class AppBucket(str, Enum):
    """面向普通用户的应用分组类型。"""
    ALLOWED = "allowed"
    NOT_ALLOWED = "not_allowed"
    NEEDS_ATTENTION = "needs_attention"


@dataclass(frozen=True)
class AppListItem:
    """脱敏的应用展示模型，绝不包含 launch_target、路径、AUMID 或底层安全阻断字段。"""
    app_id: str
    display_name: str
    aliases: tuple[str, ...]
    category: str
    icon_ref: str
    is_enabled: bool
    bucket: AppBucket
    voice_example: str
    repair_message: str = ""
    launch_type: str = "win32"
    repair_action_label: str = ""
    can_toggle: bool = True


def _category(app: AppEntry) -> str:
    """根据应用属性生成普通用户可理解的应用类别。"""
    if app.is_user_managed:
        return "手动添加"
    if "music" in app.display_name.casefold() or "音乐" in app.display_name:
        return "音乐播放"
    return "电脑应用"


def build_app_items(apps: Iterable[AppEntry]) -> list[AppListItem]:
    """将业务 AppEntry 转换为展示模型列表；高危与受限项隐藏，仅合法失效项进入“需要处理”。"""
    result = []
    for app in apps:
        # 高危系统程序或受限项永远彻底隐藏
        if app.is_restricted or app.validation_status == "dangerous":
            continue

        needs_attention = False
        if not app.is_valid:
            if app.is_user_managed or getattr(app, "requires_user_attention", False):
                needs_attention = True
            else:
                # 被动扫描发现的普通 invalid 条目（系统管理入口、帮助文档、安装器等）保持隐藏
                continue

        if needs_attention:
            bucket = AppBucket.NEEDS_ATTENTION
            can_toggle = False
            repair_action_label = "重新选择" if app.is_user_managed else "选择主程序"
            if app.is_user_managed:
                repair_msg = "应用位置发生了变化，请重新选择。"
            elif app.authorization_block_reason and "参数" in app.authorization_block_reason:
                repair_msg = "该扫描入口包含启动参数，请选择主程序文件。"
            else:
                repair_msg = "请先选择主程序。"
        elif app.is_enabled:
            bucket = AppBucket.ALLOWED
            can_toggle = True
            repair_action_label = ""
            repair_msg = ""
        else:
            bucket = AppBucket.NOT_ALLOWED
            can_toggle = True
            repair_action_label = ""
            repair_msg = ""

        spoken_name = app.aliases[0] if app.aliases else app.display_name
        lt_val = str(getattr(app.launch_type, "value", app.launch_type) or "win32")
        result.append(AppListItem(
            app_id=app.app_id,
            display_name=app.display_name,
            aliases=tuple(app.aliases),
            category=_category(app),
            icon_ref=app.icon_ref,
            is_enabled=app.is_enabled,
            bucket=bucket,
            voice_example=f"可以说：打开{spoken_name}",
            repair_message=repair_msg,
            launch_type=lt_val,
            repair_action_label=repair_action_label,
            can_toggle=can_toggle,
        ))
    return sorted(result, key=lambda item: item.display_name.casefold())


def filter_items(
    items: Iterable[AppListItem],
    query: str = "",
    bucket: AppBucket | None = None,
) -> list[AppListItem]:
    """按分组与搜索词筛选展示项，仅搜索名称与别名，绝不搜索底层物理路径。"""
    normalized = query.strip().casefold()
    return [
        item for item in items
        if (bucket is None or item.bucket is bucket)
        and (
            not normalized
            or normalized in item.display_name.casefold()
            or any(normalized in alias.casefold() for alias in item.aliases)
        )
    ]
