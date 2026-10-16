"""公开发布审计脚本自动化测试套件。

验证 scripts/audit_public_release.py 的各项检测规则：
1. 本地当前项目审计验证；
2. 必需公开材料完整性（LICENSE, NO-SALE-ADDENDUM.md, NOTICE.md 等）；
3. NOTICE 规范要求拦截与新标识检测；
4. 旧 Commons Clause 字符串与标识绝对拦截；
5. 旧品牌名称残留拦截；
6. 硬编码私人路径拦截（全树扫描）；
7. 真实 Token 与秘钥泄露拦截；
8. 测试假令牌与占位值安全允许；
9. 独立发布树中的敏感文件拦截；
10. 发布整理目录结构、成品中文名及 SHA256 校验。
"""

from __future__ import annotations

import hashlib
import io
import shutil
import zipfile
from pathlib import Path

import pytest

from scripts.audit_public_release import (
    REQUIRED_PUBLIC_DOCS,
    check_gitignore_rules,
    check_public_tree_content,
    check_release_directory_structure,
    check_required_docs,
    check_source_tree_content,
    check_tree_cleanliness,
    run_audit,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def test_audit_current_project_passes():
    """当前开发工作区执行审计必须通过。"""
    passed, errors = run_audit(PROJECT_ROOT, is_standalone_release_tree=False)
    assert passed is True, f"当前项目未通过公开发布审计，错误项: {errors}"
    assert len(errors) == 0


def test_audit_detects_missing_required_docs(tmp_path: Path):
    """当缺失任一必需合规文档（包含 NO-SALE-ADDENDUM.md）时必须报警拦截。"""
    errors = check_required_docs(tmp_path)
    assert len(errors) >= len(REQUIRED_PUBLIC_DOCS)
    assert any("LICENSE" in e for e in errors)
    assert any("NO-SALE-ADDENDUM.md" in e for e in errors)


def test_audit_detects_invalid_notice(tmp_path: Path):
    """NOTICE.md 缺少必要版权信息或未采用新版 No-Sale 许可证标识时必须报错。"""
    notice = tmp_path / "NOTICE.md"
    notice.write_text("Copyright 2025 SomeoneElse\nSome description", encoding="utf-8")
    errors = check_required_docs(tmp_path)
    assert any("Copyright 2026 定义" in e for e in errors)
    assert any("LicenseRef-MCPAssistant-No-Sale-1.0" in e for e in errors)
    assert any("NO-SALE-ADDENDUM.md" in e for e in errors)


def test_audit_rejects_legacy_commons_clause_terms(tmp_path: Path):
    """任何文件中残留旧 Commons Clause 字符串或旧标识必须直接报错拦截。"""
    doc = tmp_path / "README.md"
    doc.write_text("This project uses Commons Clause 1.0", encoding="utf-8")
    errors = check_public_tree_content(tmp_path)
    assert any("Commons Clause" in e for e in errors)

    doc2 = tmp_path / "NOTICE.md"
    doc2.write_text("LicenseRef-Apache-2.0-With-Commons-Clause-1.0", encoding="utf-8")
    errors2 = check_public_tree_content(tmp_path)
    assert any("Commons Clause" in e or "LicenseRef" in e for e in errors2)


def test_audit_detects_forbidden_brand_terms(tmp_path: Path):
    """源码中若包含旧品牌词“小智助手”必须准确定位拦截。"""
    src_dir = tmp_path / "src"
    src_dir.mkdir(parents=True)
    bad_file = src_dir / "bad.py"
    bad_file.write_text("title = '小智助手欢迎您'", encoding="utf-8")

    errors = check_source_tree_content(tmp_path)
    assert any("bad.py" in e and "小智助手" in e for e in errors)


def test_audit_detects_hardcoded_paths(tmp_path: Path):
    """源码或文档中若包含开发机硬编码绝对路径（如 D:\\codex, C:\\Users\\Li 等）必须报警。"""
    src_dir = tmp_path / "src"
    src_dir.mkdir(parents=True)
    bad_file = src_dir / "leak_path.py"
    bad_file.write_text('TEST_DIR = "D:\\\\codex\\\\MCP_reverse\\\\app"', encoding="utf-8")

    errors = check_public_tree_content(tmp_path)
    assert any("leak_path.py" in e and "开发机私有绝对路径" in e for e in errors)

    doc_file = tmp_path / "README.md"
    doc_file.write_text('cd C:\\Users\\Li\\app', encoding="utf-8")
    errors_doc = check_public_tree_content(tmp_path)
    assert any("README.md" in e and "开发机私有绝对路径" in e for e in errors_doc)


def test_audit_detects_real_token_leak(tmp_path: Path):
    """源码或配置中若包含可疑的长真实 Token 必须报警。"""
    src_dir = tmp_path / "src"
    src_dir.mkdir(parents=True)
    bad_file = src_dir / "token_leak.py"
    bad_file.write_text('URL = "wss://api.xiaozhi.me/mcp/?token=abc1234567890abcdef1234567890"', encoding="utf-8")

    errors = check_public_tree_content(tmp_path)
    assert any("token_leak.py" in e and "硬编码真实 Token" in e for e in errors)


def test_audit_allows_safe_token_placeholders(tmp_path: Path):
    """源码或测试中使用安全测试 mock 占位符允许通过。"""
    src_dir = tmp_path / "src"
    src_dir.mkdir(parents=True)
    safe_file = src_dir / "safe.py"
    safe_file.write_text('URL = "wss://api.xiaozhi.me/mcp/?token=SECRET_TEST_TOKEN_123456"', encoding="utf-8")

    errors = check_public_tree_content(tmp_path)
    assert not any("safe.py" in e for e in errors)


def test_audit_detects_forbidden_files_in_release_tree(tmp_path: Path):
    """独立待发布树中混入 .env、内部交接文档等敏感文件时必须报错。"""
    (tmp_path / ".env").write_text("SECRET=123", encoding="utf-8")
    (tmp_path / "AI_HANDOFF.md").write_text("internal log", encoding="utf-8")
    (tmp_path / "test.log").write_text("some log", encoding="utf-8")

    errors = check_tree_cleanliness(tmp_path)
    assert len(errors) == 3
    assert any(".env" in e for e in errors)
    assert any("AI_HANDOFF.md" in e for e in errors)
    assert any("test.log" in e for e in errors)


def test_audit_release_directory_checks_structure_and_sha256(tmp_path: Path):
    """检查独立发布目录的完整结构、中文文件名、SHA256 校验和以及绿色包内许可证。"""
    release_dir = tmp_path / "MCP助手-GitHub发布"
    src_repo = release_dir / "源码仓库"
    src_repo.mkdir(parents=True)
    
    # 填充源码仓库必需公开文档
    for doc in REQUIRED_PUBLIC_DOCS:
        if doc == "NOTICE.md":
            (src_repo / doc).write_text(
                "Copyright 2026 定义\nLicenseRef-MCPAssistant-No-Sale-1.0\nNO-SALE-ADDENDUM.md\nThis is not OSI open source.\n",
                encoding="utf-8",
            )
        else:
            (src_repo / doc).write_text(f"# {doc}\n" + "valid compliant dummy text " * 10, encoding="utf-8")

    portable_zip = release_dir / "MCP助手-v1.0.0-绿色免安装版.zip"
    # 创建有效的 zip，内含 MCPAssistant/NO-SALE-ADDENDUM.md
    with zipfile.ZipFile(portable_zip, "w") as zf:
        zf.writestr("MCPAssistant/MCPAssistant.exe", b"dummy exe data" * 100)
        zf.writestr("MCPAssistant/NO-SALE-ADDENDUM.md", b"# No Sale Addendum")

    setup_exe = release_dir / "MCP助手-v1.0.0-安装版.exe"
    setup_exe.write_bytes(b"dummy setup exe content" * 100)

    # 计算真实哈希
    zip_hash = hashlib.sha256(portable_zip.read_bytes()).hexdigest().upper()
    setup_hash = hashlib.sha256(setup_exe.read_bytes()).hexdigest().upper()

    sums_file = release_dir / "SHA256SUMS.txt"
    sums_file.write_text(
        f"{zip_hash}  MCP助手-v1.0.0-绿色免安装版.zip\n{setup_hash}  MCP助手-v1.0.0-安装版.exe\n",
        encoding="utf-8",
    )

    release_notes = release_dir / "发布说明.md"
    release_notes.write_text(
        "# 发布说明\n## 安装版与绿色版区别\n## SHA256 校验说明\n## 许可证说明：允许商业使用，禁止销售软件副本\n## GitHub Release 与源码仓库上传规范\n",
        encoding="utf-8",
    )

    errors = check_release_directory_structure(release_dir, min_file_size=100)
    assert len(errors) == 0, f"有效的发布目录应无错误，但发现: {errors}"

    # 篡改哈希时应报错
    sums_file.write_text(f"0000000000000000000000000000000000000000000000000000000000000000  MCP助手-v1.0.0-绿色免安装版.zip\n", encoding="utf-8")
    errors_tampered = check_release_directory_structure(release_dir, min_file_size=100)
    assert any("哈希不匹配" in e or "SHA256" in e for e in errors_tampered)

