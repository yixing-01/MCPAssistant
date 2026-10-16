"""配置与数据目录管理模块
负责路径解析、设置项读写、DPAPI 凭据安全存储与脱敏。
"""

import os
import sys
import json
import time
import base64
import ctypes
from ctypes import wintypes
from typing import Dict, Any, List, Optional
from pydantic import BaseModel, Field


# -------------------------------------------------------------------------------------------------
# Windows DPAPI 加密/解密实现
# -------------------------------------------------------------------------------------------------
class DATA_BLOB(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_char))
    ]


DPAPI_V1_PREFIX = "dpapi:v1:"


def encrypt_secret(plain_text: str) -> str:
    """使用 Windows DPAPI 加密敏感字符串，实行严格 fail-closed 策略，禁止明文降级"""
    if not plain_text:
        return ""
    if sys.platform != "win32":
        raise RuntimeError("当前操作系统不支持 Windows DPAPI 安全凭据存储")

    data_bytes = plain_text.encode("utf-8")
    blob_in = DATA_BLOB(len(data_bytes), ctypes.create_string_buffer(data_bytes, len(data_bytes)))
    blob_out = DATA_BLOB()

    crypt32 = ctypes.windll.crypt32
    # CRYPTPROTECT_UI_FORBIDDEN = 0x1
    if crypt32.CryptProtectData(
        ctypes.byref(blob_in),
        "MCPAssistantSecret",
        None,
        None,
        None,
        0x1,
        ctypes.byref(blob_out)
    ):
        encrypted = ctypes.string_at(blob_out.pbData, blob_out.cbData)
        kernel32 = ctypes.windll.kernel32
        kernel32.LocalFree(blob_out.pbData)
        b64_cipher = base64.b64encode(encrypted).decode("utf-8")
        return f"{DPAPI_V1_PREFIX}{b64_cipher}"
    else:
        raise RuntimeError("Windows DPAPI 凭据加密失败")


def decrypt_secret(encrypted_text: str) -> str:
    """使用 Windows DPAPI 解密敏感字符串，实行严格 fail-closed 策略"""
    if not encrypted_text:
        return ""

    if sys.platform != "win32":
        raise RuntimeError("当前操作系统不支持 Windows DPAPI 凭据解密")

    if encrypted_text.startswith(DPAPI_V1_PREFIX):
        payload = encrypted_text[len(DPAPI_V1_PREFIX):]
        try:
            raw_bytes = base64.b64decode(payload.encode("utf-8"))
        except Exception:
            raise RuntimeError("DPAPI 凭据密文格式损坏")
    else:
        # 旧版无标识密文兼容逻辑
        try:
            raw_bytes = base64.b64decode(encrypted_text.encode("utf-8"))
        except Exception:
            raise RuntimeError("旧版凭据密文格式损坏")

    try:
        blob_in = DATA_BLOB(len(raw_bytes), ctypes.create_string_buffer(raw_bytes, len(raw_bytes)))
        blob_out = DATA_BLOB()

        crypt32 = ctypes.windll.crypt32
        if crypt32.CryptUnprotectData(
            ctypes.byref(blob_in),
            None,
            None,
            None,
            None,
            0x1,
            ctypes.byref(blob_out)
        ):
            decrypted = ctypes.string_at(blob_out.pbData, blob_out.cbData)
            kernel32 = ctypes.windll.kernel32
            kernel32.LocalFree(blob_out.pbData)
            return decrypted.decode("utf-8")
        else:
            raise RuntimeError("Windows DPAPI 凭据解密失败，请重新配置")
    except Exception as e:
        if isinstance(e, RuntimeError):
            raise e
        raise RuntimeError("Windows DPAPI 凭据解密失败，请重新配置")


# -------------------------------------------------------------------------------------------------
# 应用路径管理器（无硬编码盘符）
# -------------------------------------------------------------------------------------------------
class AppPaths:
    """标准应用存储路径解析器与旧配置安全迁移器"""

    APP_DIR_NAME = "MCPAssistant"
    LEGACY_APP_DIR_NAME = "XiaozhiMcpApp"

    @classmethod
    def get_appdata_dir(cls) -> str:
        """用户配置目录: %AppData%\\MCPAssistant"""
        base = os.environ.get("APPDATA")
        if not base:
            base = os.path.expanduser("~")
        path = os.path.join(base, cls.APP_DIR_NAME)
        os.makedirs(path, exist_ok=True)
        return path

    @classmethod
    def get_local_appdata_dir(cls) -> str:
        """缓存、软件目录和日志目录: %LocalAppData%\\MCPAssistant"""
        base = os.environ.get("LOCALAPPDATA")
        if not base:
            base = os.path.expanduser("~")
        path = os.path.join(base, cls.APP_DIR_NAME)
        os.makedirs(path, exist_ok=True)
        return path

    @classmethod
    def get_legacy_appdata_dir(cls) -> str:
        """旧测试版用户配置目录: %AppData%\\XiaozhiMcpApp"""
        base = os.environ.get("APPDATA")
        if not base:
            base = os.path.expanduser("~")
        return os.path.join(base, cls.LEGACY_APP_DIR_NAME)

    @classmethod
    def get_legacy_local_appdata_dir(cls) -> str:
        """旧测试版数据目录: %LocalAppData%\\XiaozhiMcpApp"""
        base = os.environ.get("LOCALAPPDATA")
        if not base:
            base = os.path.expanduser("~")
        return os.path.join(base, cls.LEGACY_APP_DIR_NAME)

    @staticmethod
    def get_log_dir() -> str:
        """日志目录"""
        path = os.path.join(AppPaths.get_local_appdata_dir(), "logs")
        os.makedirs(path, exist_ok=True)
        return path

    @staticmethod
    def get_config_file_path() -> str:
        """配置文件路径"""
        return os.path.join(AppPaths.get_appdata_dir(), "config.json")

    @staticmethod
    def get_secrets_file_path() -> str:
        """加密凭据文件路径"""
        return os.path.join(AppPaths.get_appdata_dir(), "secrets.dat")

    @staticmethod
    def get_catalog_file_path() -> str:
        """软件目录索引缓存路径"""
        return os.path.join(AppPaths.get_local_appdata_dir(), "catalog.json")

    @classmethod
    def migrate_legacy_data(cls) -> None:
        """从旧测试版目录安全单向迁移配置、DPAPI 凭据密文与应用目录缓存。

        规则：
        1. 新目录已有文件时，以新目录为准，绝不覆盖；
        2. 新目录不存在、旧目录存在时，安全复制对应文件到新目录；
        3. 至少迁移：config.json, secrets.dat, catalog.json；
        4. 旧目录与旧文件绝对不删除；
        5. 使用临时文件原子化写入替换，密文原样复制，绝不以明文落盘；
        6. 遇到任何异常捕获并输出脱敏日志，回退到安全默认值。
        """
        new_appdata = cls.get_appdata_dir()
        new_local = cls.get_local_appdata_dir()
        legacy_appdata = cls.get_legacy_appdata_dir()
        legacy_local = cls.get_legacy_local_appdata_dir()

        migration_items = [
            (os.path.join(legacy_appdata, "config.json"), cls.get_config_file_path(), new_appdata),
            (os.path.join(legacy_appdata, "secrets.dat"), cls.get_secrets_file_path(), new_appdata),
            (os.path.join(legacy_local, "catalog.json"), cls.get_catalog_file_path(), new_local),
        ]

        for src_path, dst_path, target_dir in migration_items:
            if os.path.exists(dst_path):
                # 目标文件已存在，以新目录为准，绝不覆盖
                continue
            if os.path.exists(src_path) and os.path.isfile(src_path):
                tmp_path = os.path.join(
                    target_dir,
                    f".tmp_mig_{os.path.basename(dst_path)}_{os.getpid()}_{time.time_ns()}"
                )
                try:
                    os.makedirs(target_dir, exist_ok=True)
                    with open(src_path, "rb") as f_src:
                        content = f_src.read()
                    with open(tmp_path, "wb") as f_dst:
                        f_dst.write(content)
                        f_dst.flush()
                        try:
                            os.fsync(f_dst.fileno())
                        except Exception:
                            pass
                    os.replace(tmp_path, dst_path)
                    logger.info("已安全迁移旧版文件至新路径: %s", os.path.basename(dst_path))
                except Exception as e:
                    logger.warning("迁移旧版配置失败 (%s): %s", os.path.basename(dst_path), type(e).__name__)
                finally:
                    if os.path.exists(tmp_path):
                        try:
                            os.remove(tmp_path)
                        except Exception:
                            pass


# -------------------------------------------------------------------------------------------------
# 配置数据模型
# -------------------------------------------------------------------------------------------------
class AppConfig(BaseModel):
    """应用全局配置模型"""
    mcp_endpoint: str = Field(default="", description="小智 WebSocket 服务端接入点")
    scan_agreed: bool = Field(default=False, description="用户是否同意读取本机已安装软件信息")
    auto_start_with_windows: bool = Field(default=False, description="是否开机自启动（默认关闭）")
    minimize_to_tray_on_close: bool = Field(default=True, description="关闭主窗口时最小化到系统托盘")

    # 安全与权限设置
    allow_calculator: bool = Field(default=True, description="是否允许数学计算器工具")
    allow_open_url: bool = Field(default=True, description="是否允许打开网页")
    allow_run_apps: bool = Field(default=True, description="是否允许控制已授权软件的启动与关闭")
    allow_cmd_commands: bool = Field(default=False, description="是否允许执行CMD命令白名单（默认关闭）")
    allow_file_write: bool = Field(default=False, description="是否允许写入文件（默认关闭）")

    allowed_write_dirs: List[str] = Field(default_factory=list, description="允许写入文件的受信任目录白名单")
    cmd_whitelist: Dict[str, str] = Field(
        default_factory=lambda: {
            "IP配置": "ipconfig",
            "系统信息": "systeminfo",
            "网络状态": "netstat -ano",
            "锁定电脑": "rundll32.exe user32.dll,LockWorkStation"
        },
        description="允许执行的命令白名单（键为别名，值为固定命令）"
    )

    def mask_endpoint(self) -> str:
        """脱敏展示服务端 Endpoint"""
        from src.core.endpoint import mask_mcp_endpoint
        if not self.mcp_endpoint:
            return "未配置"
        return mask_mcp_endpoint(self.mcp_endpoint)


def _atomic_write_json(file_path: str, data: Any):
    """原子化写入 JSON 文件，写入临时文件成功后再进行原子替换"""
    abs_path = os.path.abspath(file_path)
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
    dir_name = os.path.dirname(abs_path)
    temp_file = os.path.join(dir_name, f".tmp_{os.path.basename(abs_path)}_{os.getpid()}_{id(data)}")
    try:
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(temp_file, abs_path)
    finally:
        if os.path.exists(temp_file):
            try:
                os.remove(temp_file)
            except Exception:
                pass


import logging

logger = logging.getLogger(__name__)


class ConfigManager:
    """配置持久化管理器"""

    def __init__(self):
        AppPaths.migrate_legacy_data()
        self.config_path = AppPaths.get_config_file_path()
        self.secrets_path = AppPaths.get_secrets_file_path()
        self.last_load_error: Optional[str] = None
        self.config: AppConfig = self.load_config()

    def load_config(self) -> AppConfig:
        """从磁盘加载配置并解密敏感数据，实施严格 fail-closed 策略"""
        data: Dict[str, Any] = {}
        load_error: Optional[str] = None

        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                    if isinstance(loaded, dict):
                        data = loaded
                    else:
                        load_error = "config_invalid"
            except Exception:
                logger.warning("读取配置文件失败，采用安全默认配置")
                load_error = "config_invalid"
                data = {}

        loaded_endpoint = ""
        should_migrate = False
        # 尝试从凭据加密文件中解密敏感字段
        if os.path.exists(self.secrets_path):
            try:
                with open(self.secrets_path, "r", encoding="utf-8") as f:
                    encrypted_secrets = json.load(f)
                if isinstance(encrypted_secrets, dict):
                    raw_cipher = encrypted_secrets.get("mcp_endpoint", "")
                    if raw_cipher:
                        loaded_endpoint = decrypt_secret(raw_cipher)
                        if not raw_cipher.startswith(DPAPI_V1_PREFIX) and loaded_endpoint:
                            should_migrate = True
                else:
                    load_error = "credential_unreadable"
            except Exception:
                logger.warning("解密存储凭据失败，需用户重新输入")
                load_error = "credential_unreadable"
                loaded_endpoint = ""

        data["mcp_endpoint"] = loaded_endpoint

        try:
            cfg = AppConfig(**data) if data else AppConfig()
        except Exception:
            logger.warning("配置数据校验失败，采用安全默认配置")
            load_error = "config_invalid"
            cfg = AppConfig()

        self.last_load_error = load_error

        # 仅在旧凭据合法且解密完全成功时平滑迁移至 v1；加载失败时绝不修改原文件
        if should_migrate and not load_error:
            try:
                self.save_config(cfg)
            except Exception:
                pass

        return cfg

    def save_config(self, config: Optional[AppConfig] = None):
        """双文件事务式保存：先加密序列化并双临时文件落盘，再执行两阶段原子替换与回滚保护"""
        target_config = config.model_copy(deep=True) if config is not None else self.config.model_copy(deep=True)

        data = target_config.model_dump()
        endpoint = data.pop("mcp_endpoint", "").strip()

        # 1. DPAPI 加密凭据（失败直接抛出异常，绝不触碰磁盘任何文件，避免明文泄露）
        encrypted_endpoint = encrypt_secret(endpoint) if endpoint else ""
        secrets_data = {
            "mcp_endpoint": encrypted_endpoint
        }
        config_data = data

        sec_abs = os.path.abspath(self.secrets_path)
        cfg_abs = os.path.abspath(self.config_path)
        os.makedirs(os.path.dirname(sec_abs), exist_ok=True)
        os.makedirs(os.path.dirname(cfg_abs), exist_ok=True)

        tmp_sec = os.path.join(os.path.dirname(sec_abs), f".tmp_{os.path.basename(sec_abs)}_{os.getpid()}_{time.time_ns()}")
        tmp_cfg = os.path.join(os.path.dirname(cfg_abs), f".tmp_{os.path.basename(cfg_abs)}_{os.getpid()}_{time.time_ns()}")

        try:
            # 2. 第一阶段：两临时文件分别写入、flush 并 fsync 落盘
            with open(tmp_sec, "w", encoding="utf-8") as f:
                json.dump(secrets_data, f, ensure_ascii=False, indent=2)
                f.flush()
                try:
                    os.fsync(f.fileno())
                except Exception:
                    pass

            with open(tmp_cfg, "w", encoding="utf-8") as f:
                json.dump(config_data, f, ensure_ascii=False, indent=2)
                f.flush()
                try:
                    os.fsync(f.fileno())
                except Exception:
                    pass

            # 3. 记录旧正式文件状态（用于两阶段替换失败时的回滚）
            sec_existed = os.path.exists(sec_abs)
            sec_old_bytes = None
            if sec_existed:
                try:
                    with open(sec_abs, "rb") as f:
                        sec_old_bytes = f.read()
                except Exception as e:
                    raise OSError("读取旧凭据备份失败") from e

            # 4. 第二阶段：执行第一个正式替换 (secrets.dat)
            os.replace(tmp_sec, sec_abs)

            # 5. 执行第二个正式替换 (config.json)
            try:
                os.replace(tmp_cfg, cfg_abs)
            except Exception as e:
                # 第二个替换失败：必须对第一个文件进行严格回滚
                logger.error("配置文件替换失败，正在回滚凭据文件")
                rollback_err = None
                try:
                    if sec_existed and sec_old_bytes is not None:
                        with open(tmp_sec, "wb") as f:
                            f.write(sec_old_bytes)
                            f.flush()
                            try:
                                os.fsync(f.fileno())
                            except Exception:
                                pass
                        os.replace(tmp_sec, sec_abs)
                    else:
                        if os.path.exists(sec_abs):
                            os.remove(sec_abs)
                except Exception as rb_ex:
                    rollback_err = rb_ex

                if rollback_err:
                    raise RuntimeError("配置存储一致性回滚失败") from rollback_err
                raise e

            # 6. 两文件均替换成功后，才更新内存配置
            self.config = target_config

        finally:
            # 清理本轮创建的临时文件
            for p in (tmp_sec, tmp_cfg):
                if os.path.exists(p):
                    try:
                        os.remove(p)
                    except Exception:
                        pass
