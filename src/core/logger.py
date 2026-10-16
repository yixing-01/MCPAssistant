"""脱敏日志模块
提供自动敏感词过滤、双通道输出（控制台与本地日志文件）以及 GUI 内存日志缓存。
"""

import os
import re
import sys
import logging
from collections import deque
from typing import Callable, List, Optional

# 正则表达式屏蔽敏感凭据（如 Token、Bearer、Key 等）、URL凭据与用户私有路径
_SENSITIVE_PATTERNS = [
    (re.compile(r'(authorization\s*[:=]\s*(?:bearer\s+)?)[^\s&,;"]+', re.IGNORECASE), r'\1***REDACTED***'),
    (re.compile(r'(bearer\s+)[^\s&,;"]+', re.IGNORECASE), r'\1***REDACTED***'),
    (re.compile(r'(token\s*[:=]\s*)[^\s&,;"]+', re.IGNORECASE), r'\1***REDACTED***'),
    (re.compile(r'(api[_-]?key\s*[:=]\s*)[^\s&,;"]+', re.IGNORECASE), r'\1***REDACTED***'),
    (re.compile(r'(secret\s*[:=]\s*)[^\s&,;"]+', re.IGNORECASE), r'\1***REDACTED***'),
    (re.compile(r'(password\s*[:=]\s*)[^\s&,;"]+', re.IGNORECASE), r'\1***REDACTED***'),
    (re.compile(r'((?:https?|wss?)://)([^:@/\s]+):([^@/\s]+)@', re.IGNORECASE), r'\1***:***@'),
    (re.compile(r'((?:https?|wss?)://)([^@/\s]+)@', re.IGNORECASE), r'\1***@'),
    (re.compile(r'([?&](?:token|key|api[_-]?key|secret|password|auth|access[_-]?token)\s*=\s*)[^&\s#"]+', re.IGNORECASE), r'\1***REDACTED***'),
    (re.compile(r'([a-zA-Z]:\\(?:Users|users)\\)([^\\]+)', re.IGNORECASE), r'\1***'),
    (re.compile(r'([a-zA-Z]:/(?:Users|users)/)([^/]+)', re.IGNORECASE), r'\1***'),
]


def sanitize_text(text: str) -> str:
    """脱敏处理文本中的敏感凭证、URL凭证与用户私有路径"""
    if not isinstance(text, str):
        text = str(text)
    for pattern, repl in _SENSITIVE_PATTERNS:
        text = pattern.sub(repl, text)
    return text


class SanitizedFormatter(logging.Formatter):
    """日志格式化器，在输出前对日志内容进行脱敏"""

    def format(self, record: logging.LogRecord) -> str:
        original = super().format(record)
        return sanitize_text(original)


class MemoryLogHandler(logging.Handler):
    """内存日志处理器，保留最新的日志条目供 GUI 实时渲染"""

    def __init__(self, max_entries: int = 1000):
        super().__init__()
        self.buffer: deque = deque(maxlen=max_entries)
        self.listeners: List[Callable[[str], None]] = []

    def emit(self, record: logging.LogRecord):
        try:
            msg = self.format(record)
            self.buffer.append(msg)
            for listener in list(self.listeners):
                try:
                    listener(msg)
                except Exception:
                    pass
        except Exception:
            self.handleError(record)

    def add_listener(self, callback: Callable[[str], None]):
        """注册实时日志监听器"""
        if callback not in self.listeners:
            self.listeners.append(callback)

    def remove_listener(self, callback: Callable[[str], None]):
        """移除日志监听器"""
        if callback in self.listeners:
            self.listeners.remove(callback)

    def get_logs(self) -> List[str]:
        """获取当前缓存的日志列表"""
        return list(self.buffer)

    def clear(self):
        """清空缓存日志"""
        self.buffer.clear()


# 全局内存日志缓存实例
memory_log_handler = MemoryLogHandler(max_entries=2000)


def setup_logger(log_dir: Optional[str] = None, log_level: int = logging.INFO, to_stderr: bool = False) -> logging.Logger:
    """初始化并配置应用日志记录器"""
    logger = logging.getLogger("MCPAssistant")
    logger.setLevel(log_level)

    # 避免重复添加处理器
    if logger.handlers:
        return logger

    formatter = SanitizedFormatter(
        fmt="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    # 控制台输出流：若 to_stderr 为 True，输出到 sys.stderr，严禁污染 stdout
    stream_target = sys.stderr if to_stderr else sys.stdout
    console_handler = logging.StreamHandler(stream_target)
    console_handler.setFormatter(formatter)
    console_handler.setLevel(log_level)
    logger.addHandler(console_handler)

    # 内存日志流（供 UI 使用）
    memory_log_handler.setFormatter(formatter)
    memory_log_handler.setLevel(log_level)
    logger.addHandler(memory_log_handler)

    # 文件输出流
    if log_dir:
        try:
            os.makedirs(log_dir, exist_ok=True)
            log_file_path = os.path.join(log_dir, "app.log")
            file_handler = logging.FileHandler(log_file_path, encoding="utf-8")
            file_handler.setFormatter(formatter)
            file_handler.setLevel(log_level)
            logger.addHandler(file_handler)
        except Exception as e:
            logger.warning(f"无法初始化文件日志: {e}")

    return logger


def configure_for_mcp_service(log_dir: Optional[str] = None, log_level: int = logging.INFO) -> logging.Logger:
    """为 MCP stdio 子服务配置专用日志记录器，确保 stdout 绝对干净，仅供 JSON-RPC 传输"""
    logger = logging.getLogger("MCPAssistant")
    logger.setLevel(log_level)

    # 移除现有所有 handler，确保没有遗留的 sys.stdout handler
    for h in list(logger.handlers):
        logger.removeHandler(h)

    # 根 logger 也清理 stdout handler，避免第三方库默认输出污染 stdout
    root_logger = logging.getLogger()
    for h in list(root_logger.handlers):
        if isinstance(h, logging.StreamHandler) and getattr(h, "stream", None) == sys.stdout:
            root_logger.removeHandler(h)

    formatter = SanitizedFormatter(
        fmt="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    # 严格仅向 stderr 输出日志
    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setFormatter(formatter)
    stderr_handler.setLevel(log_level)
    logger.addHandler(stderr_handler)

    # 文件输出流
    if log_dir:
        try:
            os.makedirs(log_dir, exist_ok=True)
            log_file_path = os.path.join(log_dir, "mcp_service.log")
            file_handler = logging.FileHandler(log_file_path, encoding="utf-8")
            file_handler.setFormatter(formatter)
            file_handler.setLevel(log_level)
            logger.addHandler(file_handler)
        except Exception:
            pass

    return logger


# 获取全局 logger 实例
app_logger = logging.getLogger("MCPAssistant")

# 兼容导出 AppPaths
from src.core.config import AppPaths
