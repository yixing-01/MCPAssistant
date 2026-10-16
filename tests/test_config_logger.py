"""配置与脱敏日志单元测试"""

import os
import tempfile
from src.core.config import AppConfig, ConfigManager, encrypt_secret, decrypt_secret, AppPaths
from src.core.logger import sanitize_text, MemoryLogHandler, SanitizedFormatter
import logging


def test_secret_encryption_and_decryption():
    """测试敏感凭据加解密"""
    original = "my_secret_token_123456789"
    encrypted = encrypt_secret(original)
    assert encrypted != original
    assert len(encrypted) > 0

    decrypted = decrypt_secret(encrypted)
    assert decrypted == original


def test_sanitize_text():
    """测试日志与字符串脱敏功能"""
    raw_1 = "Connected with token=secret_abc_123456 to endpoint"
    clean_1 = sanitize_text(raw_1)
    assert "secret_abc_123456" not in clean_1
    assert "token=***REDACTED***" in clean_1

    raw_2 = "Authorization: Bearer my_super_secret_jwt_token"
    clean_2 = sanitize_text(raw_2)
    assert "my_super_secret_jwt_token" not in clean_2
    assert "Bearer ***REDACTED***" in clean_2


def test_config_manager_io():
    """测试配置管理器读写"""
    with tempfile.TemporaryDirectory() as temp_dir:
        cfg = AppConfig(
            mcp_endpoint="ws://localhost:8080/mcp",
            scan_agreed=True,
            allow_calculator=True,
            allow_run_apps=True
        )

        # 验证脱敏展示
        masked = cfg.mask_endpoint()
        assert "localhost" not in masked or "***" in masked


def test_memory_log_handler():
    """测试内存日志处理器"""
    handler = MemoryLogHandler(max_entries=10)
    handler.setFormatter(SanitizedFormatter("%(message)s"))

    received = []
    handler.add_listener(lambda msg: received.append(msg))

    logger = logging.getLogger("TestMemLogger")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

    logger.info("测试消息 token=123456")
    logs = handler.get_logs()
    assert len(logs) == 1
    assert "token=***REDACTED***" in logs[0]
    assert len(received) == 1
