"""软件目录条目数据模型"""

import os
import time
from enum import Enum
from typing import Any, List, Optional, Union

from pydantic import BaseModel, Field, model_validator


class LaunchType(str, Enum):
    """应用启动类型"""
    WIN32 = "win32"
    SHORTCUT = "shortcut"
    STORE_AUMID = "store_aumid"
    PORTABLE = "portable"


def normalize_aliases(values: List[str]) -> List[str]:
    """规范化别名列表：去除空值并去重（不区分大小写），保持原有顺序"""
    result: List[str] = []
    seen = set()
    for value in values:
        clean = str(value or "").strip()
        key = clean.casefold()
        if clean and key not in seen:
            seen.add(key)
            result.append(clean)
    return result


def make_stable_identity(
    launch_type: Union[LaunchType, str],
    launch_target: str,
    resolved_target: str = "",
) -> str:
    """生成应用稳定身份标识"""
    kind = LaunchType(launch_type)
    if kind == LaunchType.STORE_AUMID:
        return f"store:{launch_target.strip().casefold()}"
    physical = resolved_target.strip() or launch_target.strip()
    if not physical:
        return ""
    normalized = os.path.normcase(os.path.realpath(os.path.abspath(physical)))
    return f"file:{normalized.casefold()}"


class AppEntry(BaseModel):
    """单个软件条目信息"""
    app_id: str = Field(description="软件唯一内部标识ID")
    display_name: str = Field(description="软件显示名称")
    display_name_source: str = Field(default="", description="显示名称来源")
    aliases: List[str] = Field(default_factory=list, description="语音别名列表")
    alias: str = Field(default="", exclude=True, description="向后兼容单一别名（不写入新JSON）")
    launch_type: LaunchType = Field(default=LaunchType.WIN32, description="应用启动类型")
    launch_target: str = Field(default="", description="启动目标（EXE路径、快捷方式路径或AUMID）")
    stable_identity: str = Field(default="", description="稳定身份标识")
    exe_path: str = Field(default="", description="向后兼容物理路径（Store应用为空）")
    source: str = Field(description="发现来源")
    publisher: str = Field(default="", description="发布厂商")
    version: str = Field(default="", description="版本号")
    icon_ref: str = Field(default="", description="图标引用标识或路径")
    is_enabled: bool = Field(default=False, description="是否已被明确授权允许小智打开和关闭")
    is_valid: bool = Field(default=True, description="条目当前是否有效")
    requires_user_attention: bool = Field(default=False, description="是否需要用户介入处理（如授权失败或位置失效）")
    is_restricted: bool = Field(default=False, description="是否受限")
    is_user_managed: bool = Field(default=False, description="是否由用户手动管理（绿色软件）")
    resolved_target: str = Field(default="", description="快捷方式解析后的真实目标物理路径")
    shortcut_arguments: str = Field(default="", description="快捷方式包含的启动参数")
    authorization_block_reason: str = Field(default="", description="安全拦截或禁止授权的原因")
    validation_status: str = Field(default="valid", description="校验状态")
    last_seen_scan_id: str = Field(default="", description="最后一次扫描的批次ID")
    last_verified: float = Field(default_factory=time.time, description="最后一次验证时间戳")

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_fields(cls, raw: Any) -> Any:
        """从旧格式迁移别名、启动类型与路径字段"""
        if not isinstance(raw, dict):
            return raw
        data = dict(raw)
        if "requires_user_attention" not in data:
            data["requires_user_attention"] = False
        old_alias = str(data.get("alias") or "").strip()
        aliases = data.get("aliases")
        if not aliases and old_alias:
            data["aliases"] = [old_alias]
        source = str(data.get("source") or "")
        old_path = str(data.get("exe_path") or "")
        if not data.get("launch_target"):
            data["launch_target"] = old_path
        if "launch_type" not in data:
            if old_path.lower().endswith(".lnk"):
                data["launch_type"] = LaunchType.SHORTCUT
            elif source == "Manual":
                data["launch_type"] = LaunchType.PORTABLE
            else:
                data["launch_type"] = LaunchType.WIN32
        if "is_user_managed" not in data:
            data["is_user_managed"] = source == "Manual"
        return data

    @model_validator(mode="after")
    def finish_migration(self) -> "AppEntry":
        """规范化别名与启动目标"""
        self.aliases = normalize_aliases(self.aliases or [self.alias])
        self.alias = self.aliases[0] if self.aliases else ""
        if self.launch_type != LaunchType.STORE_AUMID:
            self.exe_path = self.exe_path or self.launch_target
            self.launch_target = self.launch_target or self.exe_path
        else:
            self.exe_path = ""
        if not self.stable_identity:
            self.stable_identity = make_stable_identity(
                self.launch_type, self.launch_target, self.resolved_target
            )
        return self

    def replace_aliases(self, values: List[str]) -> None:
        """替换别名列表"""
        self.aliases = normalize_aliases(values)
        self.alias = self.aliases[0] if self.aliases else ""

    def merge_aliases(self, values: List[str]) -> None:
        """合并别名列表"""
        self.replace_aliases([*self.aliases, *values])

    def update_display_name_and_aliases(self, new_display_name: str, new_source: str = "") -> None:
        """更新主显示名称并安全重组别名列表，确保友好名称位于首位，原名称保留"""
        from src.catalog.name_quality import promote_alias_order
        clean_new = (new_display_name or "").strip()
        if not clean_new:
            return
        old_name = self.display_name
        self.display_name = clean_new
        if new_source:
            self.display_name_source = new_source
        combined = [clean_new]
        if old_name and old_name.casefold() != clean_new.casefold():
            combined.append(old_name)
        combined.extend(self.aliases)
        self.replace_aliases(promote_alias_order(combined, clean_new))

    def check_validity(self) -> bool:
        """检查条目有效性"""
        if self.launch_type == LaunchType.STORE_AUMID:
            self.last_verified = time.time()
            return self.is_valid
        if self.is_restricted:
            self.is_valid = False
            self.is_enabled = False
        else:
            self.is_valid = bool(
                self.launch_target
                and os.path.exists(self.launch_target)
                and not os.path.isdir(self.launch_target)
            )
        self.last_verified = time.time()
        return self.is_valid
