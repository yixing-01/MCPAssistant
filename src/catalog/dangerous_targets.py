"""集中式危险系统程序安全策略模块。

统一识别并阻止命令解释器、脚本宿主、PowerShell、注册表编辑器、计划任务工具、
进程终止工具、系统关闭工具、系统/磁盘/服务配置工具以及代理执行高危工具。
"""

import os
from typing import Set, Tuple

# 高危目标程序文件名集合（统一小写）
DANGEROUS_PROGRAM_FILENAMES: Set[str] = {
    # 1. 命令解释器与脚本宿主
    "cmd.exe",
    "powershell.exe",
    "pwsh.exe",
    "wscript.exe",
    "cscript.exe",
    "bash.exe",
    "sh.exe",
    "conhost.exe",
    "openconsole.exe",
    # 2. 注册表工具
    "reg.exe",
    "regedit.exe",
    "regedt32.exe",
    # 3. 计划任务管理工具
    "schtasks.exe",
    "at.exe",
    # 4. 进程终止与系统关闭工具
    "taskkill.exe",
    "tskill.exe",
    "shutdown.exe",
    # 5. 系统启动、磁盘与服务配置工具
    "msconfig.exe",
    "bcdedit.exe",
    "diskpart.exe",
    "sc.exe",
    "net.exe",
    "net1.exe",
    "format.com",
    "format.exe",
    "chkdsk.exe",
    "sfc.exe",
    "dism.exe",
    # 6. 常见代理执行与脚本滥用工具
    "mshta.exe",
    "rundll32.exe",
    "regsvr32.exe",
    "wmic.exe",
    "certutil.exe",
    "bitsadmin.exe",
    # 7. 系统维护与任务管理工具
    "control.exe",
    "taskmgr.exe",
}


def is_dangerous_system_program(target_path_or_name: str) -> Tuple[bool, str]:
    """统一检查目标程序是否属于高危系统工具。

    大小写不敏感，支持完整绝对路径、相对路径、带扩展名或不带扩展名的可执行文件名。
    返回 (is_dangerous, block_reason)。
    """
    if not target_path_or_name or not isinstance(target_path_or_name, str):
        return False, ""

    clean = target_path_or_name.strip().strip('"').strip("'")
    if not clean:
        return False, ""

    filename = os.path.basename(clean).lower()
    # 如果没有指定扩展名，补齐 .exe 统一比较
    if not os.path.splitext(filename)[1]:
        filename_with_ext = f"{filename}.exe"
    else:
        filename_with_ext = filename

    if filename in DANGEROUS_PROGRAM_FILENAMES or filename_with_ext in DANGEROUS_PROGRAM_FILENAMES:
        return True, f"高危系统程序 [{filename_with_ext}] 禁止作为可控软件授权与启动"

    return False, ""
