param([Parameter(Mandatory=$true)][string]$PlanPath,[Parameter(Mandatory=$true)][string]$PlanSha256,[switch]$NoRestart)
$ErrorActionPreference='Stop'
function File-Hash([string]$Path) {
    $stream=[IO.File]::OpenRead($Path)
    $digest=[Security.Cryptography.SHA256]::Create()
    try { return ([BitConverter]::ToString($digest.ComputeHash($stream))).Replace('-','').ToLowerInvariant() }
    finally { $stream.Dispose(); $digest.Dispose() }
}
if ((File-Hash $PlanPath) -ne $PlanSha256) { throw '更新计划校验失败。' }
$plan=Get-Content -LiteralPath $PlanPath -Raw -Encoding UTF8 | ConvertFrom-Json
if ($plan.app_id -ne 'ncc-cashier' -or $plan.exe_name -ne 'NCC出纳结算工具.exe') { throw '应用身份不符。' }
$appDir=[IO.Path]::GetFullPath($plan.app_dir)
$updateRoot=Join-Path $appDir '.updates'
function In-Root([string]$Path,[string]$Root) {
    $full=[IO.Path]::GetFullPath($Path)
    if (-not $full.StartsWith($Root.TrimEnd('\')+'\',[StringComparison]::OrdinalIgnoreCase)) { throw '路径越界。' }
    return $full
}
$stage=In-Root $plan.stage_dir $updateRoot
$backup=In-Root $plan.backup_dir $updateRoot
$null=In-Root $PlanPath $updateRoot
$lockPath=In-Root $plan.lock_path (Join-Path $appDir 'logs')
if ((Split-Path -Leaf $lockPath) -notmatch '^\.ncc_settlement_[0-9a-f]{24}\.lock$') { throw '运行锁格式无效。' }
$exe=Join-Path $appDir $plan.exe_name
$lock=$null
$applied=New-Object System.Collections.Generic.List[object]
$restart=$false
$locked=$false
try {
    $deadline=(Get-Date).AddSeconds(120)
    while (Get-Process -Id $plan.pid -ErrorAction SilentlyContinue) {
        if ((Get-Date) -gt $deadline) { throw '程序未退出，未修改文件。' }
        Start-Sleep -Milliseconds 250
    }
    $lock=[IO.File]::Open($lockPath,[IO.FileMode]::OpenOrCreate,[IO.FileAccess]::ReadWrite,[IO.FileShare]::ReadWrite)
    $lock.Lock(0,1)
    $locked=$true
    if ($lock.Length -eq 0) { $lock.WriteByte(0); $lock.Flush() }
    New-Item -ItemType Directory -Path $backup -Force | Out-Null
    $prepared=New-Object System.Collections.Generic.List[object]
    $seen=New-Object 'System.Collections.Generic.HashSet[string]' ([StringComparer]::OrdinalIgnoreCase)
    $sourceNames=@('settlement_gui.py','settlement_core.py','ncc_browser.py','ncc_safety.py','ncc_background.py','ncc_dom.js','skip_list.py','skip_list_debug.py','skip_list_results.py','tk_runtime_hook.py','package_tool.spec','build_package.ps1','调试读取文档.bat','源码运行说明.txt','完整包开始使用.txt','使用说明.txt','app_updater.py','update_dialog.py','release_identity.py','update_install.ps1','update_public_key.json','prepare_build_source.py','prepare_update_release.py','publish_ncc_release.py','update_keys.ps1')
    foreach ($file in $plan.files) {
        $relative=[string]$file.path
        if ($relative -match '(^|/)(\.|\.\.)(/|$)|[:"<>|?*]' -or $relative.StartsWith('/') -or $relative.Contains('\')) { throw '非法相对路径。' }
        $parts=$relative.Split('/')
        $allowed=($parts.Count -eq 1 -and $parts[0] -in @('NCC出纳结算工具.exe','使用说明.txt','完整包开始使用.txt','调试读取文档.bat','README.md')) -or ($parts.Count -eq 2 -and $parts[0] -eq '源码' -and $parts[1] -in $sourceNames) -or ($parts.Count -gt 1 -and $parts[0] -eq 'ms-playwright（勿动）')
        if (-not $allowed -or -not $seen.Add($relative)) { throw '更新文件不在白名单或重复。' }
        foreach ($part in $parts) { if (-not $part -or $part.EndsWith('.') -or $part.EndsWith(' ') -or $part -match '^(con|prn|aux|nul|com[1-9]|lpt[1-9])(\.|$)|[\x00-\x1f]') { throw '非法文件名。' } }
        $source=In-Root (Join-Path $stage $relative) $stage
        $target=In-Root (Join-Path $appDir $relative) $appDir
        $saved=In-Root (Join-Path $backup $relative) $backup
        $cursor=$target
        while ($cursor -ne $appDir) {
            if ((Test-Path -LiteralPath $cursor) -and ((Get-Item -LiteralPath $cursor -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw '目标路径包含联接。' }
            $cursor=Split-Path -Parent $cursor
        }
        if ((File-Hash $source) -ne $file.sha256) { throw '暂存文件校验失败。' }
        $existed=Test-Path -LiteralPath $target -PathType Leaf
        if ($existed) {
            New-Item -ItemType Directory -Path (Split-Path -Parent $saved) -Force | Out-Null
            [IO.File]::Copy($target,$saved,$false)
        }
        $prepared.Add([pscustomobject]@{Source=$source;Target=$target;Saved=$saved;Existed=$existed;Hash=$file.sha256})
    }
    foreach ($file in $prepared) {
        New-Item -ItemType Directory -Path (Split-Path -Parent $file.Target) -Force | Out-Null
        $temporary=$file.Target+'.update-'+[guid]::NewGuid().ToString('N')
        [IO.File]::Copy($file.Source,$temporary,$false)
        if ($file.Existed) { [IO.File]::Replace($temporary,$file.Target,[System.Management.Automation.Language.NullString]::Value) }
        else { [IO.File]::Move($temporary,$file.Target) }
        $applied.Add($file)
    }
    foreach ($file in $prepared) {
        if ((File-Hash $file.Target) -ne $file.Hash) { throw '安装后校验失败。' }
    }
    @{status='success';version=$plan.version;time=(Get-Date).ToString('s')} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $updateRoot 'last-result.json') -Encoding UTF8
    $restart=$true
} catch {
    $rollback=$true
    for ($i=$applied.Count-1;$i -ge 0;$i--) {
        $file=$applied[$i]
        try {
            if ($file.Existed) { [IO.File]::Copy($file.Saved,$file.Target,$true) }
            elseif (Test-Path -LiteralPath $file.Target) { Remove-Item -LiteralPath $file.Target -Force }
        } catch { $rollback=$false }
    }
    @{status='failed';rollback=$rollback;applied_count=$applied.Count;message=$_.Exception.Message;time=(Get-Date).ToString('s')} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $updateRoot 'last-result.json') -Encoding UTF8
    $restart=$rollback -and $locked
} finally {
    if ($lock) { try { $lock.Unlock(0,1) } catch {}; $lock.Dispose() }
}
if ($restart -and -not $NoRestart -and (Test-Path -LiteralPath $exe)) { Start-Process -FilePath $exe -WorkingDirectory $appDir -WindowStyle Hidden }
