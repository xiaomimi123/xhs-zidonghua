# 采集端开机自启:确保 ego lite 在运行 → 启动千帆采集脚本 → 退出后 30 秒自动重启
# 用法:start_collector.ps1            第一个店铺(默认的 ego lite)
#       start_collector.ps1 -Instance shop2   第二个店铺(单独的 ego lite 窗口,单独的数据目录和登录)
param([string]$Instance = '')
$tag = if ($Instance) { "-$Instance" } else { '' }
$mutex = New-Object System.Threading.Mutex($false, "Global\xhs-qianfan-collector$tag")
if (-not $mutex.WaitOne(0)) { exit }   # 已有一份在跑就不重复启动
$ego = Join-Path $PSScriptRoot 'ego'
$log = Join-Path $ego "autostart$tag.log"
function L($m) { "$(Get-Date -Format 'MM-dd HH:mm:ss') $m" | Add-Content -Encoding UTF8 $log }

$exe = (Get-Command ego-browser.exe -ErrorAction SilentlyContinue).Source
if (-not $exe) { $exe = Join-Path $env:USERPROFILE '.local\share\ego\active_version_dir\ego-browser.exe' }
$app = 'C:\Program Files\Citro Labs\ego lite\Application\ego.exe'
$script = if ($Instance) { Join-Path $ego "run-$Instance.mjs" } else { Join-Path $ego 'collector.mjs' }
$cliArgs = if ($Instance) { @("--ego-server-name=$Instance", 'nodejs') } else { @('nodejs') }

function EgoRunning {   # 这个实例的 ego lite 主进程在不在(不算它的子进程)
    Get-CimInstance Win32_Process -Filter "Name='ego.exe'" | Where-Object {
        $_.CommandLine -notmatch '--type=' -and (($Instance -and $_.CommandLine -match "ego-server-name=$Instance") -or (-not $Instance -and $_.CommandLine -notmatch 'ego-server-name='))
    }
}

while ($true) {
    if (-not (EgoRunning)) {
        L '启动 ego lite'
        if ($Instance) { Start-Process $app -ArgumentList "--user-data-dir=$env:USERPROFILE\.ego-$Instance", "--ego-server-name=$Instance" } else { Start-Process $app }
        Start-Sleep 30
    }
    L '启动采集端'
    $p = Start-Process -FilePath $exe -ArgumentList $cliArgs -RedirectStandardInput $script `
        -RedirectStandardOutput (Join-Path $ego "stdout$tag.log") -RedirectStandardError (Join-Path $ego "stderr$tag.log") -WindowStyle Hidden -PassThru -Wait
    L "采集端退出(code $($p.ExitCode)),30 秒后重启"
    Start-Sleep 30
}
