$ErrorActionPreference = 'SilentlyContinue'
$mutex = [Threading.Mutex]::new($false, 'Local\BatyaVPNExternalProbe')
if (-not $mutex.WaitOne(0)) { exit 0 }
try {
    while ($true) {
        & (Join-Path (Split-Path -Parent $PSCommandPath) 'windows-hysteria-probe.ps1')
        Start-Sleep -Seconds 300
    }
}
finally {
    $mutex.ReleaseMutex()
    $mutex.Dispose()
}
