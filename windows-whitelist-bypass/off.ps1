param(
    [switch]$FromTask
)

$ErrorActionPreference = 'Stop'
$TunnelDisplayName = 'WireGuard Tunnel: BatyaVPN-Laptop'
$FreeTurnPath = 'C:\FreeTurn\client.exe'
$StateDirectory = 'C:\BatyaVPN'
$WrapperPidFile = Join-Path $StateDirectory 'freeturn-wrapper.pid'
$AutoOffTaskName = 'BatyaVPN Auto-Off'
$LogFile = Join-Path $StateDirectory 'batyavpn.log'

function Write-Log([string]$Message) {
    try {
        Add-Content -LiteralPath $LogFile -Value ("{0:u} OFF {1}" -f (Get-Date), $Message) -Encoding UTF8
    }
    catch { }
}

function Test-IsAdministrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

if (-not (Test-IsAdministrator)) {
    $arguments = '-NoProfile -ExecutionPolicy Bypass -File "{0}"' -f $PSCommandPath
    if ($FromTask) { $arguments += ' -FromTask' }
    try {
        $elevated = Start-Process -FilePath 'powershell.exe' -Verb RunAs -ArgumentList $arguments -Wait -PassThru
        exit $elevated.ExitCode
    }
    catch {
        Write-Host 'Administrator permission was not granted. BatyaVPN was not changed.' -ForegroundColor Red
        Read-Host 'Press Enter to close'
        exit 1
    }
}

try {
Write-Log 'Requested.'
$tunnel = Get-Service | Where-Object { $_.DisplayName -eq $TunnelDisplayName } | Select-Object -First 1
if ($null -ne $tunnel) {
    if ($tunnel.Status -ne 'Stopped') {
        Stop-Service -InputObject $tunnel -Force
        $tunnel.WaitForStatus('Stopped', [TimeSpan]::FromSeconds(15))
    }
    Set-Service -InputObject $tunnel -StartupType Disabled
}

# Stop only the known FreeTurn executable, never a process matched by a broad name.
Get-CimInstance Win32_Process |
    Where-Object { $_.ExecutablePath -eq $FreeTurnPath } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

# Close only the wrapper process started by on.ps1, guarding against PID reuse.
if (Test-Path -LiteralPath $WrapperPidFile) {
    $wrapperPid = 0
    [void][int]::TryParse((Get-Content -LiteralPath $WrapperPidFile -Raw).Trim(), [ref]$wrapperPid)
    if ($wrapperPid -gt 0) {
        $wrapper = Get-CimInstance Win32_Process -Filter "ProcessId = $wrapperPid"
        if ($null -ne $wrapper -and
            $wrapper.Name -ieq 'powershell.exe' -and
            $wrapper.CommandLine -like '*C:\FreeTurn\Start-BatyaVPN.ps1*') {
            Stop-Process -Id $wrapperPid -Force
        }
    }
    Remove-Item -LiteralPath $WrapperPidFile -Force -ErrorAction SilentlyContinue
}

if (-not $FromTask) {
    Unregister-ScheduledTask -TaskName $AutoOffTaskName -Confirm:$false -ErrorAction SilentlyContinue
}

$remainingTunnel = Get-Service | Where-Object { $_.DisplayName -eq $TunnelDisplayName } | Select-Object -First 1
$safe = ($null -eq $remainingTunnel) -or
        ($remainingTunnel.Status -eq 'Stopped' -and $remainingTunnel.StartType -eq 'Disabled')

if (-not $safe) {
    throw 'The WireGuard tunnel did not reach Stopped + Disabled.'
}

Write-Log 'Completed successfully.'
Write-Host 'BatyaVPN is OFF. The tunnel is stopped and cannot auto-start.' -ForegroundColor Green
if (-not $FromTask) { Start-Sleep -Seconds 2 }
exit 0
}
catch {
    Write-Log ("ERROR: " + $_.Exception.Message)
    Write-Host ("BatyaVPN OFF error: " + $_.Exception.Message) -ForegroundColor Red
    if (-not $FromTask) { Read-Host 'Press Enter to close' }
    exit 1
}
