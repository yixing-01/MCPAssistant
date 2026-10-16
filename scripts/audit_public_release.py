"""公开发布与源码合规审计脚本。

静态扫描准备公开发布的文件树及整理好的发布目录，严格执行以下安全与合规审计：
1. 必需公开合规材料完整性（LICENSE, NO-SALE-ADDENDUM.md, NOTICE.md 等 11 项）；
2. 彻底拒绝任何面向用户的旧版 Commons Clause 字符串与旧许可标识；
3. 扫描整个公开源码树（源码、测试、脚本、文档、配置、构建文件），杜绝开发机私有绝对路径；
4. 真实 Token、秘钥、密码、Cookie 与敏感凭据扫描（安全占位符豁免）；
5. 敏感内部文件（AGENTS.md, AI_HANDOFF.md, .env, *.log, *.pyc, 内部截图与脚本等）全面阻断；
6. 源码仓库白名单与目录结构严格核验；
7. 独立发布目录（MCP助手-GitHub发布）结构、中文成品、大小、SHA256SUMS 及绿色包内容深度审计；
8. 发现任何违规立即报错失败（零容忍阻断）。
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
import zipfile
from pathlib import Path
from typing import Dict, List, Set, Tuple

# 1. 必需公开材料清单 (根目录)
REQUIRED_PUBLIC_DOCS = [
    "LICENSE",
    "NO-SALE-ADDENDUM.md",
    "NOTICE.md",
    "THIRD_PARTY_NOTICES.md",
    "QT_LGPL_COMPLIANCE.md",
    "PROVENANCE.md",
    "PRIVACY.md",
    "SECURITY.md",
    "CONTRIBUTING.md",
    "CHANGELOG.md",
    "README.md",
]

# 2. 源码仓库根目录允许的根文件白名单
ALLOWED_REPO_ROOT_FILES = {
    ".gitignore",
    "README.md",
    "LICENSE",
    "NO-SALE-ADDENDUM.md",
    "NOTICE.md",
    "THIRD_PARTY_NOTICES.md",
    "QT_LGPL_COMPLIANCE.md",
    "PROVENANCE.md",
    "PRIVACY.md",
    "SECURITY.md",
    "CONTRIBUTING.md",
    "CHANGELOG.md",
    "requirements.txt",
    "requirements-dev.txt",
    "pytest.ini",
    "build.bat",
    "build.ps1",
    "MCPAssistant.spec",
}

# 3. 源码仓库允许的顶级目录
ALLOWED_REPO_DIRS = {
    "src",
    "tests",
    "installer",
    "licenses",
    "docs",
    "scripts",
}

# 4. docs 目录允许的文件白名单
ALLOWED_DOCS_FILES = {
    "architecture.md",
    "releasing.md",
}

# 5. scripts 目录允许的文件白名单
ALLOWED_SCRIPTS_FILES = {
    "audit_public_release.py",
    "make_portable_zip.py",
    "prepare_release.ps1",
    "prepare_github_publication.ps1",
}

# 6. 严禁出现的旧许可证关键词与标识 (发现即失败)
FORBIDDEN_LEGACY_LICENSE_TERMS = [
    "Commons Clause",
    "COMMONS-CLAUSE.md",
    "COMMONS-CLAUSE",
    "LicenseRef-Apache-2.0-With-Commons-Clause-1.0",
]

# 7. 严禁在公开源码树中出现的敏感/内部/临时文件模式
FORBIDDEN_RELEASE_PATTERNS = [
    r"^\.env(\..+)?$",
    r"^secrets\.dat$",
    r"^config\.json$",
    r"^catalog\.json$",
    r".*\.log$",
    r".*\.pyc$",
    r".*\.pyo$",
    r"^__pycache__$",
    r"^\.pytest_cache$",
    r"^\.coverage$",
    r"^source-backups$",
    r"^dist-backups$",
    r"^\.superpowers$",
    r"^AGENTS\.md$",
    r"^GEMINI\.md$",
    r"^AI_HANDOFF\.md$",
    r"^analysis\.md$",
    r"^superpowers$",
    r"^ui-acceptance$",
    r"^tmp_.*$",
    r".*\.tmp$",
    r".*\.bak$",
    r"^run_three_rounds\.ps1$",
    r"^tmp_check\.ps1$",
]

# 8. 严禁在源码或对外资源中出现的旧品牌关键词
FORBIDDEN_BRAND_TERMS = [
    "小智助手",
    "小智 AI MCP 操控助手",
    "小智 MCP 应用管理器",
    "小智 AI",
]

# 9. 本地私有绝对路径特征
FORBIDDEN_PATH_PATTERNS = [
    re.compile(r"D:[\\/]+codex", re.IGNORECASE),
    re.compile(r"C:[\\/]+Users[\\/]+Li\b", re.IGNORECASE),
    re.compile(r"C:[\\/]+xiaozhi", re.IGNORECASE),
]

# 10. 真实 Token 与秘钥泄露检测
TOKEN_REGEX = re.compile(r"token=([a-zA-Z0-9_\-]{24,})", re.IGNORECASE)
PRIVATE_KEY_REGEX = re.compile(r"-----BEGIN\s+(?:RSA\s+)?PRIVATE\s+KEY-----", re.IGNORECASE)
SAFE_SECRET_PATTERNS = [
    "SECRET_TEST_TOKEN",
    "test",
    "dummy",
    "placeholder",
    "example",
    "mock",
    "your_token",
    "token=...",
    "token_here",
    "auto",
    "migration",
]

# 允许的文本文件后缀
TEXT_EXTENSIONS = {
    ".py",
    ".md",
    ".txt",
    ".iss",
    ".bat",
    ".ps1",
    ".spec",
    ".ini",
    ".json",
    ".qss",
    ".manifest",
    ".yml",
    ".yaml",
    ".gitignore",
    ".svg",
}

# 允许的已知安全二进制文件白名单 (相对路径)
ALLOWED_BINARY_PATHS = {
    "src/ui_qt/resources/branding/mcp_assistant.ico",
    "src/ui_qt/resources/branding/mcp_brand_full.png",
    "src/ui_qt/resources/branding/mcp_brand_master.png",
    "src/ui_qt/resources/branding/mcp_brand_symbol.png",
    "src/ui_qt/resources/branding/xiaozhi_mcp.ico",
}


def check_required_docs(root: Path) -> List[str]:
    """检查 11 项必需公开材料是否存在且内容有效。"""
    errors: List[str] = []
    for doc in REQUIRED_PUBLIC_DOCS:
        path = root / doc
        if not path.is_file():
            errors.append(f"缺失必需公开合规文档: {doc}")
            continue
        content = path.read_text(encoding="utf-8", errors="replace").strip()
        if len(content) < 50:
            errors.append(f"合规文档内容过短或为空: {doc}")

    # 特别检查许可证关键标识
    notice_path = root / "NOTICE.md"
    if notice_path.is_file():
        text = notice_path.read_text(encoding="utf-8", errors="replace")
        if "Copyright 2026 定义" not in text:
            errors.append("NOTICE.md 缺少正确的版权声明: Copyright 2026 定义")
        if "LicenseRef-MCPAssistant-No-Sale-1.0" not in text:
            errors.append("NOTICE.md 缺少规范的新许可证标识: LicenseRef-MCPAssistant-No-Sale-1.0")
        if "NO-SALE-ADDENDUM.md" not in text:
            errors.append("NOTICE.md 必须明确引用 NO-SALE-ADDENDUM.md 文件")
        if "OSI" not in text:
            errors.append("NOTICE.md 必须明确指出此许可不是 OSI 批准的开源许可证")

    # 检查 COMMONS-CLAUSE.md 绝不能存在
    if (root / "COMMONS-CLAUSE.md").exists():
        errors.append("公开目录中严禁存在 COMMONS-CLAUSE.md 文件")

    return errors


def check_gitignore_rules(root: Path) -> List[str]:
    """验证 .gitignore 完整排除了内部协作文件、构建目录及敏感凭据。"""
    errors: List[str] = []
    gitignore = root / ".gitignore"
    if not gitignore.is_file():
        errors.append("根目录缺少 .gitignore 文件")
        return errors

    content = gitignore.read_text(encoding="utf-8", errors="replace")
    required_ignores = [
        "AGENTS.md",
        "GEMINI.md",
        "AI_HANDOFF.md",
        "source-backups",
        "dist-backups",
        ".superpowers",
        "secrets.dat",
        "config.json",
        ".env",
    ]
    for req in required_ignores:
        if req not in content:
            errors.append(f".gitignore 缺少对敏感或内部项的忽略规则: {req}")

    return errors


def check_public_tree_content(root: Path, is_source_repo: bool = False) -> List[str]:
    """全树扫描所有公开文本文件，检查旧许可证残留、私人路径、品牌残留与真实 Secret。"""
    errors: List[str] = []

    # 扫描跳过目录（仅在工作区模式下忽略构建、环境与本地测试缓存）
    skip_dirs = {
        ".venv",
        ".venv-build",
        "build",
        "dist",
        "dist-backups",
        "source-backups",
        ".git",
        "__pycache__",
        ".pytest_cache",
        "ui-acceptance",
        "superpowers",
        ".superpowers",
    }

    # 工作区模式下内部专属文件/脚本（不拟公开发布）
    internal_workspace_prefixes = (
        "docs/ui-acceptance",
        "docs/superpowers",
        "docs/analysis.md",
        "docs/AI_HANDOFF.md",
        "scripts/capture_",
        "scripts/check_scaling.py",
        "scripts/verify_",
        "scripts/generate_branding_assets.py",
        "run_three_rounds.ps1",
        "tmp_check.ps1",
        "AGENTS.md",
        "GEMINI.md",
    )

    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = Path(dirpath).relative_to(root)
        
        # 排除工作区运行态与缓存目录
        if not is_source_repo:
            dirnames[:] = [d for d in dirnames if d not in skip_dirs]
            if any(p in rel_dir.parts for p in skip_dirs):
                continue

        for fname in filenames:
            fpath = Path(dirpath) / fname
            rel_file = fpath.relative_to(root)
            norm_rel_str = rel_file.as_posix()

            # 在本地工作区模式下，跳过内部开发脚本与内部文档
            if not is_source_repo:
                if norm_rel_str in internal_workspace_prefixes or any(
                    norm_rel_str.startswith(p) for p in internal_workspace_prefixes
                ):
                    continue

            # 检查二进制文件
            ext = fpath.suffix.lower()
            if ext not in TEXT_EXTENSIONS and fname not in {".gitignore", "LICENSE"}:
                if norm_rel_str not in ALLOWED_BINARY_PATHS:
                    # 检查是否在 licenses 目录下的普通文本
                    if not norm_rel_str.startswith("licenses/"):
                        errors.append(f"发现未在白名单中的未知或二进制文件: {norm_rel_str}")
                continue

            try:
                text = fpath.read_text(encoding="utf-8", errors="replace")
            except Exception as e:
                errors.append(f"无法读取文件 {norm_rel_str}: {e}")
                continue

            # 1. 自审计文件豁免自身检测代码
            is_self_audit = norm_rel_str in {
                "scripts/audit_public_release.py",
                "tests/test_audit_public_release.py",
            }
            if not is_self_audit:
                # 旧许可证术语检查
                for term in FORBIDDEN_LEGACY_LICENSE_TERMS:
                    if term in text:
                        errors.append(f"文件 {norm_rel_str} 残留旧许可证术语: '{term}'")

                # 品牌残留检查
                for term in FORBIDDEN_BRAND_TERMS:
                    if term in text:
                        errors.append(f"文件 {norm_rel_str} 包含违规旧品牌名称: '{term}'")

                # 开发机私有绝对路径检查 (逐行检查并排除注释中的安全说明)
                for line_no, line in enumerate(text.splitlines(), start=1):
                    for pat in FORBIDDEN_PATH_PATTERNS:
                        m = pat.findall(line)
                        if m:
                            errors.append(f"文件 {norm_rel_str}:{line_no} 发现开发机私有绝对路径特征: {m[0]}")
                            break

            # 2. 私钥与真实 Token 泄露检查
            if PRIVATE_KEY_REGEX.search(text):
                errors.append(f"文件 {norm_rel_str} 疑似包含硬编码私钥数据！")

            is_test_file = norm_rel_str.startswith("tests/")
            for m in TOKEN_REGEX.finditer(text):
                token_val = m.group(1)
                is_safe = any(p.lower() in token_val.lower() for p in SAFE_SECRET_PATTERNS)
                # 测试文件中的合成 mock token 允许通过
                if is_test_file and (
                    is_safe
                    or token_val.startswith("SECRET_")
                    or token_val.startswith("test_")
                    or token_val.startswith("autotest_")
                    or token_val.startswith("NEW_")
                    or token_val.startswith("mock_")
                    or "abc1234567890" in token_val  # 专项测试合成占位
                ):
                    continue
                if not is_safe:
                    errors.append(f"文件 {norm_rel_str} 疑似包含硬编码真实 Token: token={token_val[:4]}...{token_val[-4:]}")

    return errors


def check_source_tree_content(root: Path) -> List[str]:
    """向后兼容的源码检查接口。"""
    return check_public_tree_content(root, is_source_repo=False)


def check_tree_cleanliness(target_dir: Path) -> List[str]:
    """检查独立待发布源码树中是否存在严禁包含的文件。"""
    errors: List[str] = []
    compiled_patterns = [re.compile(p, re.IGNORECASE) for p in FORBIDDEN_RELEASE_PATTERNS]

    for item in target_dir.rglob("*"):
        name = item.name
        rel_posix = item.relative_to(target_dir).as_posix()
        for pat in compiled_patterns:
            if pat.match(name) or pat.match(rel_posix):
                errors.append(f"发布树中包含被禁止的敏感或内部项: {rel_posix}")
                break

    return errors


def check_source_repo_whitelist(repo_dir: Path) -> List[str]:
    """检查待上传的“源码仓库”目录是否严格符合允许列表。"""
    errors: List[str] = []
    if not repo_dir.is_dir():
        errors.append(f"源码仓库目录不存在: {repo_dir}")
        return errors

    # 1. 检查根目录项
    for child in repo_dir.iterdir():
        name = child.name
        if child.is_file():
            if name not in ALLOWED_REPO_ROOT_FILES:
                errors.append(f"源码仓库根目录包含未在白名单中的多余文件: {name}")
        elif child.is_dir():
            if name not in ALLOWED_REPO_DIRS:
                errors.append(f"源码仓库包含未在白名单中的多余目录: {name}")
            elif name == "docs":
                for doc_child in child.rglob("*"):
                    if doc_child.is_file():
                        rel_doc = doc_child.relative_to(child).as_posix()
                        if rel_doc not in ALLOWED_DOCS_FILES:
                            errors.append(f"docs 目录包含未被白名单允许的非公开文件: {rel_doc}")
            elif name == "scripts":
                for script_child in child.rglob("*"):
                    if script_child.is_file():
                        rel_script = script_child.relative_to(child).as_posix()
                        if rel_script not in ALLOWED_SCRIPTS_FILES:
                            errors.append(f"scripts 目录包含未被白名单允许的非公开脚本: {rel_script}")

    # 2. 检查安装程序配置
    setup_iss = repo_dir / "installer" / "setup.iss"
    if setup_iss.is_file():
        iss_text = setup_iss.read_text(encoding="utf-8", errors="replace")
        if "COMMONS-CLAUSE.md" in iss_text:
            errors.append("installer/setup.iss 仍然包含旧 COMMONS-CLAUSE.md 引用")
        if "LicenseFile=LICENSE_FOR_SETUP.txt" not in iss_text:
            errors.append("installer/setup.iss 未正确配置新许可证页面 (LicenseFile=LICENSE_FOR_SETUP.txt)")

    return errors


def check_release_directory_structure(release_dir: Path, min_file_size: int = 5 * 1024 * 1024) -> List[str]:
    """深度检查独立的发布整理总目录（MCP助手-GitHub发布）。"""
    errors: List[str] = []
    release_dir = release_dir.resolve()

    if not release_dir.is_dir():
        errors.append(f"发布总目录不存在: {release_dir}")
        return errors

    # 必需的顶层产物与文件
    expected_top_items = {
        "源码仓库",
        "MCP助手-v1.0.0-绿色免安装版.zip",
        "MCP助手-v1.0.0-安装版.exe",
        "SHA256SUMS.txt",
        "发布说明.md",
    }

    actual_top_items = {c.name for c in release_dir.iterdir()}
    missing_items = expected_top_items - actual_top_items
    extra_items = actual_top_items - expected_top_items

    for m in missing_items:
        errors.append(f"发布总目录缺少必要产物或文件: {m}")
    for x in extra_items:
        errors.append(f"发布总目录包含未定义的额外项: {x}")

    # 1. 检查两个成品不是空文件或异常小文件 (默认 >= 5MB)
    portable_zip = release_dir / "MCP助手-v1.0.0-绿色免安装版.zip"
    setup_exe = release_dir / "MCP助手-v1.0.0-安装版.exe"

    if portable_zip.is_file():
        zsize = portable_zip.stat().st_size
        if zsize < min_file_size:
            errors.append(f"绿色免安装版 ZIP 大小异常 ({zsize} 字节)，疑似打包损坏")
        else:
            # 解压检查绿色版 ZIP 内容
            try:
                with zipfile.ZipFile(portable_zip, "r") as zf:
                    namelist = zf.namelist()
                    # 检查是否有顶层单目录 MCPAssistant/
                    has_main_exe = any(n == "MCPAssistant/MCPAssistant.exe" for n in namelist)
                    has_no_sale = any(n == "MCPAssistant/NO-SALE-ADDENDUM.md" for n in namelist)
                    has_old_commons = any("COMMONS-CLAUSE" in n for n in namelist)
                    
                    if not has_main_exe:
                        errors.append("绿色版 ZIP 内缺少 MCPAssistant/MCPAssistant.exe 主程序")
                    if not has_no_sale:
                        errors.append("绿色版 ZIP 内缺少新许可证文件 MCPAssistant/NO-SALE-ADDENDUM.md")
                    if has_old_commons:
                        errors.append("绿色版 ZIP 内依然残留旧版 COMMONS-CLAUSE.md 文件！")
            except Exception as e:
                errors.append(f"无法读取解析绿色版 ZIP 文件: {e}")

    if setup_exe.is_file():
        ssize = setup_exe.stat().st_size
        if ssize < min_file_size:
            errors.append(f"一键安装版 EXE 大小异常 ({ssize} 字节)，疑似编译损坏")

    # 2. 检查并核对 SHA256SUMS.txt
    sums_file = release_dir / "SHA256SUMS.txt"
    if sums_file.is_file():
        sums_text = sums_file.read_text(encoding="utf-8-sig", errors="replace")
        sum_dict: Dict[str, str] = {}
        for line in sums_text.splitlines():
            line = line.strip().lstrip("\ufeff")
            if not line:
                continue
            parts = line.split(None, 1)
            if len(parts) == 2:
                h, fn = parts[0].strip().lstrip("\ufeff"), parts[1].strip().lstrip("*")
                sum_dict[fn] = h.upper()

        for target_file, expected_name in [
            (portable_zip, "MCP助手-v1.0.0-绿色免安装版.zip"),
            (setup_exe, "MCP助手-v1.0.0-安装版.exe"),
        ]:
            if target_file.is_file():
                actual_hash = hashlib.sha256(target_file.read_bytes()).hexdigest().upper()
                if expected_name not in sum_dict:
                    errors.append(f"SHA256SUMS.txt 中缺少条目: {expected_name}")
                elif sum_dict[expected_name] != actual_hash:
                    errors.append(
                        f"成品文件 {expected_name} SHA256 哈希不匹配！(清单: {sum_dict[expected_name]}, 实际: {actual_hash})"
                    )

    # 3. 检查发布说明.md 必备要点
    release_notes = release_dir / "发布说明.md"
    if release_notes.is_file():
        notes_text = release_notes.read_text(encoding="utf-8", errors="replace")
        required_topics = [
            "绿色",
            "安装",
            "SHA256",
            "商业",
            "禁止销售",
            "GitHub",
        ]
        for topic in required_topics:
            if topic not in notes_text:
                errors.append(f"发布说明.md 缺少关于 '{topic}' 的必要说明内容")

    # 4. 检查源码仓库子目录
    src_repo = release_dir / "源码仓库"
    if src_repo.is_dir():
        errors.extend(check_source_repo_whitelist(src_repo))
        errors.extend(check_required_docs(src_repo))
        errors.extend(check_tree_cleanliness(src_repo))
        errors.extend(check_public_tree_content(src_repo, is_source_repo=True))

    return errors


def run_audit(root_dir: str | Path, is_standalone_release_tree: bool = False, is_full_distribution_dir: bool = False) -> Tuple[bool, List[str]]:
    """执行公开发布合规审计流程。"""
    root = Path(root_dir).resolve()
    all_errors: List[str] = []

    if is_full_distribution_dir:
        # 审计完整的发布整理目录 (含 源码仓库、两个成品、SHA256SUMS.txt、发布说明.md)
        all_errors.extend(check_release_directory_structure(root))
    else:
        # 审计源码仓库或当前工作区
        all_errors.extend(check_required_docs(root))
        if is_standalone_release_tree:
            all_errors.extend(check_tree_cleanliness(root))
            all_errors.extend(check_source_repo_whitelist(root))
            all_errors.extend(check_public_tree_content(root, is_source_repo=True))
        else:
            all_errors.extend(check_gitignore_rules(root))
            all_errors.extend(check_public_tree_content(root, is_source_repo=False))

    passed = len(all_errors) == 0
    return passed, all_errors


def main() -> int:
    parser = argparse.ArgumentParser(description="MCP助手公开发布合规审计工具")
    parser.add_argument("--dir", default=".", help="待审计的项目根目录或源码包目录")
    parser.add_argument(
        "--release-tree",
        nargs="?",
        const="",
        default=None,
        help="指定为独立发布目录模式（若提供目录路径则直接作为发布总目录审计，若不提供则审计 --dir）",
    )
    args = parser.parse_args()

    # 处理参数
    if args.release_tree is not None and args.release_tree != "":
        target_path = Path(args.release_tree).resolve()
        is_full_dist = True
        is_release_tree = True
    elif args.release_tree == "":
        target_path = Path(args.dir).resolve()
        is_full_dist = (target_path / "源码仓库").is_dir()
        is_release_tree = True
    else:
        target_path = Path(args.dir).resolve()
        is_full_dist = (target_path / "源码仓库").is_dir()
        is_release_tree = False

    print("=" * 60)
    print(f"正在进行公开发布合规审计: {target_path}")
    if is_full_dist:
        print("模式: 独立发布总目录深度审计 (源码仓库 + 双成品 + 校验和 + 发布说明)")
    elif is_release_tree:
        print("模式: 独立待公开源码仓库深度审计")
    else:
        print("模式: 本地工作区发布准备审计")
    print("=" * 60)

    passed, errors = run_audit(
        target_path,
        is_standalone_release_tree=is_release_tree,
        is_full_distribution_dir=is_full_dist,
    )

    if passed:
        print("\n[SUCCESS] 公开发布合规审计全部通过！未发现任何合规违规、敏感泄露或旧版许可残留。")
        return 0
    else:
        print(f"\n[FAILURE] 审计失败，发现 {len(errors)} 项违规：")
        for err in errors:
            print(f"  - {err}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
