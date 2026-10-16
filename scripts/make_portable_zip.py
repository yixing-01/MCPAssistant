"""将打包好的 dist/MCPAssistant 目录打包为绿色版 ZIP。

保证解压后只有一个顶层目录: MCPAssistant/
解压后不需要安装 Python，直接双击运行 MCPAssistant.exe。
"""

from __future__ import annotations

import os
import sys
import zipfile
from pathlib import Path


def make_portable_zip(dist_dir: str | Path) -> Path:
    dist_path = Path(dist_dir).resolve()
    src_dir = dist_path / "MCPAssistant"
    target_zip = dist_path / "MCP助手-v1.0.0-win-x64-portable.zip"

    if not src_dir.is_dir():
        raise FileNotFoundError(f"未找到待打包的主程序目录: {src_dir}")

    main_exe = src_dir / "MCPAssistant.exe"
    if not main_exe.is_file():
        raise FileNotFoundError(f"未在主程序目录中找到主可执行文件: {main_exe}")

    if target_zip.exists():
        target_zip.unlink()

    print(f"正在打包绿色版 ZIP: {target_zip} ...")
    with zipfile.ZipFile(target_zip, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for root, dirs, files in os.walk(src_dir):
            for file in files:
                full_path = Path(root) / file
                # 相对路径必须以 MCPAssistant/... 开头
                rel_path = full_path.relative_to(dist_path).as_posix()
                zf.write(full_path, rel_path)

    print(f"绿色版 ZIP 生成完成: {target_zip} ({target_zip.stat().st_size} 字节)")
    return target_zip


if __name__ == "__main__":
    project_root = Path(__file__).resolve().parent.parent
    dist = project_root / "dist"
    make_portable_zip(dist)
