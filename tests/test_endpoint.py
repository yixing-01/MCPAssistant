"""统一 MCP 接入地址解析与校验模块测试套件
严格按照规范验证：
- mcp:wss:// 与 wss:// 前缀处理
- /mcp 与 /mcp/ 规范化
- 不安全 scheme、非官方域名、用户信息、fragment、异常端口、Token 异常等全面拦截
- 脱敏掩码绝不泄露首尾字符
- 错误信息固定分类且不回显用户输入
"""

import pytest
from src.core.endpoint import (
    normalize_mcp_endpoint,
    validate_mcp_endpoint,
    mask_mcp_endpoint,
    EndpointValidationResult,
)


def test_normalize_valid_endpoints():
    # 1. 标准 mcp:wss:// 格式
    raw1 = "mcp:wss://api.xiaozhi.me/mcp/?token=SECRET_TEST_TOKEN_123"
    assert normalize_mcp_endpoint(raw1) == "wss://api.xiaozhi.me/mcp/?token=SECRET_TEST_TOKEN_123"

    # 2. 直接 wss:// 格式
    raw2 = "wss://api.xiaozhi.me/mcp/?token=SECRET_TEST_TOKEN_123"
    assert normalize_mcp_endpoint(raw2) == "wss://api.xiaozhi.me/mcp/?token=SECRET_TEST_TOKEN_123"

    # 3. 路径为 /mcp 时自动规范化为 /mcp/
    raw3 = "mcp:wss://api.xiaozhi.me/mcp?token=SECRET_TEST_TOKEN_123"
    assert normalize_mcp_endpoint(raw3) == "wss://api.xiaozhi.me/mcp/?token=SECRET_TEST_TOKEN_123"

    # 4. 外层包含空白字符自动清洗
    raw4 = "  mcp:wss://api.xiaozhi.me/mcp/?token=SECRET_TEST_TOKEN_123  \t"
    assert normalize_mcp_endpoint(raw4) == "wss://api.xiaozhi.me/mcp/?token=SECRET_TEST_TOKEN_123"

    # 5. 显式 443 端口规范化为默认端口
    raw5 = "wss://api.xiaozhi.me:443/mcp/?token=SECRET_TEST_TOKEN_123"
    assert normalize_mcp_endpoint(raw5) == "wss://api.xiaozhi.me/mcp/?token=SECRET_TEST_TOKEN_123"


def test_reject_insecure_and_invalid_endpoints():
    invalid_cases = [
        # 不安全 scheme
        ("ws://api.xiaozhi.me/mcp/?token=test", "只允许安全的 WSS 地址"),
        ("http://api.xiaozhi.me/mcp/?token=test", "只允许安全的 WSS 地址"),
        ("https://api.xiaozhi.me/mcp/?token=test", "只允许安全的 WSS 地址"),
        ("file:///C:/test", "只允许安全的 WSS 地址"),
        # 非白名单域名
        ("wss://evil.com/mcp/?token=test", "当前版本仅支持 api.xiaozhi.me 的安全 MCP 接入地址"),
        ("wss://api.xiaozhi.me.attacker.com/mcp/?token=test", "当前版本仅支持 api.xiaozhi.me 的安全 MCP 接入地址"),
        # 用户信息 (user:pass@)
        ("wss://admin:pass@api.xiaozhi.me/mcp/?token=test", "接入地址格式不正确"),
        # fragment
        ("wss://api.xiaozhi.me/mcp/?token=test#frag", "接入地址格式不正确"),
        # 异常端口
        ("wss://api.xiaozhi.me:8080/mcp/?token=test", "接入地址格式不正确"),
        ("wss://api.xiaozhi.me:80/mcp/?token=test", "接入地址格式不正确"),
        # 错误路径
        ("wss://api.xiaozhi.me/other/?token=test", "接入地址格式不正确"),
        ("wss://api.xiaozhi.me/?token=test", "接入地址格式不正确"),
        # Token 异常
        ("wss://api.xiaozhi.me/mcp/", "接入地址缺少 Token"),
        ("wss://api.xiaozhi.me/mcp/?token=", "接入地址缺少 Token"),
        ("wss://api.xiaozhi.me/mcp/?other=123", "接入地址缺少 Token"),
        ("wss://api.xiaozhi.me/mcp/?token=1&token=2", "接入地址格式不正确"),
        # 嵌入控制字符或换行
        ("wss://api.xiaozhi.me/mcp/?token=test\n", "接入地址格式不正确"),
        ("wss://api.xiaozhi.me/mcp/?token=te\rst", "接入地址格式不正确"),
        ("wss://api.xiaozhi.me/mcp/?token=te\x00st", "接入地址格式不正确"),
        # 空地址
        ("", "接入地址不能为空"),
        ("   ", "接入地址不能为空"),
    ]

    for raw, expected_err in invalid_cases:
        res = validate_mcp_endpoint(raw)
        assert res.is_valid is False, f"应拒绝但未拒绝: {raw}"
        assert expected_err in res.error_message
        # 错误信息绝不得回显用户的输入或敏感 Token
        assert "SECRET" not in res.error_message
        assert "attacker" not in res.error_message


def test_mask_mcp_endpoint():
    url = "wss://api.xiaozhi.me/mcp/?token=SECRET_LONG_TOKEN_999888777"
    masked = mask_mcp_endpoint(url)
    assert masked == "wss://api.xiaozhi.me/mcp/?token=••••••"
    # 绝不泄露 Token 首字符、尾字符或后四位
    assert "SECRET" not in masked
    assert "999888777" not in masked
    assert "777" not in masked


def test_no_bypass_and_local_rejected():
    # 无论任何情况，本地测试地址必须被严格拒绝
    local_url = "ws://127.0.0.1:8765/test_mcp?token=mock_tok"
    assert validate_mcp_endpoint(local_url).is_valid is False
    assert validate_mcp_endpoint("ws://localhost:8765/mcp/?token=test").is_valid is False
    assert validate_mcp_endpoint("wss://127.0.0.1/mcp/?token=test").is_valid is False
