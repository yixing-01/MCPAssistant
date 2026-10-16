# 本地发布与打包指南 (Local Release Guide)

本文档说明 MCP助手（MCPAssistant）在本地环境中构建正式绿色版（Portable ZIP）及一键安装包（Setup EXE）的完整操作规范。

---

## 1. 发布构建环境要求

- **操作系统**: Windows 10 / 11 (x64)
- **Python 运行时**: `Python 3.13.11 final` 64-bit（使用项目内置的 `.venv-build` 稳定构建环境）
- **打包工具**: `PyInstaller 6.22.3`
- **安装包编译器**: `Inno Setup 7`（或 Inno Setup 6），例如：`D:\Program Files\Inno Setup 7\ISCC.exe`
- **依赖检查**: 运行 `pip check` 必须零报错
- **语法检查**: `python -m compileall src tests` 必须零错误

---

## 2. 本地正式构建命令

在项目根目录下，启动 PowerShell 执行全自动化正式构建：

```powershell
powershell -ExecutionPolicy Bypass -File .\build.ps1 -Release -IsccPath "D:\Program Files\Inno Setup 7\ISCC.exe"
```

### 构建流程包含的自动化门禁：
1. 验证 Python 解释器为 `3.13.11 final`；
2. 验证 Inno Setup 编译器路径有效性（缺失时快速阻断失败）；
3. 自动执行依赖完整性检查（`pip check`）与语法预编译（`compileall`）；
4. 运行全量源码自动化测试（全部测试必须 100% 通过）；
5. 自动归档备份旧产物，清理临时构建缓存；
6. 调用 PyInstaller 根据 `MCPAssistant.spec` 生成独立自包含目录（`dist\MCPAssistant\`）；
7. 自动将版权许可（`LICENSE`、`NO-SALE-ADDENDUM.md`、`NOTICE.md`、`THIRD_PARTY_NOTICES.md`、`QT_LGPL_COMPLIANCE.md`、`PRIVACY.md`、`licenses/` 目录）复制到打包产物中；
8. 验证核心动态链接库（单一 `python313.dll`）与架构一致性；
9. 启动真实打包产物子进程执行 MCP 协议 stdio 烟测与 Qt 界面烟测；
10. 打包生成绿色版压缩包：`dist\MCP助手-v1.0.0-win-x64-portable.zip`（内含唯一顶层目录 `MCPAssistant\`）；
11. 调用 Inno Setup 编译生成安装程序：`dist\MCP助手-v1.0.0-win-x64-setup.exe`（支持“仅当前用户”与“所有用户”双模式安装，内建安装目录写权限预探测阻断；卸载默认保留连接码和个人数据；仅允许非提权的当前用户模式在两次确认后彻底清除；所有用户/管理员模式卸载自动跳过清理以保护其他 Windows 账号数据；静默卸载一律安全保留数据；内建 Windows 重解析点/符号链接防穿透保护）；
12. 自动计算并输出 `dist\SHA256SUMS.txt` 校验和文件。

---

## 3. 本地发布产物规范

构建完成后，`dist\` 目录下将生成以下合规产物：

| 产物文件 | 说明 | 结构与要求 |
| :--- | :--- | :--- |
| `dist\MCPAssistant\MCPAssistant.exe` | 绿色主程序 | 独立 onedir 结构，自带 Python 运行时与所有依赖，解压即用 |
| `dist\MCP助手-v1.0.0-win-x64-portable.zip` | 绿色版压缩包 | 仅含单层顶层目录 `MCPAssistant\`，解压后双击 `MCPAssistant.exe` 即可运行 |
| `dist\MCP助手-v1.0.0-win-x64-setup.exe` | 一键安装包 | Inno Setup 编译，内置许可证同意页面、程序安装及开始菜单/桌面快捷方式 |
| `dist\SHA256SUMS.txt` | 完整性校验文件 | 包含上述 ZIP、Setup 及主 EXE 的标准 SHA-256 哈希值 |

---

## 4. 安全与操作纪律

1. **本地纯净执行**: 所有构建与产物整理过程必须在本地离线/沙箱内完成，严禁调用任何自动化上传命令或未经用户确认的 Git 推送。
2. **凭据安全**: 发布构建产物中严禁混入开发者的任何本地配置文件（`config.json`）、凭据密文（`secrets.dat`）、Token 或运行日志。
