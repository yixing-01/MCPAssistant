"""Windows .lnk 快捷方式统一安全解析与校验模块
仅使用当前进程内 Windows 原生 IShellLinkW / IPersistFile 原生 COM 解析，
彻底杜绝外部进程（PowerShell / cmd / wscript）调用，防范命令注入。
严格实施目标黑名单、无参数策略、类型受限校验与启动前完整性重验。
"""

import os
import sys
import ctypes
from dataclasses import dataclass
from typing import Optional, Set
from src.core.logger import app_logger

from src.catalog.dangerous_targets import (
    DANGEROUS_PROGRAM_FILENAMES,
    is_dangerous_system_program,
)

# 兼容保留原有导出名称
DANGEROUS_TARGET_NAMES: Set[str] = DANGEROUS_PROGRAM_FILENAMES


@dataclass
class ShortcutInfo:
    """快捷方式元数据"""
    shortcut_path: str
    target_path: str = ""
    arguments: str = ""
    working_directory: str = ""
    is_resolved: bool = False
    error_message: str = ""


@dataclass
class ShortcutValidationResult:
    """快捷方式安全校验结果"""
    is_allowed: bool
    block_reason: str = ""
    info: Optional[ShortcutInfo] = None


class ShortcutResolver:
    """统一快捷方式安全解析与校验器"""

    @classmethod
    def resolve(cls, lnk_path: str) -> ShortcutInfo:
        """
        在 Windows 平台解析 .lnk 快捷方式的 TargetPath、Arguments、WorkingDirectory。
        在非 Windows 环境、COM 异常或解析失败时默认拒绝。
        绝对不调用外部进程（PowerShell / cmd / wscript 等）。
        """
        if not lnk_path or not os.path.exists(lnk_path):
            return ShortcutInfo(
                shortcut_path=lnk_path or "",
                is_resolved=False,
                error_message="快捷方式文件不存在"
            )

        if sys.platform != "win32":
            return ShortcutInfo(
                shortcut_path=lnk_path,
                is_resolved=False,
                error_message="非 Windows 系统不支持 .lnk 原生解析"
            )

        # 仅通过 in-process IShellLinkW COM 接口进行原生解析
        try:
            return cls._resolve_via_ishell_link(lnk_path)
        except Exception as e:
            app_logger.warning(f"IShellLinkW 解析快捷方式失败: {type(e).__name__}")
            return ShortcutInfo(
                shortcut_path=lnk_path,
                is_resolved=False,
                error_message="快捷方式无法解析"
            )

    @classmethod
    def _get_shell_link_arguments(cls, p_shell_link, vtable, buf_args) -> int:
        """调用 IShellLinkW::GetArguments (vtable index 10) 并返回 HRESULT"""
        from ctypes import wintypes
        get_args_type = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, wintypes.LPWSTR, ctypes.c_int)
        return get_args_type(vtable[10])(p_shell_link, buf_args, 2048)

    @classmethod
    def _resolve_via_ishell_link(cls, lnk_path: str) -> ShortcutInfo:
        """通过 Windows 原生 IShellLinkW 读取快捷方式内部真实字段"""
        from ctypes import wintypes

        class GUID(ctypes.Structure):
            _fields_ = [
                ('Data1', wintypes.DWORD),
                ('Data2', wintypes.WORD),
                ('Data3', wintypes.WORD),
                ('Data4', wintypes.BYTE * 8)
            ]

        CLSID_ShellLink = GUID(0x00021401, 0, 0, (wintypes.BYTE * 8)(0xC0, 0, 0, 0, 0, 0, 0, 0x46))
        IID_IShellLinkW = GUID(0x000214F9, 0, 0, (wintypes.BYTE * 8)(0xC0, 0, 0, 0, 0, 0, 0, 0x46))
        IID_IPersistFile = GUID(0x0000010B, 0, 0, (wintypes.BYTE * 8)(0xC0, 0, 0, 0, 0, 0, 0, 0x46))

        ole32 = ctypes.windll.ole32
        hr_init = ole32.CoInitialize(None)
        co_initialized = (hr_init == 0 or hr_init == 1)

        p_shell_link = ctypes.c_void_p()
        p_persist_file = ctypes.c_void_p()

        try:
            hr = ole32.CoCreateInstance(
                ctypes.byref(CLSID_ShellLink),
                None,
                1,  # CLSCTX_INPROC_SERVER
                ctypes.byref(IID_IShellLinkW),
                ctypes.byref(p_shell_link)
            )
            if hr != 0 or not p_shell_link.value:
                raise RuntimeError(f"CoCreateInstance(IShellLinkW) 失败: HRESULT {hex(hr)}")

            vtable = ctypes.cast(p_shell_link, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents

            # QueryInterface -> IPersistFile
            qi_type = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p))
            hr = qi_type(vtable[0])(p_shell_link, ctypes.byref(IID_IPersistFile), ctypes.byref(p_persist_file))
            if hr != 0 or not p_persist_file.value:
                raise RuntimeError(f"QueryInterface(IPersistFile) 失败: HRESULT {hex(hr)}")

            pf_vtable = ctypes.cast(p_persist_file, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents

            # IPersistFile::Load (index 5)
            load_type = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, wintypes.LPCWSTR, wintypes.DWORD)
            hr = load_type(pf_vtable[5])(p_persist_file, lnk_path, 0)
            if hr != 0:
                raise RuntimeError(f"IPersistFile::Load 失败: HRESULT {hex(hr)}")

            buf_path = ctypes.create_unicode_buffer(520)
            buf_args = ctypes.create_unicode_buffer(2048)
            buf_wdir = ctypes.create_unicode_buffer(520)

            # IShellLinkW: GetPath (3), GetWorkingDirectory (8)
            get_path_type = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, wintypes.LPWSTR, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)
            get_wdir_type = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, wintypes.LPWSTR, ctypes.c_int)

            hr_path = get_path_type(vtable[3])(p_shell_link, buf_path, 520, None, 0)
            hr_args = cls._get_shell_link_arguments(p_shell_link, vtable, buf_args)
            hr_wdir = get_wdir_type(vtable[8])(p_shell_link, buf_wdir, 520)

            if hr_path != 0:
                raise RuntimeError(f"IShellLinkW::GetPath 失败: HRESULT {hex(hr_path)}")

            if hr_args != 0:
                # 安全策略：快捷方式必须证明不带参数。若无法可靠读取参数，必须默认拒绝
                raise RuntimeError(f"IShellLinkW::GetArguments 失败: HRESULT {hex(hr_args)}")

            return ShortcutInfo(
                shortcut_path=lnk_path,
                target_path=buf_path.value.strip(),
                arguments=buf_args.value.strip(),
                working_directory=buf_wdir.value.strip() if hr_wdir == 0 else "",
                is_resolved=True
            )
        finally:
            if p_persist_file.value:
                try:
                    pf_vtable = ctypes.cast(p_persist_file, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
                    release_type = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)
                    release_type(pf_vtable[2])(p_persist_file)
                except Exception:
                    pass
            if p_shell_link.value:
                try:
                    vtable = ctypes.cast(p_shell_link, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
                    release_type = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)
                    release_type(vtable[2])(p_shell_link)
                except Exception:
                    pass
            if co_initialized:
                try:
                    ole32.CoUninitialize()
                except Exception:
                    pass

    @classmethod
    def validate(cls, lnk_path: str, info: Optional[ShortcutInfo] = None) -> ShortcutValidationResult:
        """
        统一快捷方式安全策略校验。
        所有快捷方式授权与启动前必须满足：
        1. 解析成功；
        2. 目标路径存在且非目录；
        3. 目标必须是 .exe 文件；
        4. 不包含任何非空启动参数；
        5. 目标不是 URL 或 Shell 命名空间；
        6. 目标文件名不得在受限制系统程序黑名单中。
        对外返回脱敏的安全分类原因，严禁泄露原始参数和用户物理路径。
        """
        if info is None:
            info = cls.resolve(lnk_path)

        if not info.is_resolved:
            return ShortcutValidationResult(
                is_allowed=False,
                block_reason="快捷方式无法解析",
                info=info
            )

        target = info.target_path.strip().strip('"').strip("'")
        if not target:
            return ShortcutValidationResult(
                is_allowed=False,
                block_reason="快捷方式目标不存在",
                info=info
            )

        # 检查是否为 URL 或 Shell 命名空间对象
        if "://" in target or target.startswith("::{"):
            return ShortcutValidationResult(
                is_allowed=False,
                block_reason="快捷方式目标类型不受支持",
                info=info
            )

        # 危险目标黑名单检查（无论目标是否存在，高危目标必须优先拦截）
        is_dang, dang_reason = is_dangerous_system_program(target)
        if is_dang:
            return ShortcutValidationResult(
                is_allowed=False,
                block_reason=dang_reason or "快捷方式指向受限制的高危系统程序，禁止授权和启动",
                info=info
            )

        # 参数检查：针对剪映 (JianyingPro.exe) 允许严格的固定参数白名单 (--src\d{1,3})
        # 绝不允许包含其他参数、引号、URL、重定向或脚本；其他程序一律严格禁止任何参数
        if info.arguments and info.arguments.strip():
            raw_args = info.arguments.strip()
            import re
            target_name = os.path.basename(target).lower()
            is_jianying = target_name == "jianyingpro.exe"
            if is_jianying and bool(re.fullmatch(r"^--src\d{1,3}$", raw_args)):
                pass  # 允许剪映安全启动参数通过
            else:
                return ShortcutValidationResult(
                    is_allowed=False,
                    block_reason="快捷方式包含启动参数，为避免参数注入，禁止授权和启动",
                    info=info
                )


        # 目标必须是 .exe
        ext = os.path.splitext(target)[1].lower()
        if ext != ".exe":
            return ShortcutValidationResult(
                is_allowed=False,
                block_reason="快捷方式目标类型不受支持，仅支持普通可执行文件",
                info=info
            )

        # 检查物理存在性
        if not os.path.exists(target):
            return ShortcutValidationResult(
                is_allowed=False,
                block_reason="快捷方式目标不存在或已被移除",
                info=info
            )

        # 目标不能是目录
        if os.path.isdir(target):
            return ShortcutValidationResult(
                is_allowed=False,
                block_reason="快捷方式目标类型不受支持，不能指向文件夹目录",
                info=info
            )

        return ShortcutValidationResult(
            is_allowed=True,
            block_reason="",
            info=info
        )
