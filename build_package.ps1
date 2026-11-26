param(
    [string]$PythonPath = '',
    [string]$ChromiumSource = '',
    [ValidatePattern('^[A-Za-z0-9_.-]+$')]
    [string]$Version = 'V1.9.5',
    [switch]$UpdateOnly,
    [switch]$PublicRelease
)

$ErrorActionPreference = 'Stop'
$projectDir = [System.IO.Path]::GetFullPath($PSScriptRoot)
$workspaceDir = Split-Path -Parent $projectDir

function Assert-ProjectPath([string]$Candidate) {
    $resolved = [System.IO.Path]::GetFullPath($Candidate)
    $projectPrefix = $projectDir.TrimEnd('\') + '\'
    if (-not $resolved.StartsWith($projectPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "输出路径不在本工具目录中：$resolved"
    }
    return $resolved
}

if (-not $PythonPath) {
    $PythonPath = Join-Path $workspaceDir 'win7_build_env\python38\python.exe'
}
if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
    throw "未找到构建用 Python：$PythonPath"
}
if (-not $ChromiumSource -and -not $UpdateOnly) {
    $browserCandidates = @(
        'C:\Users\ROG\Desktop\孵化平台\填写工具\收款结算填写工具\收款结算填写工具\ms-playwright（勿动）\chromium-1161',
        (Join-Path $workspaceDir 'v7_package_stage_current_full\收款结算单填写工具V7\ms-playwright（勿动）\chromium-1161'),
        (Join-Path $workspaceDir '付款结算单工具_打包版\ms-playwright（勿动）\chromium-1161')
    )
    foreach ($candidate in $browserCandidates) {
        if (Test-Path -LiteralPath (Join-Path $candidate 'chrome-win\chrome.exe') -PathType Leaf) {
            $ChromiumSource = $candidate
            break
        }
    }
}
if (-not $UpdateOnly -and (-not $ChromiumSource -or -not (Test-Path -LiteralPath (Join-Path $ChromiumSource 'chrome-win\chrome.exe') -PathType Leaf))) {
    throw '未找到 chromium-1161 浏览器，请通过 -ChromiumSource 指定现有完整目录。'
}

foreach ($sourceName in @('settlement_gui.py', 'settlement_core.py', 'ncc_browser.py', 'ncc_safety.py', 'ncc_background.py', 'ncc_dom.js', 'skip_list.py', 'skip_list_debug.py', 'skip_list_results.py', '调试读取文档.bat', 'package_tool.spec', 'tk_runtime_hook.py')) {
    if (-not (Test-Path -LiteralPath (Join-Path $projectDir $sourceName) -PathType Leaf)) {
        throw "源文件尚未准备好：$sourceName"
    }
}

$stamp = Get-Date -Format 'yyyyMMdd_HHmmss_fff'
$workDir = Assert-ProjectPath (Join-Path $projectDir "build\work_$stamp")
$distDir = Assert-ProjectPath (Join-Path $projectDir "build\dist_$stamp")
$buildSourceDir = Assert-ProjectPath (Join-Path $projectDir "build\source_$stamp")
$packageName = "NCC出纳结算工具_$Version"
$releaseName = if ($UpdateOnly) { $packageName } else { "${packageName}_完整版" }
$releaseDir = Assert-ProjectPath (Join-Path $projectDir "release\$releaseName")
$zipName = if ($UpdateOnly) { "${packageName}_轻量更新.zip" } else { "${packageName}_完整版.zip" }
$zipPath = Assert-ProjectPath (Join-Path $projectDir $zipName)
$logPath = Assert-ProjectPath (Join-Path $projectDir "build\build_$stamp.log")

# Never remove or overwrite a previous delivery or any of the old tools.
if (Test-Path -LiteralPath $releaseDir) {
    throw "发行目录已存在，已保留旧产物。请使用新的 -Version：$releaseDir"
}
if (Test-Path -LiteralPath $zipPath) {
    throw "压缩包已存在，已保留旧产物。请使用新的 -Version：$zipPath"
}
New-Item -ItemType Directory -Path $workDir, $distDir -Force | Out-Null
& $PythonPath (Join-Path $projectDir 'prepare_build_source.py') $buildSourceDir
if ($LASTEXITCODE -ne 0) { throw '干净构建源码准备失败。' }

Write-Host '正在构建独立窗口程序……'
Push-Location -LiteralPath $projectDir
try {
    # PyInstaller writes normal progress to stderr. Windows PowerShell 5 can
    # otherwise treat an INFO line as a terminating NativeCommandError.
    $savedPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        & $PythonPath -m PyInstaller --noconfirm --workpath $workDir --distpath $distDir (Join-Path $buildSourceDir 'package_tool.spec') *> $logPath
        $buildExitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $savedPreference
    }
    if ($buildExitCode -ne 0) {
        throw "构建失败，退出码 $buildExitCode。日志：$logPath"
    }
} finally {
    Pop-Location
}

$builtExe = Join-Path $distDir 'NCC出纳结算工具.exe'
if (-not (Test-Path -LiteralPath $builtExe -PathType Leaf)) {
    throw "构建未生成预期程序：$builtExe"
}

New-Item -ItemType Directory -Path $releaseDir | Out-Null
Copy-Item -LiteralPath $builtExe -Destination $releaseDir
if (-not $UpdateOnly) {
    $browserDir = Assert-ProjectPath (Join-Path $releaseDir 'ms-playwright（勿动）')
    New-Item -ItemType Directory -Path $browserDir | Out-Null
    Copy-Item -LiteralPath $ChromiumSource -Destination $browserDir -Recurse
    if (-not (Test-Path -LiteralPath (Join-Path $browserDir 'chromium-1161\chrome-win\chrome.exe') -PathType Leaf)) {
        throw '浏览器复制后校验失败，未创建压缩包。'
    }
}

foreach ($readme in @('使用说明.txt', 'README.md', '调试读取文档.bat')) {
    $readmePath = Join-Path $projectDir $readme
    if (Test-Path -LiteralPath $readmePath -PathType Leaf) {
        Copy-Item -LiteralPath $readmePath -Destination $releaseDir
    }
}

# 轻量更新同样包含源码和说明；仅省略浏览器与默认配置。
Copy-Item -LiteralPath (Join-Path $projectDir '完整包开始使用.txt') -Destination $releaseDir
$sourceDir = Assert-ProjectPath (Join-Path $releaseDir '源码')
New-Item -ItemType Directory -Path $sourceDir | Out-Null
foreach ($sourcePath in (Get-ChildItem -LiteralPath $buildSourceDir -File)) {
    Copy-Item -LiteralPath $sourcePath.FullName -Destination $sourceDir
}
if (-not $UpdateOnly) {
    Push-Location -LiteralPath $projectDir
    try {
        $settingsArgs = @((Join-Path $projectDir 'prepare_build_source.py'), '--settings', (Join-Path $releaseDir '出纳结算设置.json'))
        if ($PublicRelease) { $settingsArgs += '--public' }
        & $PythonPath @settingsArgs
        if ($LASTEXITCODE -ne 0) { throw '默认设置生成失败，未创建完整包。' }
    } finally {
        Pop-Location
    }
}

Compress-Archive -LiteralPath $releaseDir -DestinationPath $zipPath -CompressionLevel Optimal
Write-Host "发行目录：$releaseDir"
Write-Host "交付压缩包：$zipPath"
Write-Host "构建日志：$logPath"
