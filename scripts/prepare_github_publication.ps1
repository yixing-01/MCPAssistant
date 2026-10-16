# MCPAssistant (MCP助手) GitHub 公开发布整理脚本
# 功能：依据严格白名单构建纯净“源码仓库”、复制重命名发布成品、计算校验和并生成发布说明
# 警告：本项目严禁执行任何网络上传、git push 或 git init 操作。

param(
    [string]$TargetReleaseDir = "",
    [switch]$ForceBackup = $false
)

[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$ErrorActionPreference = "Stop"

$ProjectRoot = (Split-Path $PSScriptRoot -Parent)
$ResolvedProjectRoot = (Resolve-Path $ProjectRoot).Path

if (-not $TargetReleaseDir) {
    # 默认发布目录设定为项目根目录同级下的 "MCP助手-GitHub发布"
    $ParentDir = Split-Path $ResolvedProjectRoot -Parent
    $TargetReleaseDir = Join-Path $ParentDir "MCP助手-GitHub发布"
}

$ResolvedTargetDir = [System.IO.Path]::GetFullPath($TargetReleaseDir)

Write-Host "==================================================" -ForegroundColor Cyan
Write-Host " MCP助手 (MCPAssistant) GitHub 公开发布整理" -ForegroundColor Cyan
Write-Host "==================================================" -ForegroundColor Cyan
Write-Host "项目根目录: $ResolvedProjectRoot"
Write-Host "目标发布目录: $ResolvedTargetDir"

# 1. 安全路径校验：防止误传项目根目录或根驱动器
$driveRoot = [System.IO.Path]::GetPathRoot($ResolvedTargetDir)
if ($ResolvedTargetDir.TrimEnd('\') -eq $driveRoot.TrimEnd('\')) {
    Write-Error "安全限制阻断：禁止将盘符根目录作为发布目录！"
    exit 1
}

if ($ResolvedTargetDir.TrimEnd('\') -eq $ResolvedProjectRoot.TrimEnd('\')) {
    Write-Error "安全限制阻断：目标发布目录不能是项目工作区根目录自身！"
    exit 1
}

# 2. 如果目标目录已经存在，先安全重命名备份，坚决不对不确定目录执行递归删除
if (Test-Path $ResolvedTargetDir) {
    $timestamp = (Get-Date).ToString("yyyyMMdd-HHmmss")
    $backupDir = "${ResolvedTargetDir}-backup-${timestamp}"
    Write-Host "检测到目标发布目录已存在，执行安全重命名备份 -> $backupDir" -ForegroundColor Yellow
    Rename-Item -Path $ResolvedTargetDir -NewName (Split-Path $backupDir -Leaf)
}

# 3. 创建发布总目录结构
New-Item -ItemType Directory -Path $ResolvedTargetDir -Force | Out-Null
$SourceRepoDir = Join-Path $ResolvedTargetDir "源码仓库"
New-Item -ItemType Directory -Path $SourceRepoDir -Force | Out-Null

# ========================================================
# 4. 基于白名单生成干净的“源码仓库”
# ========================================================
Write-Host "`n正在依据白名单生成 '源码仓库' ..." -ForegroundColor Cyan

# 4.1 根文件白名单
$AllowedRootFiles = @(
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
    "MCPAssistant.spec"
)

foreach ($rf in $AllowedRootFiles) {
    $srcFile = Join-Path $ResolvedProjectRoot $rf
    if (Test-Path $srcFile) {
        Copy-Item -Path $srcFile -Destination $SourceRepoDir -Force
        Write-Host "  [+] 复制根文件: $rf" -ForegroundColor Green
    } else {
        Write-Error "缺失白名单必需根文件: $rf"
        exit 1
    }
}

# 4.2 完整目录白名单复制（自动排除缓存与编译产物）
function Copy-CleanDirectory {
    param(
        [string]$SourcePath,
        [string]$DestPath
    )
    New-Item -ItemType Directory -Path $DestPath -Force | Out-Null
    Get-ChildItem -Path $SourcePath | ForEach-Object {
        $name = $_.Name
        if ($_.PSIsContainer) {
            # 排除 __pycache__, .pytest_cache
            if ($name -ne "__pycache__" -and $name -ne ".pytest_cache") {
                Copy-CleanDirectory -SourcePath $_.FullName -DestPath (Join-Path $DestPath $name)
            }
        } else {
            # 排除 .pyc, .pyo, .tmp, .bak
            if (-not ($name.EndsWith(".pyc") -or $name.EndsWith(".pyo") -or $name.EndsWith(".tmp") -or $name.EndsWith(".bak"))) {
                Copy-Item -Path $_.FullName -Destination $DestPath -Force
            }
        }
    }
}

$AllowedDirs = @("src", "tests", "installer", "licenses")
foreach ($d in $AllowedDirs) {
    $srcDirPath = Join-Path $ResolvedProjectRoot $d
    $destDirPath = Join-Path $SourceRepoDir $d
    if (Test-Path $srcDirPath) {
        Copy-CleanDirectory -SourcePath $srcDirPath -DestPath $destDirPath
        Write-Host "  [+] 纯净复制目录: $d" -ForegroundColor Green
    } else {
        Write-Error "缺失白名单目录: $d"
        exit 1
    }
}

# 4.3 docs 目录逐项复制
$DestDocsDir = Join-Path $SourceRepoDir "docs"
New-Item -ItemType Directory -Path $DestDocsDir -Force | Out-Null
$AllowedDocs = @("architecture.md", "releasing.md")
foreach ($doc in $AllowedDocs) {
    $docSrc = Join-Path $ResolvedProjectRoot "docs\$doc"
    if (Test-Path $docSrc) {
        Copy-Item -Path $docSrc -Destination $DestDocsDir -Force
        Write-Host "  [+] 复制文档 (逐项白名单): docs\$doc" -ForegroundColor Green
    } else {
        Write-Error "缺失必需公开文档: docs\$doc"
        exit 1
    }
}

# 4.4 scripts 目录逐项复制
$DestScriptsDir = Join-Path $SourceRepoDir "scripts"
New-Item -ItemType Directory -Path $DestScriptsDir -Force | Out-Null
$AllowedScripts = @(
    "audit_public_release.py",
    "make_portable_zip.py",
    "prepare_release.ps1",
    "prepare_github_publication.ps1"
)
foreach ($s in $AllowedScripts) {
    $sSrc = Join-Path $ResolvedProjectRoot "scripts\$s"
    if (Test-Path $sSrc) {
        Copy-Item -Path $sSrc -Destination $DestScriptsDir -Force
        Write-Host "  [+] 复制脚本 (逐项白名单): scripts\$s" -ForegroundColor Green
    } else {
        Write-Error "缺失必需构建脚本: scripts\$s"
        exit 1
    }
}

# ========================================================
# 5. 复制并重命名两个发布成品（采用复制，不删除/移走 dist 原文件）
# ========================================================
Write-Host "`n正在整理发布成品 (从 dist 复制并重命名) ..." -ForegroundColor Cyan
$DistDir = Join-Path $ResolvedProjectRoot "dist"

$SrcPortable = Join-Path $DistDir "MCP助手-v1.0.0-win-x64-portable.zip"
$SrcSetup = Join-Path $DistDir "MCP助手-v1.0.0-win-x64-setup.exe"

$DestPortableName = "MCP助手-v1.0.0-绿色免安装版.zip"
$DestSetupName = "MCP助手-v1.0.0-安装版.exe"

$DestPortable = Join-Path $ResolvedTargetDir $DestPortableName
$DestSetup = Join-Path $ResolvedTargetDir $DestSetupName

if (-not (Test-Path $SrcPortable)) {
    Write-Error "未在 dist 目录中找到绿色版 ZIP: $SrcPortable"
    exit 1
}
if (-not (Test-Path $SrcSetup)) {
    Write-Error "未在 dist 目录中找到安装包 EXE: $SrcSetup"
    exit 1
}

Copy-Item -Path $SrcPortable -Destination $DestPortable -Force
Copy-Item -Path $SrcSetup -Destination $DestSetup -Force

$portableSize = (Get-Item $DestPortable).Length
$setupSize = (Get-Item $DestSetup).Length
Write-Host "  [✓] 复制绿色免安装版: $DestPortableName ($([math]::Round($portableSize / 1MB, 2)) MB)" -ForegroundColor Green
Write-Host "  [✓] 复制一键安装版: $DestSetupName ($([math]::Round($setupSize / 1MB, 2)) MB)" -ForegroundColor Green

# ========================================================
# 6. 重新计算并生成 SHA256SUMS.txt
# ========================================================
Write-Host "`n正在计算发布成品 SHA256 校验和 ..." -ForegroundColor Cyan
$portableHash = (Get-FileHash -Path $DestPortable -Algorithm SHA256).Hash.ToUpper()
$setupHash = (Get-FileHash -Path $DestSetup -Algorithm SHA256).Hash.ToUpper()

$SumsContent = "$portableHash  $DestPortableName`n$setupHash  $DestSetupName`n"
$SumsFile = Join-Path $ResolvedTargetDir "SHA256SUMS.txt"
$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($SumsFile, $SumsContent, $Utf8NoBom)
Write-Host "  [✓] 生成 SHA256SUMS.txt:" -ForegroundColor Green
Write-Host "      $portableHash  $DestPortableName"
Write-Host "      $setupHash  $DestSetupName"

# ========================================================
# 7. 生成正式的“发布说明.md”
# ========================================================
Write-Host "`n正在生成 发布说明.md ..." -ForegroundColor Cyan
$ReleaseNotesTemplate = @'
# MCP助手 (MCPAssistant) v1.0.0 公开发布说明

感谢您使用 **MCP助手**（英文技术标识：`MCPAssistant`）！本软件是一款专为 Windows 平台打造的独立 MCP（Model Context Protocol）桌面管理与安全执行代理程序。

---

## 一、发布成品与区别选择

在本次发布包中，我们为您提供了两种形式的运行版本：

| 发布文件名 | 类型 | 适用场景与区别 |
| :--- | :--- | :--- |
| **`MCP助手-v1.0.0-安装版.exe`** | 一键安装包 | **推荐普通日常使用**。支持“仅当前用户安装”（免提权，默认安装至 `%LocalAppData%\Programs`）与“为所有用户安装”（UAC 提权）双模式；自动创建桌面与开始菜单快捷方式；支持平滑卸载与数据保护。 |
| **`MCP助手-v1.0.0-绿色免安装版.zip`** | 便携免安装版 | **推荐便携或临时测试使用**。解压后得到单一顶层目录 `MCPAssistant`，内含完整自包含的 Python 运行时及所有依赖库，直接双击 `MCPAssistant.exe` 即可运行，不写入系统注册表。 |

---

## 二、Windows 适用范围与系统要求

- **支持的操作系统**: Windows 10 64-bit（版本 1809 及以上）或 Windows 11 64-bit
- **硬件架构**: x86_64 (AMD64)
- **依赖环境**: 绿色版与安装版均已自包含所有依赖与运行时，**无需**提前安装 Python 或额外环境。
- **网络条件**: 需保持网络通畅，能够正常访问兼容的 MCP 服务端点（如 `api.xiaozhi.me`）。

---

## 三、基本运行与配置方法

1. **解压或安装**：
   - 若使用安装版：双击 `MCP助手-v1.0.0-安装版.exe`，按向导提示点击“下一步”完成安装即可。
   - 若使用绿色版：解压 ZIP 包至任意不含特殊符号的目录，双击其中的 `MCPAssistant.exe`。
2. **首次配置向导**：
   - 首次启动将展示设置向导，请粘贴您的专属安全 MCP 接入地址（形如 `wss://api.xiaozhi.me/mcp/?token=...`），点击【验证并保存】。
3. **软件扫描与单项授权**：
   - 在主界面点击【扫描本机软件】按钮，程序将检索本机常见应用程序；
   - 在列表中为您允许大模型调起或关闭的软件开启授权开关；未开启授权的软件大模型无权操作。
4. **启动服务**：
   - 点击【启动服务】按钮，状态显示为“服务已就绪”后，即可通过兼容的云端大模型安全唤起本地应用。

---

## 四、完整性校验 (SHA256)

为确保您下载的程序文件完整、未经第三方恶意篡改或损坏，请在运行前核对文件的 SHA-256 哈希值：

```text
{PORTABLE_HASH}  MCP助手-v1.0.0-绿色免安装版.zip
{SETUP_HASH}  MCP助手-v1.0.0-安装版.exe
```

在 Windows PowerShell 中可通过以下命令快速校验：
```powershell
Get-FileHash -Algorithm SHA256 "MCP助手-v1.0.0-绿色免安装版.zip"
Get-FileHash -Algorithm SHA256 "MCP助手-v1.0.0-安装版.exe"
```

---

## 五、许可证与使用规则摘要

- **许可证全称**: `Apache License 2.0 与 MCPAssistant 禁止销售附加条款 1.0` (LicenseRef-MCPAssistant-No-Sale-1.0)
- **许可属性**: **源码公开软件 (Source-Available)**。由于包含对“销售软件副本”的商业限制，因此**不是** OSI 认可的开源许可证。
- **核心权利与规则**:
  - **允许商业使用**：允许任何个人、企业或商业实体在正常商业经营活动中将本软件作为办公与生产力辅助工具使用；
  - **允许收费技术服务**：允许第三方收费提供本软件的安装、配置、部署、技术咨询、运维保障与云端/服务器托管服务；
  - **禁止直接或变相销售软件副本**：**严禁任何个人或组织直接向他人出售本软件或修改版本的软件副本，严禁将软件换皮后出售，严禁将软件打包进付费资源礼包，严禁以收取激活码费、会员卡费、下载资格费等任何变相形式出售副本**；
  - **正式法律效力**：本说明仅供参考，正式条款以 `LICENSE` 及 `NO-SALE-ADDENDUM.md` 为准。自定义条款属于发布草案，未声称已获得正式法律机构审查。

---

## 六、GitHub 仓库发布与维护指引

1. **源码仓库上传规范**:
   - 上传至 GitHub 代码仓库根目录的内容，应为本目录中的 **`源码仓库` 文件夹内的完整内容**（不包含任何构建缓存、临时脚本或测试截图）。
2. **发布成品 (Release) 规范**:
   - `MCP助手-v1.0.0-绿色免安装版.zip` 与 `MCP助手-v1.0.0-安装版.exe` 应作为 **GitHub Release 的附件资产 (Release Assets)** 上传提供下载。
   - **切勿**将体积较大的二进制可执行文件或安装包直接提交进 Git 源码仓库的历史版本中。
'@

$ReleaseNotesContent = $ReleaseNotesTemplate.Replace('{PORTABLE_HASH}', $portableHash).Replace('{SETUP_HASH}', $setupHash)
$ReleaseNotesFile = Join-Path $ResolvedTargetDir "发布说明.md"
[System.IO.File]::WriteAllText($ReleaseNotesFile, $ReleaseNotesContent, $Utf8NoBom)
Write-Host "  [✓] 生成 发布说明.md 完成" -ForegroundColor Green

# ========================================================
# 8. 自动执行最终公开目录完整性与合规审计
# ========================================================
Write-Host "`n==================================================" -ForegroundColor Cyan
Write-Host " 正在对生成的发布目录执行完整合规审计" -ForegroundColor Cyan
Write-Host "==================================================" -ForegroundColor Cyan

$AuditPy = Join-Path $ResolvedProjectRoot "scripts\audit_public_release.py"
$PythonExe = Join-Path $ResolvedProjectRoot ".venv-build\Scripts\python.exe"
if (-not (Test-Path $PythonExe)) {
    $PythonExe = "python"
}

& $PythonExe $AuditPy --release-tree $ResolvedTargetDir
if ($LASTEXITCODE -ne 0) {
    Write-Error "公开发布目录审计未通过！请检查上述违规并修正。"
    exit 1
}

Write-Host "`n[SUCCESS] GitHub 公开发布整理完成且 100% 通过审计！" -ForegroundColor Green
Write-Host "发布总目录: $ResolvedTargetDir" -ForegroundColor Green
Get-ChildItem -Path $ResolvedTargetDir | Format-Table Name, Length, LastWriteTime
exit 0
