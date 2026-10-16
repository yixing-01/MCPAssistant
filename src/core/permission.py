"""权限控制与操作审计模块
负责 MCP 工具调用的权限验证和严格的操作审计日志记录。
"""

import time
from typing import Optional, Dict, Any, List
from pydantic import BaseModel, Field
from src.core.logger import app_logger, sanitize_text


class AuditRecord(BaseModel):
    """MCP 工具调用审计记录模型"""
    timestamp: float = Field(default_factory=time.time, description="操作时间戳")
    formatted_time: str = Field(default="", description="格式化时间")
    tool_name: str = Field(description="工具名称")
    target_type: str = Field(description="目标类型（如软件/网页/计算/文件/命令）")
    target_summary: str = Field(description="脱敏后的目标摘要")
    requires_confirmation: bool = Field(default=False, description="是否需要用户确认")
    success: bool = Field(default=False, description="执行结果是否成功")
    result_or_error: str = Field(default="", description="脱敏后的执行摘要或错误原因")


class PermissionEngine:
    """权限与审计管理引擎"""

    def __init__(self):
        self.audit_records: List[AuditRecord] = []

    def clear_audit(self) -> None:
        """清空当前审计记录列表（测试与维护使用）。"""
        self.audit_records.clear()

    def log_audit(
        self,
        tool_name: str,
        target_type: str,
        target_summary: str,
        success: bool,
        result_or_error: str,
        requires_confirmation: bool = False
    ) -> AuditRecord:
        """记录一次 MCP 操作审计"""
        now_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        clean_target = sanitize_text(target_summary)
        clean_result = sanitize_text(result_or_error)

        record = AuditRecord(
            timestamp=time.time(),
            formatted_time=now_str,
            tool_name=tool_name,
            target_type=target_type,
            target_summary=clean_target,
            requires_confirmation=requires_confirmation,
            success=success,
            result_or_error=clean_result
        )

        self.audit_records.append(record)
        # 保持内存中最多保留 500 条
        if len(self.audit_records) > 500:
            self.audit_records.pop(0)

        # 记录到系统日志中
        status_tag = "成功" if success else "失败"
        app_logger.info(
            f"[审计] 工具: {tool_name} | 类型: {target_type} | 目标: {clean_target} | 结果: {status_tag} | 详情: {clean_result}"
        )
        return record


# 全局审计引擎实例
permission_engine = PermissionEngine()
