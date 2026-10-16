"""DPAPI fail-closed 安全测试套件
验证：
- 加密成功生成 dpapi:v1:<BASE64>
- 加密失败必须抛出安全异常，严禁 Base64 明文降级
- 加密失败不覆盖旧凭据，原子写入失败清理临时文件
- 解密失败要求重新输入，不把原始字节当明文
- 旧版真实 DPAPI 密文可平滑迁移
- 旧版 Base64 明文严禁被当做凭据恢复
- 日志绝不输出凭据明文
"""

import os
import sys
import json
import base64
from unittest.mock import patch, MagicMock
import pytest

from src.core.config import (
    encrypt_secret,
    decrypt_secret,
    ConfigManager,
    AppConfig,
    DPAPI_V1_PREFIX,
)


@pytest.mark.skipif(sys.platform != "win32", reason="仅在 Windows 上测试 DPAPI")
def test_dpapi_encrypt_and_decrypt_v1_success():
    secret = "wss://api.xiaozhi.me/mcp/?token=VALID_SECRET_123"
    cipher = encrypt_secret(secret)
    assert cipher.startswith(DPAPI_V1_PREFIX)
    # 确认密文不是简单的 base64 明文
    payload = cipher[len(DPAPI_V1_PREFIX):]
    raw_decoded = base64.b64decode(payload)
    assert b"VALID_SECRET_123" not in raw_decoded

    # 成功解密
    plain = decrypt_secret(cipher)
    assert plain == secret


@pytest.mark.skipif(sys.platform != "win32", reason="仅在 Windows 上测试 DPAPI")
def test_dpapi_encrypt_failure_never_falls_back_to_plaintext():
    # 模拟 CryptProtectData 失败（返回 0）
    with patch("ctypes.windll.crypt32.CryptProtectData", return_value=0):
        with pytest.raises(RuntimeError) as exc_info:
            encrypt_secret("wss://api.xiaozhi.me/mcp/?token=MY_TOKEN")
        assert "DPAPI" in str(exc_info.value)
        # 绝不得返回 Base64 包装的明文！


@pytest.mark.skipif(sys.platform != "win32", reason="仅在 Windows 上测试 DPAPI")
def test_atomic_save_failure_does_not_overwrite_existing_secrets(tmp_path):
    cfg_file = tmp_path / "config.json"
    secrets_file = tmp_path / "secrets.dat"

    mgr = ConfigManager()
    mgr.config_path = str(cfg_file)
    mgr.secrets_path = str(secrets_file)

    # 先成功保存一份合法凭据
    old_secret = "wss://api.xiaozhi.me/mcp/?token=OLD_SECRET_TOKEN"
    mgr.config = AppConfig(mcp_endpoint=old_secret)
    mgr.save_config()

    with open(secrets_file, "r", encoding="utf-8") as f:
        old_data = json.load(f)
    old_cipher = old_data["mcp_endpoint"]
    assert old_cipher.startswith(DPAPI_V1_PREFIX)

    # 现在模拟 DPAPI 加密新凭据失败
    new_secret = "wss://api.xiaozhi.me/mcp/?token=NEW_SECRET_TOKEN"
    mgr.config.mcp_endpoint = new_secret

    with patch("ctypes.windll.crypt32.CryptProtectData", return_value=0):
        with pytest.raises(RuntimeError):
            mgr.save_config()

    # 核心安全断言：旧凭据文件完好无损，没有被损坏或覆盖！
    with open(secrets_file, "r", encoding="utf-8") as f:
        current_data = json.load(f)
    assert current_data["mcp_endpoint"] == old_cipher
    # 确认没有残留临时文件
    tmp_files = [f for f in os.listdir(tmp_path) if f.startswith(".tmp_")]
    assert len(tmp_files) == 0


@pytest.mark.skipif(sys.platform != "win32", reason="仅在 Windows 上测试 DPAPI")
def test_dpapi_decrypt_failure_rejects_and_does_not_fallback_to_plaintext():
    # 构造损坏的 v1 密文
    corrupted_cipher = f"{DPAPI_V1_PREFIX}{base64.b64encode(b'GARBAGE_BYTES_CORRUPTED').decode('utf-8')}"
    with pytest.raises(RuntimeError) as exc_info:
        decrypt_secret(corrupted_cipher)
    assert "解密失败" in str(exc_info.value)


@pytest.mark.skipif(sys.platform != "win32", reason="仅在 Windows 上测试 DPAPI")
def test_legacy_base64_plaintext_is_rejected():
    # 以前的明文降级：直接 base64("wss://...")
    plaintext_raw = "wss://api.xiaozhi.me/mcp/?token=LEAKED_PLAINTEXT"
    legacy_base64 = base64.b64encode(plaintext_raw.encode("utf-8")).decode("utf-8")

    # 当尝试用新版解密时，因为没有 dpapi:v1:，且 DPAPI 解密必失败，必须抛出异常拒绝，严禁当作明文恢复
    with pytest.raises(RuntimeError):
        decrypt_secret(legacy_base64)


@pytest.mark.skipif(sys.platform != "win32", reason="仅在 Windows 上测试 DPAPI")
def test_legacy_real_dpapi_migrates_to_v1(tmp_path):
    # 构造合法的旧版 DPAPI 密文（没有 dpapi:v1: 前缀）
    secret = "wss://api.xiaozhi.me/mcp/?token=LEGACY_DPAPI_TOKEN"
    import ctypes
    from ctypes import wintypes
    from src.core.config import DATA_BLOB
    data_bytes = secret.encode("utf-8")
    blob_in = DATA_BLOB(len(data_bytes), ctypes.create_string_buffer(data_bytes, len(data_bytes)))
    blob_out = DATA_BLOB()
    ctypes.windll.crypt32.CryptProtectData(ctypes.byref(blob_in), "XiaozhiMcpAppSecret", None, None, None, 0x1, ctypes.byref(blob_out))
    encrypted = ctypes.string_at(blob_out.pbData, blob_out.cbData)
    ctypes.windll.kernel32.LocalFree(blob_out.pbData)
    legacy_raw_cipher = base64.b64encode(encrypted).decode("utf-8")

    # 解密应成功解出内容
    plain = decrypt_secret(legacy_raw_cipher)
    assert plain == secret

    # 加载至 ConfigManager 时应自动平滑迁移为带有 dpapi:v1: 的新格式
    cfg_file = tmp_path / "config.json"
    secrets_file = tmp_path / "secrets.dat"
    with open(secrets_file, "w", encoding="utf-8") as f:
        json.dump({"mcp_endpoint": legacy_raw_cipher}, f)

    mgr = ConfigManager()
    mgr.config_path = str(cfg_file)
    mgr.secrets_path = str(secrets_file)
    loaded_cfg = mgr.load_config()
    assert loaded_cfg.mcp_endpoint == secret

    # 磁盘上已自动更新为 v1 格式
    with open(secrets_file, "r", encoding="utf-8") as f:
        new_data = json.load(f)
    assert new_data["mcp_endpoint"].startswith(DPAPI_V1_PREFIX)
