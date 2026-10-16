# MCP助手 (MCPAssistant)

[![License: Apache 2.0 with MCPAssistant No-Sale Addendum 1.0](https://img.shields.io/badge/License-Apache_2.0_w%2F_No--Sale_Addendum_1.0-blue.svg)](NO-SALE-ADDENDUM.md)
[![Platform: Windows](https://img.shields.io/badge/Platform-Windows_10_%2F_11_x64-0078D6.svg)](https://microsoft.com)

**MCP助手**（英文技术标识：`MCPAssistant`）是一款专为 Windows 平台打造的独立 MCP（Model Context Protocol）桌面管理与安全执行代理程序。通过本助手，您可以安全、可控地连接兼容的云端大模型 MCP 服务，实现基于大模型的本地应用程序安全发现与调起控制。

> **声明：关于开源属性与许可证的重要提示**  
> 本软件属于**源码公开软件 (Source-Available)**，自有代码采用 **Apache License 2.0 与 MCPAssistant 禁止销售附加条款 1.0** 发布。  
> **本许可不是 OSI（开放源代码倡议组织）认可的开源许可证**，应称为“源代码公开”或“source-available”。  
> - **允许商业使用**：允许在公司、企业日常运营及商业活动中免费部署和使用本软件；  
> - **允许收费服务**：允许第三方收费提供安装、配置、部署、技术支持、维护、咨询及云端/服务器托管服务；  
> - **严禁销售软件副本**：**严禁直接或变相以收取金钱、费用或其他有价对价的方式，向他人提供本软件或其修改版本的软件副本**（包括单独出售、换皮出售、捆绑进收费合集包、或要求购买会员/激活码/下载资格才能取得副本等）；  
> - **正式效力提示**：README 中的简要说明仅用于帮助理解，正式法律条款以 [LICENSE](LICENSE) 与 [NO-SALE-ADDENDUM.md](NO-SALE-ADDENDUM.md) 为准。详见 [许可证与开源声明](#许可证与开源声明)。

---

## 目录

- [核心功能](#核心功能)
- [特别重要说明与免责声明](#特别重要说明与免责声明)
- [系统要求](#系统要求)
- [普通用户使用指引](#普通用户使用指引)
  - [使用绿色免安装版 (Portable)](#1-使用绿色免安装版-portable)
  - [使用一键安装包 (Setup)](#2-使用一键安装包-setup)
  - [配置与连接步骤](#3-配置与连接步骤)
- [安全与授权机制](#安全与授权机制)
- [开发者指南与源码构建](#开发者指南与源码构建)
- [许可证与开源声明](#许可证与开源声明)
- [相关文档链接](#相关文档链接)

---

## 核心功能

1. **安全接入与 MCP 桥接**:
   - 作为本地安全代理，连接兼容的 MCP WebSocket 服务端点；
   - 保持标准的 Model Context Protocol (MCP) JSON-RPC 通信协议，保障本地与云端的高效互通。
2. **应用目录智能扫描与逐项授权**:
   - 用户主动授权后，智能发现本机已安装的主流桌面程序与安全快捷方式；
   - 提供直观的图形化授权开关，只有经过用户明确勾选的应用，大模型 MCP 服务才有权拉起。
3. **多重安全防护机制**:
   - 系统维护工具、卸载器、安装包及高危命令行（PowerShell / CMD / 注册表编辑器）由底层安全规则硬性阻断，永不放行；
   - 接入 Token 采用 Windows DPAPI 硬件级绑定强加密存储，绝不以明文落盘；
   - 本地运行日志全面自动脱敏，保护用户敏感隐私。
4. **现代化 Windows 桌面体验**:
   - 基于 PySide6 (Qt 6.12.0) 打造的精致界面，原生支持 Windows 浅色/深色主题与高 DPI 动态缩放；
   - 支持系统托盘静默驻留、开机自启动与快速唤醒。

---

## 特别重要说明与免责声明

1. **非官方关系声明**:
   本项目是由社区作者“定义”独立设计并研发的第三方工具，**并非小智官方产品**，亦未获得小智官方的授权、背书或商业合作。
2. **服务端兼容性范围**:
   **当前版本仅支持 `api.xiaozhi.me` 的安全 MCP 接入地址**（即 `wss://api.xiaozhi.me/mcp/?token=...`）。软件内置了严格的安全白名单校验，不支持且会拒绝非官方域名的任意未经验证地址。
3. **保护您的 Token**:
   **切勿将您的 MCP 接入地址或 Token 公开发布至任何 Issue、论坛、代码仓库或截图之中！** 拥有该 Token 的服务方即可通过已连接的客户端发起经授权的工具调用。
4. **SmartScreen 风险提示**:
   由于本客户端为独立开发者发布的免费工具，尚未购买昂贵的微软商业代码签名证书（EV Certificate）。在 Windows 10/11 上首次双击安装包或主程序时，Windows Defender SmartScreen 可能会弹出“已保护你的电脑”的未知发布者提示。这是未签名新软件的正常现象，点击“更多信息”并选择“仍要运行”即可。

---

## 系统要求

- **操作系统**: Windows 10 64-bit (1809 及以上) 或 Windows 11 64-bit
- **硬件架构**: x86_64 (AMD64)
- **网络连接**: 能够正常访问 Internet 以连接 MCP 服务

---

## 普通用户使用指引

### 1. 使用绿色免安装版 (Portable)
1. 从发布页面下载 `MCP助手-v1.0.0-win-x64-portable.zip`；
2. 解压 ZIP 压缩包至您希望存放的任意目录（解压后会得到一个完整的 `MCPAssistant` 文件夹）；
3. 打开文件夹，双击 `MCPAssistant.exe` 即可直接运行（无需安装 Python 或任何运行库）。

### 2. 使用一键安装包 (Setup)
1. 下载 `MCP助手-v1.0.0-win-x64-setup.exe`；
2. 双击安装程序，向导支持两种安装模式：
   - **仅为当前用户安装 (默认推荐)**: 无需管理员权限，默认安装至当前用户的程序目录（`%LocalAppData%\Programs\MCPAssistant`）。若手动选择 `D:\Program Files` 等受保护路径，安装器将在复制前进行写权限预检查并友好提示；
   - **为所有用户安装**: 选择此模式将触发 Windows UAC 管理员提权确认，支持安装至 `C:\Program Files`、`D:\Program Files` 等系统级保护目录。安装完成后主程序依然以当前普通用户权限启动，绝不常驻管理员权限；
3. 安装完成后会自动创建开始菜单项与桌面快捷方式，便于日常唤起。

### 3. 卸载与个人数据保留
- **默认卸载行为**: 卸载向导默认会**完整保留**您的所有连接码、应用授权白名单、软件目录缓存和日志，重新安装后即可直接恢复使用；
- **当前用户卸载（可选彻底清除）**: 在非提权的普通“当前用户”卸载模式下，若您确认不再使用本软件并希望清除所有个人数据，卸载向导会提供“同时删除 MCP助手 的全部个人数据”选项。该操作需要您**连续两次明确点击“是”**（两次确认框的默认按钮均为“否”）后才会执行，安全清理当前用户下的相关目录（包括已迁移的历史测试版数据）；
- **所有用户/管理员模式卸载（保护多账号数据）**: 本软件若采用“所有用户”或管理员模式安装，卸载时为避免因权限混淆或跨用户操作而误删其他 Windows 账号的连接码和设置，卸载向导将只删除程序文件，**所有个人数据将被安全保留**，绝不触碰或删除任何用户的 AppData 目录；
- **静默卸载**: 使用 `/SILENT` 或 `/VERYSILENT` 命令行参数执行卸载时，永远默认完整保留所有个人配置与数据，不弹窗、不执行清理。

### 4. 配置与连接步骤
1. 首次启动软件时，会自动打开【欢迎使用向导】；
2. **复制并粘贴 MCP 地址**:
   - 从兼容平台（如小智开放平台管理页面）复制您的专属 MCP 接入地址；
   - 格式形如：`wss://api.xiaozhi.me/mcp/?token=您的密钥`；
   - 将其粘贴到设置向导或主界面的输入框中，点击【验证并保存】；
3. **软件目录扫描与授权**:
   - 首次使用需点击【扫描本机软件】按钮，程序将检索本机已安装的常见应用程序；
   - 在应用列表中，开启您允许大模型控制的软件开关（同一个开关同时允许小智打开和关闭该软件，未授权的软件坚决不会被启动或关闭）；
4. **开启服务**: 点击【启动服务】按钮，看到状态变为“服务已就绪”，即代表配置完成，此时云端智能体即可通过 MCP 协议协助您打开或正常关闭指定软件的主窗口。

---

## 安全与授权机制

- **最小权限原则**: 默认拒绝一切非授权操作，只有在白名单目录且被用户激活的应用方可执行。
- **无静默扫描**: 未经用户在界面上主动点击确认，程序绝不在后台收集系统文件信息。
- **凭据硬件加密**: 所有敏感凭据使用当前 Windows 登录用户的 DPAPI 密钥进行加密（保存在 `secrets.dat`），其他用户或被盗取的文件无法解密。
- **详尽隐私策略**: 请参阅 [PRIVACY.md](PRIVACY.md)。
- **安全漏洞报告**: 请参阅 [SECURITY.md](SECURITY.md)。

---

## 开发者指南与源码构建

若您希望从源代码运行、调试或自行打包本程序，请遵循以下步骤。

### 1. 编译与运行环境准备
- **操作系统**: Windows 10 / 11 64-bit
- **Python**: `Python 3.13.11` (推荐使用官方 64 位安装包)
- **打包工具**: `PyInstaller 6.22.3`
- **安装包工具 (可选)**: `Inno Setup 7` (ISCC)

### 2. 初始化环境与安装依赖
```powershell
# 1. 克隆代码至本地并进入项目目录
cd path\to\xiaozhi-mcp-app

# 2. 创建并激活虚拟环境 (推荐 Python 3.13.11)
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# 3. 安装开发与运行依赖
pip install -r requirements-dev.txt

# 4. 依赖完整性校验
pip check
```

### 3. 运行本地自动化测试
```powershell
# 执行全部单元测试与界面逻辑测试
pytest tests --ignore=tests/test_packaged_exe.py --ignore=tests/qt/test_packaged_qt.py -v
```

### 4. 本地一键构建 (生成绿色版与安装包)
```powershell
# 仅生成绿色免安装目录 (onedir)
powershell -ExecutionPolicy Bypass -File .\build.ps1

# 正式发布打包 (生成绿色版 ZIP + Inno Setup 安装包 + SHA256 校验和，请根据实际安装位置提供 ISCC 编译器路径)
powershell -ExecutionPolicy Bypass -File .\build.ps1 -Release -IsccPath "C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
```
构建产物将输出至 `dist\` 目录下。详细打包流程与门禁规范请阅读 [docs/releasing.md](docs/releasing.md)。

---

## 许可证与开源声明

- **自有代码许可**:
  本项目原创代码受 **Apache License 2.0 与 MCPAssistant 禁止销售附加条款 1.0** 约束。
  - 中文全称: `Apache License 2.0 与 MCPAssistant 禁止销售附加条款 1.0`
  - 英文全称: `Apache License 2.0 with MCPAssistant No-Sale Addendum 1.0`
  - 许可证标识: `LicenseRef-MCPAssistant-No-Sale-1.0`
  - 基础许可正文: [LICENSE](LICENSE)
  - 禁止销售附加条款正文: [NO-SALE-ADDENDUM.md](NO-SALE-ADDENDUM.md)
  - 版权声明与署名要求: [NOTICE.md](NOTICE.md)
  - 许可属性与核心规则：
    * 允许出于个人、组织内部目的免费使用；
    * 允许在商业活动中将本软件作为办公或生产辅助工具；
    * 允许收费提供安装、部署、维护、咨询、技术支持与托管服务；
    * 唯一限制：禁止直接或变相以收取金钱或有价对价方式出售本软件或修改版的软件副本；
    * 本项目属于“源代码公开”(Source-Available)，不是 OSI 认可的开源许可证；
    * 本说明仅供帮助理解，正式法律条款以 `LICENSE` 与 `NO-SALE-ADDENDUM.md` 为准。
- **第三方开源组件**:
  本项目使用的一系列优秀开源依赖（包括 PySide6/Qt、websockets、mcp、pydantic 等）保留各自原作者的版权与开源协议，完整清单及许可证文本请查阅 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) 及 [licenses/](licenses/) 目录。
- **Qt LGPL 动态链接合规**:
  本项目严格遵循 LGPLv3 动态链接协议使用 PySide6/Qt，用户拥有替换兼容 Qt 运行时动态链接库的权利，详见 [QT_LGPL_COMPLIANCE.md](QT_LGPL_COMPLIANCE.md)。

---

## 相关文档链接

- [项目来源与独立性声明 (PROVENANCE.md)](PROVENANCE.md)
- [隐私政策与数据说明 (PRIVACY.md)](PRIVACY.md)
- [安全策略与漏洞提交 (SECURITY.md)](SECURITY.md)
- [贡献指南 (CONTRIBUTING.md)](CONTRIBUTING.md)
- [版本更新记录 (CHANGELOG.md)](CHANGELOG.md)
- [Qt LGPL 合规说明 (QT_LGPL_COMPLIANCE.md)](QT_LGPL_COMPLIANCE.md)
- [打包发布技术规范 (docs/releasing.md)](docs/releasing.md)
