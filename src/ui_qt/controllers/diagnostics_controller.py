"""脱敏诊断控制器。

生成无敏感信息的摘要信息，并对导出的日志执行安全脱敏，
绝不输出任何 Token、连接码原文或未脱敏的用户私有地址。
"""

from __future__ import annotations

from pathlib import Path

from src.core.config import AppPaths
from src.core.logger import sanitize_text
from src.ui_qt.presentation.apps import AppBucket, build_app_items


class DiagnosticsController:
    """诊断控制器，负责生成状态摘要与脱敏日志导出。"""

    def __init__(self, config_manager, catalog_manager, log_dir=None):
        self.config_manager = config_manager
        self.catalog_manager = catalog_manager
        self.log_dir = Path(log_dir or AppPaths.get_log_dir())

    def build_summary(self, product_status: str, tray_available: bool) -> str:
        """构建面向普通用户的脱敏状态摘要字符串。"""
        self.config_manager.load_config()
        apps = self.catalog_manager.get_all_apps()
        items = build_app_items(apps)
        enabled = len([item for item in items if item.bucket is AppBucket.ALLOWED])
        needs_attention = len([item for item in items if item.bucket is AppBucket.NEEDS_ATTENTION])
        credential_state = "需要重新连接" if self.config_manager.last_load_error else "已安全保存"
        return "\n".join((
            f"当前状态：{product_status}",
            f"连接信息：{credential_state}",
            f"已允许应用：{enabled}",
            f"需要处理：{needs_attention}",
            f"系统托盘：{'可用' if tray_available else '不可用'}",
        ))

    def export_logs(self, destination: str | Path) -> None:
        """导出当前日志目录下的日志文件并统一进行脱敏过滤。"""
        target = Path(destination)
        lines = []
        if self.log_dir.exists():
            for source in sorted(self.log_dir.glob("*.log")):
                try:
                    for line in source.read_text(encoding="utf-8", errors="replace").splitlines():
                        lines.append(sanitize_text(line))
                except Exception:
                    continue
        target.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
