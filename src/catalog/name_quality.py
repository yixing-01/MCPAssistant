"""软件显示名称质量评估与通用来源优先级机制。

用于解决跨扫描来源（例如 App Paths 注册表先发现英文 stem，开始菜单后发现本地化中文名称）
导致展示内部名称的问题，严禁针对单个软件编写特例映射。
"""

from __future__ import annotations

import os
from typing import List, Optional, Sequence


def get_source_rank(source: str) -> int:
    """返回扫描来源的基础质量等级（数值越大优先级越高）。

    优先级：
    1. 用户手动添加、手动管理 (50)
    2. 桌面或开始菜单本地化快捷方式 (40)
    3. Windows AppsFolder、安装注册表 DisplayName (30)
    4. EXE 元数据 (FileDescription / ProductName) (20)
    5. App Paths 键名、PATH 环境变量扫描、EXE 文件 stem (10)
    """
    s = (source or "").strip().lower()
    if not s:
        return 10
    if "manual" in s or "user" in s:
        return 50
    if any(k in s for k in ("startmenu", "start_menu", "desktop", "shortcut")):
        return 40
    if any(k in s for k in ("registry", "appsfolder", "uninstall")):
        return 30
    if any(k in s for k in ("metadata", "versioninfo", "description")):
        return 20
    if any(k in s for k in ("apppaths", "app_paths", "path", "stem", "builtin")):
        return 10
    return 15


def has_cjk(text: str) -> bool:
    """检查字符串是否包含中文字符（本地化核心特征之一）。"""
    return any("\u4e00" <= char <= "\u9fff" for char in text)


def extract_exe_stem(path: str) -> str:
    """提取可执行文件无扩展名的小写主干名称。"""
    if not path:
        return ""
    clean = path.strip().strip('"').strip("'")
    base = os.path.basename(clean)
    stem, _ = os.path.splitext(base)
    return stem.lower()


def is_low_quality_stem_name(name: str, exe_stem: str = "") -> bool:
    """保守判断名称是否属于明显的底层内部可执行文件标识或低质量 stem。"""
    if not name or not isinstance(name, str):
        return True
    n = name.strip()
    if not n:
        return True
    # 1. 直接等于 exe 文件主干（如 cloudmusic == cloudmusic）
    if exe_stem and n.casefold() == exe_stem.casefold():
        return True
    # 2. 带有 .exe / .lnk 等扩展名
    if n.lower().endswith((".exe", ".lnk", ".bat", ".cmd")):
        return True
    # 3. 全小写且无空格纯英文代号（例如 cloudmusic, wechat, myapp）
    if n.islower() and " " not in n and not has_cjk(n):
        return True
    return False


def calculate_name_quality(name: str, source: str = "", exe_stem: str = "") -> int:
    """计算单个候选名称的综合质量评分。"""
    n = (name or "").strip()
    if not n:
        return -100

    score = get_source_rank(source) * 10

    # 扣分项：完全等于 exe stem（例如 cloudmusic, msedge, notepad）
    if exe_stem and n.casefold() == exe_stem.casefold():
        score -= 25

    # 扣分项：带有可执行文件扩展名
    if n.lower().endswith((".exe", ".lnk")):
        score -= 30

    # 扣分项：全小写无空格纯字母数字标识（如 cloudmusic, wechat, myapp）
    if n.islower() and " " not in n and not has_cjk(n):
        score -= 5

    # 加分项：包含中文字符（本地化友好特征）
    if has_cjk(n):
        score += 20
    # 加分项：包含自然空格或规范大小写驼峰（人类可读语言特征）
    elif " " in n or any(c.isupper() for c in n):
        score += 8

    return score


def should_upgrade_display_name(
    current_name: str,
    current_source: str,
    new_name: str,
    new_source: str,
    exe_stem: str = "",
    is_user_managed: bool = False,
) -> bool:
    """保守比较当前名称与新候选名称，决定是否应该升级 display_name。

    原则：
    1. 用户手动管理条目不可自动覆盖；
    2. 只有当前名称属于低质量名称或弱来源(<=20)，且新名称评分严格更高时才升级；
    3. 已经确立的正式、高质量名称（如注册表 Uninstall、正式快捷方式）不被随意替换；
    4. 相同规范化名称不重复升级（仅在大小写更规范时优化）。
    """
    if is_user_managed:
        return False

    clean_new = (new_name or "").strip()
    clean_cur = (current_name or "").strip()
    if not clean_new:
        return False
    if not clean_cur:
        return True

    # 若内容忽略大小写后完全相同，但新名称有更好的首字母大写或驼峰形式且来源不低于当前，可允许微调
    if clean_new.casefold() == clean_cur.casefold():
        if clean_new != clean_cur and clean_new[0].isupper() and clean_cur.islower():
            return get_source_rank(new_source) >= get_source_rank(current_source)
        return False

    # 只有当当前名称属于低质量名称，或者当前来源属于弱底层来源(<= 20)时，才允许升级替换
    cur_is_low_quality = is_low_quality_stem_name(clean_cur, exe_stem) or get_source_rank(current_source) <= 20
    if not cur_is_low_quality:
        return False

    score_cur = calculate_name_quality(clean_cur, current_source, exe_stem)
    score_new = calculate_name_quality(clean_new, new_source, exe_stem)

    return score_new > score_cur


def promote_alias_order(aliases: Sequence[str], primary_name: str) -> List[str]:
    """组织别名列表：将主显示名称排在首位，原别名去重保留。"""
    seen = set()
    result: List[str] = []

    clean_primary = primary_name.strip()
    if clean_primary:
        seen.add(clean_primary.casefold())
        result.append(clean_primary)

    for item in aliases:
        clean = str(item or "").strip()
        if clean and clean.casefold() not in seen:
            seen.add(clean.casefold())
            result.append(clean)

    return result
