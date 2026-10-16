param(
    [switch]$Release,
    [string]$IsccPath = ""
)

# MCPAssistant (MCP助手) 一键构建与打包脚本
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$ErrorActionPreference = "Stop"

$ProjectRoot = $PSScriptRoot

# --------------------------------------------------
# 0. 预检查 Inno Setup 路径与 Release 发布门禁（快速失败）
# --------------------------------------------------
if ($IsccPath -ne "") {
    if (-not (Test-Path $IsccPath)) {
        Write-Error "【Release 构建阻断】指定的 Inno Setup 编译器路径不存在: $IsccPath"
        exit 1
    }
    $ResolvedIscc = $IsccPath
} else {
    $ResolvedIscc = $null

    # 1. 优先检查 PATH 中的 iscc.exe
    if (Get-Command 'iscc.exe' -ErrorAction SilentlyContinue) {
        $ResolvedIscc = (Get-Command 'iscc.exe').Source
    }

    # 2. 查询 Windows 卸载注册表中的 Inno Setup 安装位置
    if (-not $ResolvedIscc) {
        $RegRoots = @(
            'HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*',
            'HKLM:\Software\Wow6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*',
            'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*'
        )
        $InnoApps = Get-ItemProperty $RegRoots -ErrorAction SilentlyContinue | Where-Object { $_.DisplayName -like 'Inno Setup*' }
        foreach ($app in $InnoApps) {
            if ($app.InstallLocation) {
                $cand = Join-Path $app.InstallLocation 'ISCC.exe'
                if (Test-Path $cand) {
                    $ResolvedIscc = $cand
                    break
                }
            }
            if ($app.UninstallString) {
                $cleanUninstall = $app.UninstallString.Replace([char]34, [char]0).Trim()
                $candDir = Split-Path $cleanUninstall -Parent
                $cand = Join-Path $candDir 'ISCC.exe'
                if (Test-Path $cand) {
                    $ResolvedIscc = $cand
                    break
                }
            }
        }
    }

    # 3. 常见 Inno Setup 7 与 6 默认目录
    if (-not $ResolvedIscc) {
        $DefaultIsccPaths = @(
            'D:\Program Files\Inno Setup 7\ISCC.exe',
            'C:\Program Files\Inno Setup 7\ISCC.exe',
            'D:\Program Files (x86)\Inno Setup 7\ISCC.exe',
            'C:\Program Files (x86)\Inno Setup 7\ISCC.exe',
            'D:\Program Files\Inno Setup 6\ISCC.exe',
            'C:\Program Files\Inno Setup 6\ISCC.exe',
            'D:\Program Files (x86)\Inno Setup 6\ISCC.exe',
            'C:\Program Files (x86)\Inno Setup 6\ISCC.exe'
        )
        foreach ($p in $DefaultIsccPaths) {
            if (Test-Path $p) {
                $ResolvedIscc = $p
                break
            }
        }
    }
}

if ($Release -and (-not $ResolvedIscc)) {
    Write-Error "【Release 构建阻断】当前指定了 -Release 参数要求发布正式版本，但本机未检测到 Inno Setup 编译器 (ISCC.exe)！无法生成最终安装包。请安装 Inno Setup 6/7 或通过 -IsccPath 指定有效路径后再试。"
    exit 1
}

# 优先选用专用于构建的稳定版虚拟环境 .venv-build，若不存在则回退至 .venv
if (Test-Path (Join-Path $ProjectRoot ".venv-build\Scripts\python.exe")) {
    $VenvDir = Join-Path $ProjectRoot ".venv-build"
} else {
    $VenvDir = Join-Path $ProjectRoot ".venv"
}

$VenvPython = Join-Path $VenvDir "Scripts\python.exe"
$VenvPyInstaller = Join-Path $VenvDir "Scripts\pyinstaller.exe"
$VenvPytest = Join-Path $VenvDir "Scripts\pytest.exe"

Write-Host "==================================================" -ForegroundColor Cyan
Write-Host " 1. 验证构建环境 Python 版本与稳定性门禁" -ForegroundColor Cyan
Write-Host "==================================================" -ForegroundColor Cyan

if (-not (Test-Path $VenvPython)) {
    Write-Error "虚拟环境 Python 不存在: $VenvPython"
    exit 1
}

$VersionCheck = & $VenvPython -c "import sys; v = sys.version_info; print(str(v[0])+'.'+str(v[1])+'.'+str(v[2])+' ('+v[3]+')'); sys.exit(0 if (v[0], v[1], v[2], v[3]) == (3, 13, 11, 'final') else 1)"
if ($LASTEXITCODE -ne 0) {
    Write-Error "构建环境检查失败！当前 Python: $VersionCheck。正式构建必须严格使用 Python 3.13.11 final 稳定版本！"
    exit 1
}
Write-Host "构建运行环境合格: Python $VersionCheck [$VenvPython]" -ForegroundColor Green

Write-Host "验证 Python 依赖完整性 (pip check)..." -ForegroundColor Cyan
& $VenvPython -m pip check
if ($LASTEXITCODE -ne 0) {
    Write-Error "pip check 依赖一致性检查失败！"
    exit 1
}

Write-Host "预编译 Python 源码与测试语法检查 (compileall)..." -ForegroundColor Cyan
& $VenvPython -m compileall -q (Join-Path $ProjectRoot "src") (Join-Path $ProjectRoot "tests")
if ($LASTEXITCODE -ne 0) {
    Write-Error "compileall 语法检查失败！"
    exit 1
}

Write-Host "==================================================" -ForegroundColor Cyan
Write-Host " 2. 执行源码单元测试与集成测试（排除尚未打包的 EXE 测试）" -ForegroundColor Cyan
Write-Host "==================================================" -ForegroundColor Cyan

if (-not $env:QT_QPA_PLATFORM) {
    $env:QT_QPA_PLATFORM = "offscreen"
}

& $VenvPytest -v --ignore=tests/test_packaged_exe.py --ignore=tests/qt/test_packaged_qt.py
if ($LASTEXITCODE -ne 0) {
    Write-Error "源码测试未全部通过，打包已终止！"
    exit 1
}
Write-Host "源码测试全部通过！" -ForegroundColor Green

# --------------------------------------------------
# 构建前安全备份旧 build 与 dist 目录产物，严禁直接删除用户已有产物
# --------------------------------------------------
$OldDist = Join-Path $ProjectRoot "dist"
$OldBuild = Join-Path $ProjectRoot "build"
$HasOldDist = (Test-Path $OldDist) -and ((Get-ChildItem $OldDist).Count -gt 0)
$HasOldBuild = (Test-Path $OldBuild) -and ((Get-ChildItem $OldBuild).Count -gt 0)

if ($HasOldDist -or $HasOldBuild) {
    $Timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $BackupDir = Join-Path $ProjectRoot "dist-backups\pre-build-$Timestamp"
    Write-Host "备份现有构建产物至: $BackupDir ..." -ForegroundColor Cyan
    New-Item -ItemType Directory -Path $BackupDir -Force | Out-Null
    
    if ($HasOldDist) {
        $DestDist = Join-Path $BackupDir "dist"
        New-Item -ItemType Directory -Path $DestDist -Force | Out-Null
        Copy-Item -Path (Join-Path $OldDist "*") -Destination $DestDist -Recurse -Force
        Remove-Item -Path (Join-Path $OldDist "*") -Recurse -Force -ErrorAction SilentlyContinue
    }
    if ($HasOldBuild) {
        $DestBuild = Join-Path $BackupDir "build"
        New-Item -ItemType Directory -Path $DestBuild -Force | Out-Null
        Copy-Item -Path (Join-Path $OldBuild "*") -Destination $DestBuild -Recurse -Force
        Remove-Item -Path (Join-Path $OldBuild "*") -Recurse -Force -ErrorAction SilentlyContinue
    }
    Write-Host "旧构建产物已安全移动到备份目录: $BackupDir" -ForegroundColor Green
}

# 确保全新 dist 目录存在
New-Item -ItemType Directory -Path $OldDist -Force | Out-Null

Write-Host "==================================================" -ForegroundColor Green
Write-Host " 3. 执行 PyInstaller 自包含 onedir 构建" -ForegroundColor Green
Write-Host "==================================================" -ForegroundColor Green

& $VenvPyInstaller --noconfirm (Join-Path $ProjectRoot "MCPAssistant.spec")
if ($LASTEXITCODE -ne 0) {
    Write-Error "PyInstaller 构建失败！"
    exit 1
}

$DistApp = Join-Path $ProjectRoot "dist\MCPAssistant\MCPAssistant.exe"
if (Test-Path $DistApp) {
    Write-Host "自包含独立应用程序已生成: $DistApp" -ForegroundColor Green
} else {
    Write-Error "未能找到生成的主程序: $DistApp"
    exit 1
}

# 验证打包生成的 Python DLL 版本与构建解释器严格匹配
$InternalDir = Join-Path $ProjectRoot "dist\MCPAssistant\_internal"
$PyMajor = [string](& $VenvPython -c "import sys; print(sys.version_info.major)")
$PyMinor = [string](& $VenvPython -c "import sys; print(sys.version_info.minor)")
$ExpectedDllName = "python$PyMajor$PyMinor.dll"

$PythonDlls = Get-ChildItem -Path $InternalDir -Filter "python*.dll" -File -ErrorAction SilentlyContinue
Write-Host "打包内嵌的 Python DLL:" -ForegroundColor Cyan
foreach ($dll in $PythonDlls) {
    $dllName = $dll.Name
    $dllLen = $dll.Length
    Write-Host "  - $dllName (大小: $dllLen 字节)" -ForegroundColor Cyan
}

# 1. 必须找到预期主运行时 DLL
$ExpectedDllPath = Join-Path $InternalDir $ExpectedDllName
if (-not (Test-Path $ExpectedDllPath)) {
    Write-Error "构建失败：未在 _internal 目录下找到预期的主运行时 DLL: $ExpectedDllName"
    exit 1
}

# 2. 检查 DLL 文件版本必须与构建解释器版本一致
$DllVersionInfo = (Get-Item $ExpectedDllPath).VersionInfo
if (($DllVersionInfo.FileMajorPart -ne [int]$PyMajor) -or ($DllVersionInfo.FileMinorPart -ne [int]$PyMinor)) {
    Write-Error "构建失败：$ExpectedDllName 的文件版本 ($($DllVersionInfo.FileVersion)) 与构建解释器版本 ($PyMajor.$PyMinor) 不一致！"
    exit 1
}

# 3. 检查不得存在其他主版本不匹配的 python3xx.dll 运行时 DLL
$MismatchedRuntimeDlls = @($PythonDlls | Where-Object { 
    $_.Name -match '^python3\d+\.dll$' -and $_.Name -ne $ExpectedDllName 
})
if ($MismatchedRuntimeDlls.Count -gt 0) {
    $Names = ($MismatchedRuntimeDlls | ForEach-Object { $_.Name }) -join ", "
    Write-Error "构建失败：发现不匹配的 Python 主运行时 DLL: $Names"
    exit 1
}

Write-Host "Python 运行时 DLL 强制验证通过: $ExpectedDllName (版本 $($DllVersionInfo.FileVersion))" -ForegroundColor Green

# 复制合规与许可证材料到绿色程序根目录
$LicenseFiles = @(
    "LICENSE",
    "NO-SALE-ADDENDUM.md",
    "NOTICE.md",
    "THIRD_PARTY_NOTICES.md",
    "QT_LGPL_COMPLIANCE.md",
    "PRIVACY.md"
)
$DistAppDir = Join-Path $ProjectRoot "dist\MCPAssistant"
foreach ($lf in $LicenseFiles) {
    $srcFile = Join-Path $ProjectRoot $lf
    if (Test-Path $srcFile) {
        Copy-Item -Path $srcFile -Destination $DistAppDir -Force
    }
}
$LicensesFolder = Join-Path $ProjectRoot "licenses"
if (Test-Path $LicensesFolder) {
    Copy-Item -Path $LicensesFolder -Destination (Join-Path $DistAppDir "licenses") -Recurse -Force
}

Write-Host "==================================================" -ForegroundColor Cyan
Write-Host " 4. 执行全新打包产物真实子进程烟测认证" -ForegroundColor Cyan
Write-Host "==================================================" -ForegroundColor Cyan

& $VenvPytest -v tests/test_packaged_exe.py
if ($LASTEXITCODE -ne 0) {
    Write-Error "打包产物烟测失败，新构建的 EXE 无法通过 MCP 协议认证！"
    exit 1
}
Write-Host "打包产物 MCP 真实协议烟测认证通过！" -ForegroundColor Green

& $VenvPytest -v tests/qt/test_packaged_qt.py
if ($LASTEXITCODE -ne 0) {
    Write-Error "打包产物 Qt 图形界面烟测失败！"
    exit 1
}
Write-Host "打包产物 Qt 图形界面烟测认证通过！" -ForegroundColor Green

Write-Host "==================================================" -ForegroundColor Cyan
Write-Host " 5. 打包免安装绿色版 ZIP (包含单个顶级目录 MCPAssistant)" -ForegroundColor Cyan
Write-Host "==================================================" -ForegroundColor Cyan

$PortableZip = Join-Path $ProjectRoot "dist\MCP助手-v1.0.0-win-x64-portable.zip"
Write-Host "正在生成绿色版压缩包: $PortableZip ..." -ForegroundColor Cyan

& $VenvPython (Join-Path $ProjectRoot "scripts\make_portable_zip.py")

if (-not (Test-Path $PortableZip)) {
    Write-Error "生成绿色版 ZIP 失败: $PortableZip"
    exit 1
}
$PortableZipItem = Get-Item $PortableZip
$PortableZipHash = (Get-FileHash -Path $PortableZip -Algorithm SHA256).Hash
Write-Host "绿色版 ZIP 生成成功: $PortableZip" -ForegroundColor Green
Write-Host "绿色版大小: $($PortableZipItem.Length) 字节 | SHA-256: $PortableZipHash" -ForegroundColor Green

Write-Host "==================================================" -ForegroundColor Cyan
Write-Host " 6. 检查 Inno Setup 编译器环境与发布安装包构建" -ForegroundColor Cyan
Write-Host "==================================================" -ForegroundColor Cyan

$InstallerExe = Join-Path $ProjectRoot "dist\MCP助手-v1.0.0-win-x64-setup.exe"
$InstallerHash = $null

if ($ResolvedIscc) {
    Write-Host "发现 Inno Setup 编译器: $ResolvedIscc" -ForegroundColor Green
    if (Test-Path $InstallerExe) {
        Remove-Item -Force $InstallerExe
    }
    $BuildStartTime = Get-Date

    Write-Host "正在编译一键安装包..." -ForegroundColor Green
    & $ResolvedIscc (Join-Path $ProjectRoot "installer\setup.iss")
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Inno Setup 编译失败，返回码: $LASTEXITCODE"
        exit 1
    }

    if (-not (Test-Path $InstallerExe)) {
        Write-Error "Inno Setup 执行完毕，但未能找到生成的安装包: $InstallerExe"
        exit 1
    }

    $InstallerItem = Get-Item $InstallerExe
    if ($InstallerItem.Length -le 0) {
        Write-Error "生成的一键安装包大小异常: $($InstallerItem.Length) 字节"
        exit 1
    }
    if ($InstallerItem.LastWriteTime -lt $BuildStartTime.AddSeconds(-2)) {
        Write-Error "生成的一键安装包时间戳未更新，可能不是最新生成！"
        exit 1
    }

    $InstallerHash = (Get-FileHash -Path $InstallerExe -Algorithm SHA256).Hash
    Write-Host "一键安装包生成成功: $InstallerExe" -ForegroundColor Green
    Write-Host "安装包大小: $($InstallerItem.Length) 字节 | SHA-256: $InstallerHash" -ForegroundColor Green
} else {
    Write-Host "【提示】本机未检测到 Inno Setup 编译器 (ISCC.exe)。跳过 Setup.exe 生成。" -ForegroundColor Yellow
}

# --------------------------------------------------
# 7. 生成 SHA256SUMS.txt 校验和文件
# --------------------------------------------------
Write-Host "==================================================" -ForegroundColor Cyan
Write-Host " 7. 生成 SHA256 校验和清单" -ForegroundColor Cyan
Write-Host "==================================================" -ForegroundColor Cyan

$DistExeHash = (Get-FileHash -Path $DistApp -Algorithm SHA256).Hash
$SumsFile = Join-Path $ProjectRoot "dist\SHA256SUMS.txt"

$SumsLines = [System.Collections.Generic.List[string]]::new()
$SumsLines.Add("$PortableZipHash  MCP助手-v1.0.0-win-x64-portable.zip")
if ($InstallerHash) {
    $SumsLines.Add("$InstallerHash  MCP助手-v1.0.0-win-x64-setup.exe")
}
$SumsLines.Add("$DistExeHash  MCPAssistant\MCPAssistant.exe")

[System.IO.File]::WriteAllLines($SumsFile, $SumsLines, [System.Text.Encoding]::UTF8)

Write-Host "SHA256 校验文件已生成: $SumsFile" -ForegroundColor Green
Get-Content $SumsFile | ForEach-Object { Write-Host ("  " + $_) -ForegroundColor Cyan }

Write-Host "==================================================" -ForegroundColor Cyan
Write-Host " 构建与本地发布产物生成全部完成！" -ForegroundColor Green
Write-Host "==================================================" -ForegroundColor Cyan
