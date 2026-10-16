# MCPAssistant (MCP助手) 本地发布产物整理与校验脚本
# 警告：本项目仅在本地完成打包与整理，严禁调用 Git、GitHub CLI 或任何网络上传命令。
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path $PSScriptRoot -Parent
$DistDir = Join-Path $ProjectRoot "dist"

Write-Host "==================================================" -ForegroundColor Cyan
Write-Host " MCP助手 (MCPAssistant) 本地发布产物核验与整理" -ForegroundColor Cyan
Write-Host "==================================================" -ForegroundColor Cyan

if (-not (Test-Path $DistDir)) {
    Write-Error "dist 目录不存在，请先执行 .\build.ps1 -Release"
    exit 1
}

$Errors = [System.Collections.Generic.List[string]]::new()

# 1. 检查主程序目录与主可执行文件
$MainExe = Join-Path $DistDir "MCPAssistant\MCPAssistant.exe"
if (-not (Test-Path $MainExe)) {
    $Errors.Add("缺失主程序文件: dist\MCPAssistant\MCPAssistant.exe")
} else {
    $mainSize = (Get-Item $MainExe).Length
    if ($mainSize -le 1024) {
        $Errors.Add("主程序文件过小或损坏: $MainExe ($mainSize 字节)")
    } else {
        Write-Host " [✓] 主程序已就绪: dist\MCPAssistant\MCPAssistant.exe ($mainSize 字节)" -ForegroundColor Green
    }
}

# 2. 检查主程序目录内的许可证与合规材料
$LicenseFiles = @(
    "LICENSE",
    "NO-SALE-ADDENDUM.md",
    "NOTICE.md",
    "THIRD_PARTY_NOTICES.md",
    "QT_LGPL_COMPLIANCE.md",
    "PRIVACY.md"
)
foreach ($lf in $LicenseFiles) {
    $targetPath = Join-Path $DistDir "MCPAssistant\$lf"
    if (-not (Test-Path $targetPath)) {
        $Errors.Add("dist\MCPAssistant 缺少合规文件: $lf")
    } else {
        Write-Host " [✓] 合规文档已就绪: dist\MCPAssistant\$lf" -ForegroundColor Green
    }
}

$LicensesDir = Join-Path $DistDir "MCPAssistant\licenses"
if (-not (Test-Path $LicensesDir)) {
    $Errors.Add("dist\MCPAssistant 缺少第三方许可证文本目录: licenses\")
} else {
    $count = (Get-ChildItem $LicensesDir -File).Count
    Write-Host " [✓] 第三方许可证文本目录已就绪: $count 个许可证文件" -ForegroundColor Green
}

# 3. 检查绿色版 ZIP 及其结构
$PortableZip = Join-Path $DistDir "MCP助手-v1.0.0-win-x64-portable.zip"
if (-not (Test-Path $PortableZip)) {
    $Errors.Add("缺失绿色版 ZIP: dist\MCP助手-v1.0.0-win-x64-portable.zip")
} else {
    $zipSize = (Get-Item $PortableZip).Length
    Write-Host " [✓] 绿色版 ZIP 已就绪: dist\MCP助手-v1.0.0-win-x64-portable.zip ($zipSize 字节)" -ForegroundColor Green

    # 使用 .NET 验证 ZIP 内部结构顶层是否唯一为 MCPAssistant/
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $zip = [System.IO.Compression.ZipFile]::OpenRead($PortableZip)
    $hasOtherRoot = $false
    $hasMainExe = $false
    foreach ($entry in $zip.Entries) {
        $norm = $entry.FullName.Replace('\', '/')
        if (-not ($norm.StartsWith("MCPAssistant/"))) {
            $hasOtherRoot = $true
            break
        }
        if ($norm -eq "MCPAssistant/MCPAssistant.exe") {
            $hasMainExe = $true
        }
    }
    $zip.Dispose()

    if ($hasOtherRoot) {
        $Errors.Add("绿色版 ZIP 存在非 MCPAssistant/ 的顶层路径，不符合独立单目录解压规范")
    } elseif (-not $hasMainExe) {
        $Errors.Add("绿色版 ZIP 未包含 MCPAssistant/MCPAssistant.exe 主程序")
    } else {
        Write-Host " [✓] 绿色版 ZIP 目录结构核验通过 (单一根目录 MCPAssistant/)" -ForegroundColor Green
    }
}

# 4. 检查一键安装包 Setup.exe
$SetupExe = Join-Path $DistDir "MCP助手-v1.0.0-win-x64-setup.exe"
if (-not (Test-Path $SetupExe)) {
    $Errors.Add("缺失一键安装包: dist\MCP助手-v1.0.0-win-x64-setup.exe")
} else {
    $setupSize = (Get-Item $SetupExe).Length
    if ($setupSize -le 1024) {
        $Errors.Add("一键安装包过小或损坏: $SetupExe ($setupSize 字节)")
    } else {
        Write-Host " [✓] 一键安装包已就绪: dist\MCP助手-v1.0.0-win-x64-setup.exe ($setupSize 字节)" -ForegroundColor Green
    }
}

# 5. 检查与验证 SHA256SUMS.txt
$SumsFile = Join-Path $DistDir "SHA256SUMS.txt"
if (-not (Test-Path $SumsFile)) {
    $Errors.Add("缺失校验和清单: dist\SHA256SUMS.txt")
} else {
    Write-Host " [✓] 校验和清单存在: dist\SHA256SUMS.txt" -ForegroundColor Green
    $lines = Get-Content $SumsFile
    foreach ($line in $lines) {
        $line = $line.Trim()
        if (-not $line) { continue }
        $parts = $line -split '\s+', 2
        if ($parts.Count -lt 2) {
            $Errors.Add("SHA256SUMS 格式错误行: $line")
            continue
        }
        $expectedHash = $parts[0]
        $relPath = $parts[1].TrimStart('*')
        $actualFile = Join-Path $DistDir $relPath
        if (-not (Test-Path $actualFile)) {
            $Errors.Add("SHA256SUMS 列出的文件未找到: $relPath")
        } else {
            $actualHash = (Get-FileHash -Path $actualFile -Algorithm SHA256).Hash
            if ($actualHash.ToLower() -ne $expectedHash.ToLower()) {
                $Errors.Add("哈希校验不匹配: $relPath (期望 $expectedHash，实际 $actualHash)")
            } else {
                Write-Host " [✓] 哈希匹配: $relPath ($actualHash)" -ForegroundColor Green
            }
        }
    }
}

# 汇总输出
Write-Host "--------------------------------------------------" -ForegroundColor Cyan
if ($Errors.Count -gt 0) {
    Write-Host "本地发布产物核验失败，发现 $($Errors.Count) 个问题:" -ForegroundColor Red
    foreach ($err in $Errors) {
        Write-Host "  - $err" -ForegroundColor Red
    }
    exit 1
}

Write-Host "全部本地发布产物核验成功，可直接提供给普通用户使用与测试！" -ForegroundColor Green
Write-Host "产物清单：" -ForegroundColor Green
Get-Item (Join-Path $DistDir "MCP助手-v1.0.0-win-x64-portable.zip"),
         (Join-Path $DistDir "MCP助手-v1.0.0-win-x64-setup.exe"),
         (Join-Path $DistDir "SHA256SUMS.txt") -ErrorAction SilentlyContinue |
    Format-Table Name, Length, LastWriteTime

exit 0
