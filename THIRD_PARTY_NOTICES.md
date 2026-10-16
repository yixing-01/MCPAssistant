# 第三方软件与开源许可声明 (Third-Party Software Notices)

本项目（MCP助手 / MCPAssistant）使用了以下第三方开源与分发组件。各组件的版权声明、开源许可协议及官方来源如下。全部相关组件的许可证正文已归档于项目根目录的 `licenses/` 目录中。

---

## 一、运行时直接依赖 (Runtime Direct Dependencies)

### 1. PySide6
- **包名**: `PySide6`
- **版本**: `6.12.0`
- **用途**: 官方 Qt for Python 图形用户界面框架，用于桌面主窗口、页面与交互控件渲染。
- **许可证**: `LGPL-3.0-only`（亦支持 GPL-2.0-only 或 GPL-3.0-only，本项目采用 LGPL-3.0-only 途径）
- **版权方**: The Qt Company Ltd. and other contributors
- **官方来源**: https://code.qt.io/cgit/pyside/pyside-setup.git/
- **依赖性质**: 运行时依赖（通过动态链接库 DLL 分发，遵循 LGPLv3 要求，详见 `QT_LGPL_COMPLIANCE.md`）
- **本地许可证文本**: `licenses/GNU-LGPL-3.0.txt`, `licenses/PySide6.txt`

### 2. mcp
- **包名**: `mcp`
- **版本**: `1.8.1`
- **用途**: Model Context Protocol (MCP) 官方 Python SDK，用于标准化 MCP 协议交互。
- **许可证**: `MIT`
- **版权方**: Anthropic, PBC and Model Context Protocol contributors
- **官方来源**: https://github.com/modelcontextprotocol/python-sdk
- **依赖性质**: 运行时依赖
- **本地许可证文本**: `licenses/mcp.txt`

### 3. websockets
- **包名**: `websockets`
- **版本**: `12.0`
- **用途**: 安全 WebSocket 客户端与通信管道，用于连接授权的 MCP 服务端点。
- **许可证**: `BSD-3-Clause`
- **版权方**: Aymeric Augustin and contributors
- **官方来源**: https://github.com/aaugustin/websockets
- **依赖性质**: 运行时依赖
- **本地许可证文本**: `licenses/websockets.txt`

### 4. pydantic
- **包名**: `pydantic`
- **版本**: `2.11.4`
- **用途**: 数据模式校验、配置对象序列化与类型安全解析。
- **许可证**: `MIT`
- **版权方**: Samuel Colvin and contributors
- **官方来源**: https://github.com/pydantic/pydantic
- **依赖性质**: 运行时依赖
- **本地许可证文本**: `licenses/pydantic.txt`

### 5. requests
- **包名**: `requests`
- **版本**: `2.32.3`
- **用途**: HTTP 客户端请求库，用于网络连通性探测。
- **许可证**: `Apache-2.0`
- **版权方**: Kenneth Reitz and Requests contributors
- **官方来源**: https://github.com/psf/requests
- **依赖性质**: 运行时依赖
- **本地许可证文本**: `licenses/requests.txt`

### 6. pillow
- **包名**: `pillow`
- **版本**: `12.3.0`
- **用途**: 图像处理库，用于系统原生图标加载、缩放与托盘图标处理。
- **许可证**: `MIT-CMU`
- **版权方**: 
  - Copyright © 1997-2011 by Secret Labs AB
  - Copyright © 1995-2011 by Fredrik Lundh and contributors
  - Copyright © 2010 by Jeffrey 'Alex' Clark and contributors
- **官方来源**: https://github.com/python-pillow/Pillow
- **依赖性质**: 运行时依赖
- **本地许可证文本**: `licenses/pillow.txt`

### 7. six
- **包名**: `six`
- **版本**: `1.17.0`
- **用途**: Python 兼容性工具库。
- **许可证**: `MIT`
- **版权方**: Benjamin Peterson and contributors
- **官方来源**: https://github.com/benjaminp/six
- **依赖性质**: 运行时依赖
- **本地许可证文本**: `licenses/six.txt`

### 8. pywin32
- **包名**: `pywin32`
- **版本**: `312`
- **用途**: Windows 原生 API 互操作，包括 DPAPI 凭据安全加解密、快捷方式解析、注册表操作与 Shell 启动。
- **许可证**: `PSF` (Python Software Foundation License)
- **版权方**: Mark Hammond and pywin32 contributors
- **官方来源**: https://github.com/mhammond/pywin32
- **依赖性质**: 运行时依赖
- **本地许可证文本**: `licenses/pywin32.txt`

---

## 二、运行时传递依赖 (Runtime Transitive Dependencies)

### 9. shiboken6
- **版本**: `6.12.0`
- **用途**: PySide6 C++/Python 绑定层基础支撑。
- **许可证**: `LGPL-3.0-only`
- **版权方**: The Qt Company Ltd. and contributors
- **官方来源**: https://code.qt.io/cgit/pyside/pyside-setup.git/
- **本地许可证文本**: `licenses/shiboken6.txt`

### 10. pydantic_core
- **版本**: `2.33.2`
- **用途**: Pydantic 核心解析与校验引擎 (Rust/C)。
- **许可证**: `MIT`
- **版权方**: Samuel Colvin and contributors
- **官方来源**: https://github.com/pydantic/pydantic-core
- **本地许可证文本**: `licenses/pydantic_core.txt`

### 11. annotated-types
- **版本**: `0.8.0`
- **用途**: 类型注解扩展。
- **许可证**: `MIT`
- **版权方**: Samuel Colvin and contributors
- **官方来源**: https://github.com/annotated-types/annotated-types
- **本地许可证文本**: `licenses/annotated-types.txt`

### 12. typing_extensions
- **版本**: `4.16.0`
- **用途**: Python 类型系统向后兼容。
- **许可证**: `PSF-2.0`
- **版权方**: Python Software Foundation and contributors
- **官方来源**: https://github.com/python/typing_extensions
- **本地许可证文本**: `licenses/typing_extensions.txt`

### 13. urllib3
- **版本**: `2.8.0`
- **用途**: HTTP 连接池。
- **许可证**: `MIT`
- **版权方**: urllib3 contributors
- **官方来源**: https://github.com/urllib3/urllib3
- **本地许可证文本**: `licenses/urllib3.txt`

### 14. charset-normalizer
- **版本**: `3.5.2`
- **用途**: 字符编码探测。
- **许可证**: `MIT`
- **版权方**: Ahmed TAHRI and contributors
- **官方来源**: https://github.com/Ousret/charset_normalizer
- **本地许可证文本**: `licenses/charset-normalizer.txt`

### 15. idna
- **版本**: `3.20`
- **用途**: 国际化域名 (IDNA) 处理。
- **许可证**: `BSD-3-Clause`
- **版权方**: Kim Davies and contributors
- **官方来源**: https://github.com/kjd/idna
- **本地许可证文本**: `licenses/idna.txt`

### 16. certifi
- **版本**: `2026.7.22`
- **用途**: Mozilla CA 根证书集合。
- **许可证**: `MPL-2.0`
- **版权方**: Mozilla and Certifi contributors
- **官方来源**: https://github.com/certifi/python-certifi
- **本地许可证文本**: `licenses/certifi.txt`

### 17. anyio
- **版本**: `4.15.1`
- **用途**: 异步网络框架兼容层。
- **许可证**: `MIT`
- **版权方**: Alex Grönholm and contributors
- **官方来源**: https://github.com/agronholm/anyio
- **本地许可证文本**: `licenses/anyio.txt`

### 18. httpx
- **版本**: `0.28.1`
- **用途**: 异步 HTTP 客户端。
- **许可证**: `BSD-3-Clause`
- **版权方**: Encode OSS Ltd. and contributors
- **官方来源**: https://github.com/encode/httpx
- **本地许可证文本**: `licenses/httpx.txt`

### 19. httpcore
- **版本**: `1.0.9`
- **用途**: 传输层 HTTP 协议支持。
- **许可证**: `BSD-3-Clause`
- **版权方**: Encode OSS Ltd. and contributors
- **官方来源**: https://github.com/encode/httpcore
- **本地许可证文本**: `licenses/httpcore.txt`

### 20. h11
- **版本**: `0.16.0`
- **用途**: 纯 Python HTTP/1.1 实现。
- **许可证**: `MIT`
- **版权方**: Nathaniel J. Smith and contributors
- **官方来源**: https://github.com/python-hyper/h11
- **本地许可证文本**: `licenses/h11.txt`

### 21. starlette
- **版本**: `1.7.0`
- **用途**: ASGI 基础组件。
- **许可证**: `BSD-3-Clause`
- **版权方**: Encode OSS Ltd. and contributors
- **官方来源**: https://github.com/encode/starlette
- **本地许可证文本**: `licenses/starlette.txt`

### 22. sse-starlette
- **版本**: `3.5.0`
- **用途**: Server-Sent Events (SSE) 协议支持。
- **许可证**: `BSD-3-Clause`
- **版权方**: SysNet and contributors
- **官方来源**: https://github.com/sysid/sse-starlette
- **本地许可证文本**: `licenses/sse-starlette.txt`

---

## 三、构建时工具依赖 (Build-Time Dependencies)

### 23. PyInstaller
- **包名**: `PyInstaller`
- **版本**: `6.22.3`
- **用途**: 用于将 Python 解释器、运行时及依赖打包为独立 Windows 执行程序及绿色版目录。
- **许可证**: `GPL-2.0-or-later with PyInstaller Bootloader Special Exception`
- **版权方**: PyInstaller Development Team
- **官方来源**: https://github.com/pyinstaller/pyinstaller
- **依赖性质**: 开发与构建阶段工具依赖（根据 PyInstaller Bootloader 特殊例外条款，编译打包出的应用程序不受 GPL 传染约束）

---

## 四、测试工具依赖 (Test Dependencies)

### 24. pytest
- **包名**: `pytest`
- **版本**: `8.3.3`
- **用途**: 自动化单元测试与集成测试框架。
- **许可证**: `MIT`
- **版权方**: Holger Krekel and pytest contributors
- **官方来源**: https://github.com/pytest-dev/pytest
- **依赖性质**: 仅用于开发阶段本地自动化测试，不随最终产品分发。

### 25. pytest-qt
- **包名**: `pytest-qt`
- **版本**: `4.5.0`
- **用途**: pytest 与 Qt 事件循环协同的测试插件。
- **许可证**: `MIT`
- **版权方**: Bruno Oliveira and contributors
- **官方来源**: https://github.com/pytest-dev/pytest-qt
- **依赖性质**: 仅用于开发阶段本地自动化测试，不随最终产品分发。
