# Qt / PySide6 LGPL 合规说明 (Qt LGPL Compliance Notice)

本项目（MCP助手 / MCPAssistant）的图形用户界面部分使用了 **PySide6**（Qt for Python 官方绑定库）。为遵循 GNU 宽通用公共许可证第三版（GNU Lesser General Public License version 3.0, LGPLv3）的各项要求，特此发布本合规说明。

---

## 1. 使用的 Qt 与 PySide6 版本

- **PySide6 框架版本**: `6.12.0`
- **Qt 底层运行时版本**: `6.12.0`
- **采用的许可途径**: `GNU Lesser General Public License version 3.0` (LGPL-3.0-only)

## 2. 许可文本与官方源码获取途径

- **LGPLv3 官方许可文本**: 本软件分发包中的 `licenses/GNU-LGPL-3.0.txt` 及 `licenses/GNU-GPL-3.0.txt`，亦可访问 GNU 官方站点查阅：https://www.gnu.org/licenses/lgpl-3.0.html
- **Qt 框架官方源码**: https://download.qt.io/official_releases/qt/ 及 https://code.qt.io/cgit/qt/qtbase.git/
- **PySide6 绑定层官方源码**: https://code.qt.io/cgit/pyside/pyside-setup.git/

## 3. 动态链接与库替换权利说明

1. **动态库分发机制**:
   本项目在源码运行、绿色版目录（onedir）以及 Inno Setup 安装包中，均**严格采用动态共享库（DLL）机制**调用 PySide6 与 Qt 运行时组件。本软件未对 Qt/PySide6 采用任何静态链接，未修改 Qt/PySide6 本身的 C++ 核心代码。
2. **用户替换动态库的权利**:
   根据 LGPLv3 第 4 条规定，本软件的最终用户有权使用自定义编译、修补或更新版本的兼容 Qt / PySide6 动态链接库（.dll / .pyd），替换本程序分发目录（`MCPAssistant\_internal\` 或安装目录下的对应文件夹）中的原文件，并验证本程序是否能正常加载运行。
3. **技术限制承诺**:
   本项目承诺不使用任何反调试、代码混淆或限制性技术阻碍用户依照 LGPLv3 权利替换或升级 Qt 动态库。

## 4. 分发与附带说明范围

- 本说明文件、LGPLv3 许可文本（`licenses/GNU-LGPL-3.0.txt`）以及完整的第三方组件声明（`THIRD_PARTY_NOTICES.md`）已完整包含在源码仓库、绿色版压缩包及安装程序中。
- **重要提示**: 本说明及 LGPLv3 的适用范围仅严格限定于 PySide6 和 Qt 相关库组件，不改变、不扩大亦不豁免本项目自有代码（受 Apache 2.0 与 MCPAssistant 禁止销售附加条款 1.0 约束）或其他第三方组件的许可条款。
