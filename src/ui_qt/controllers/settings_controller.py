"""普通设置控制器。

提供配置与注册表自启项的双向同步与事务一致性，
严格限制仅允许修改白名单中的安全业务字段，并防止非法的目录配置。
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping

from PySide6.QtCore import QObject, Signal, Slot

from src.core.autostart import is_autostart_enabled, set_autostart
from src.core.logger import app_logger
from src.core.security import PathSandbox

# 允许普通用户界面修改的设置白名单字段
EDITABLE_FIELDS = {
    "auto_start_with_windows",
    "minimize_to_tray_on_close",
    "allow_run_apps",
    "allow_open_url",
    "allow_file_write",
}


class SettingsController(QObject):
    """设置控制器，负责加载、安全校验与事务式保存设置。"""

    loaded = Signal(dict)
    saved = Signal(dict)
    save_failed = Signal(str)

    def __init__(
        self,
        config_manager,
        get_autostart: Callable[[], bool] = is_autostart_enabled,
        set_autostart: Callable[[bool], bool] = set_autostart,
        parent=None,
    ):
        super().__init__(parent)
        self.config_manager = config_manager
        self.get_autostart = get_autostart
        self.set_autostart = set_autostart

    @Slot()
    def load(self) -> None:
        """从配置管理器读取当前配置并向界面发出安全字段字典。"""
        from src.core.endpoint import validate_mcp_endpoint

        config = self.config_manager.load_config()
        values = {name: getattr(config, name) for name in EDITABLE_FIELDS}
        safe_dirs = []
        for raw_dir in config.allowed_write_dirs:
            clean = str(raw_dir or "").strip()
            if not clean:
                continue
            is_safe, reason = PathSandbox.is_safe_write_directory(clean)
            if is_safe:
                normalized = os.path.realpath(os.path.abspath(clean))
                if normalized not in safe_dirs:
                    safe_dirs.append(normalized)
            else:
                app_logger.warning(f"加载设置时跳过不安全的保存目录: 原因: {reason}")
        values["allowed_write_dirs"] = safe_dirs
        val = validate_mcp_endpoint(config.mcp_endpoint or "")
        values["has_valid_endpoint"] = val.is_valid
        values["masked_endpoint"] = "wss://api.xiaozhi.me/mcp/?token=••••••" if val.is_valid else "尚未设置连接码"
        self.loaded.emit(values)

    @Slot(dict)
    def save(self, changes: Mapping[str, object]) -> None:
        """事务式保存用户修改，若配置保存失败自动回滚注册表开机自启项。"""
        safe_changes = {
            key: bool(value) for key, value in changes.items() if key in EDITABLE_FIELDS
        }
        current = self.config_manager.load_config()
        candidate = current.model_copy(deep=True)
        requested_dirs = changes.get("allowed_write_dirs", current.allowed_write_dirs)
        safe_dirs = []
        rejected_reasons = []
        for raw_path in requested_dirs:
            clean = str(raw_path or "").strip()
            if not clean:
                continue
            is_safe, reason = PathSandbox.is_safe_write_directory(clean)
            if not is_safe:
                rejected_reasons.append(reason)
                app_logger.warning(f"拒绝不安全的保存目录: 原因: {reason}")
                continue
            normalized = os.path.realpath(os.path.abspath(clean))
            if normalized not in safe_dirs:
                safe_dirs.append(normalized)

        # 若开启了文件写入权限，校验目录合法性
        wants_file_write = safe_changes.get("allow_file_write", current.allow_file_write)
        if wants_file_write:
            if rejected_reasons and not safe_dirs:
                self.save_failed.emit(rejected_reasons[0])
                return
            if not safe_dirs:
                self.save_failed.emit("请先选择允许通过 MCP 保存文件的文件夹。")
                return
            if rejected_reasons:
                self.save_failed.emit(rejected_reasons[0])
                return

        candidate.allowed_write_dirs = safe_dirs
        previous_registry = self.get_autostart()
        desired_registry = safe_changes.get("auto_start_with_windows", previous_registry)

        # 若注册表自启项发生变化，先尝试更新注册表
        if desired_registry != previous_registry and not self.set_autostart(desired_registry):
            self.save_failed.emit("开机自动准备没有保存，请重试。")
            return

        for key, value in safe_changes.items():
            setattr(candidate, key, value)

        try:
            self.config_manager.save_config(candidate)
        except Exception:
            # 配置保存发生异常时，对注册表自启项进行回滚
            rollback_ok = (
                desired_registry == previous_registry
                or self.set_autostart(previous_registry)
            )
            message = (
                "设置没有保存，系统启动项也未能恢复，请保持窗口打开并重试。"
                if not rollback_ok
                else "设置没有保存，原来的设置已恢复。"
            )
            self.save_failed.emit(message)
            return

        values = {name: getattr(candidate, name) for name in EDITABLE_FIELDS}
        values["allowed_write_dirs"] = list(candidate.allowed_write_dirs)
        self.saved.emit(values)
