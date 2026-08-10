param(
    [ValidateRange(1, 1440)]
    [int]$Minutes = 60
)

$ErrorActionPreference = 'Stop'
$TunnelDisplayName = 'WireGuard Tunnel: BatyaVPN-Laptop'
$TunnelName = 'BatyaVPN-Laptop'
$StateDirectory = 'C:\BatyaVPN'
$OffScript = Join-Path $StateDirectory 'off.ps1'
$WrapperPidFile = Join-Path $StateDirectory 'freeturn-wrapper.pid'
$FreeTurnScript = 'C:\FreeTurn\Start-BatyaVPN.ps1'
$FreeTurnPath = 'C:\FreeTurn\client.exe'
$WireGuardExe = 'C:\Program Files\WireGuard\wireguard.exe'
$WgExe = 'C:\Program Files\WireGuard\wg.exe'
$AutoOffTaskName = 'BatyaVPN Auto-Off'
$LogFile = Join-Path $StateDirectory 'batyavpn.log'

function Write-Log([string]$Message) {
    try {
        Add-Content -LiteralPath $LogFile -Value ("{0:u} ON {1}" -f (Get-Date), $Message) -Encoding UTF8
    }
    catch { }
}

function Test-IsAdministrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Invoke-FailSafe([string]$Message) {
    Write-Log ("FAIL-SAFE: " + $Message)
    Write-Host ''
    Write-Host $Message -ForegroundColor Red
    & $OffScript
    Read-Host 'Press Enter to close'
    exit 1
}

function Set-AutoOffTask {
    $powerShellExe = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
    $taskAction = New-ScheduledTaskAction -Execute $powerShellExe -Argument (
        '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "C:\BatyaVPN\off.ps1" -FromTask'
    )
    $taskTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes($Minutes)
    Register-ScheduledTask -TaskName $AutoOffTaskName -Action $taskAction -Trigger $taskTrigger `
        -User 'SYSTEM' -RunLevel Highest -Force | Out-Null
}

function Test-FreeTurnEndpoint {
    $endpoints = @(Get-NetUDPEndpoint -LocalAddress '127.0.0.1' -LocalPort 9000 -ErrorAction SilentlyContinue)
    foreach ($endpoint in $endpoints) {
        $owner = Get-CimInstance Win32_Process -Filter "ProcessId = $($endpoint.OwningProcess)"
        if ($null -ne $owner -and $owner.ExecutablePath -eq $FreeTurnPath) {
            return $true
        }
    }
    return $false
}

function Test-NormalInternet {
    $httpsOk = Test-NetConnection 1.1.1.1 -Port 443 -InformationLevel Quiet
    $dnsOk = $null -ne (Resolve-DnsName 'id.vk.ru' -ErrorAction SilentlyContinue | Select-Object -First 1)
    $tunnel = Get-Service | Where-Object { $_.DisplayName -eq $TunnelDisplayName } | Select-Object -First 1
    $tunnelStopped = ($null -eq $tunnel) -or ($tunnel.Status -eq 'Stopped')
    return ($httpsOk -and $dnsOk -and $tunnelStopped)
}

if (-not (Test-IsAdministrator)) {
    $arguments = '-NoProfile -ExecutionPolicy Bypass -File "{0}" -Minutes {1}' -f $PSCommandPath, $Minutes
    try {
        $elevated = Start-Process -FilePath 'powershell.exe' -Verb RunAs -ArgumentList $arguments -Wait -PassThru
        exit $elevated.ExitCode
    }
    catch {
        Write-Host 'Administrator permission was not granted. BatyaVPN was not started.' -ForegroundColor Red
        Read-Host 'Press Enter to close'
        exit 1
    }
}

try {
    Write-Log "Requested for $Minutes minutes."

    foreach ($requiredPath in @($OffScript, $FreeTurnScript, $FreeTurnPath, $WireGuardExe, $WgExe)) {
        if (-not (Test-Path -LiteralPath $requiredPath)) {
            Invoke-FailSafe "Required file not found: $requiredPath"
        }
    }

    # Begin with WireGuard stopped and Disabled. This state must persist through CAPTCHA.
    & $OffScript
    $offExitCode = $LASTEXITCODE
    if ($offExitCode -ne 0) {
        Invoke-FailSafe 'Could not establish the safe OFF baseline.'
    }

    # Arm a timer before starting anything. It survives this window closing.
    Set-AutoOffTask

    Write-Host ''
    Write-Host 'STEP 1 OF 2: FreeTurn authorization' -ForegroundColor Cyan
    Write-Host 'WireGuard is OFF and Disabled. Do not activate it manually.' -ForegroundColor Yellow
    Write-Host 'A FreeTurn window will open. Paste the current VK call link there.' -ForegroundColor Cyan

    $wrapper = Start-Process -FilePath 'powershell.exe' -ArgumentList @(
        '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', ('"{0}"' -f $FreeTurnScript)
    ) -WorkingDirectory 'C:\FreeTurn' -PassThru
    $wrapper.Id | Set-Content -LiteralPath $WrapperPidFile -Encoding ASCII

    $listenerReady = $false
    $deadline = (Get-Date).AddMinutes(10)
    while ((Get-Date) -lt $deadline) {
        $wrapper.Refresh()
        if ($wrapper.HasExited) { break }
        if (Test-FreeTurnEndpoint) {
            $listenerReady = $true
            break
        }
        Start-Sleep -Seconds 2
    }

    if (-not $listenerReady) {
        Invoke-FailSafe 'FreeTurn did not create its verified 127.0.0.1:9000 UDP endpoint.'
    }

    if (-not (Test-NormalInternet)) {
        Invoke-FailSafe 'Normal Internet or VK DNS is unavailable before CAPTCHA. WireGuard remains OFF.'
    }

    Write-Host ''
    Write-Host 'FreeTurn opened its local port, but authorization may still be running.' -ForegroundColor Yellow
    Write-Host 'Complete the VK CAPTCHA in the browser/FreeTurn window first.' -ForegroundColor Yellow
    Write-Host 'Return to THIS window only after CAPTCHA succeeds.' -ForegroundColor Yellow

    do {
        $confirmation = (Read-Host 'After successful CAPTCHA, type READY (or CANCEL)').Trim().ToUpperInvariant()
        if ($confirmation -eq 'CANCEL') {
            Invoke-FailSafe 'Startup cancelled by user.'
        }
        if ($confirmation -ne 'READY') {
            Write-Host 'WireGuard is still OFF. Type READY only after CAPTCHA succeeds.' -ForegroundColor Yellow
        }
    } while ($confirmation -ne 'READY')

    $wrapper.Refresh()
    if ($wrapper.HasExited -or -not (Test-FreeTurnEndpoint)) {
        Invoke-FailSafe 'FreeTurn stopped or lost 127.0.0.1:9000 before WireGuard startup.'
    }

    if (-not (Test-NormalInternet)) {
        Invoke-FailSafe 'Normal Internet or VK DNS failed before WireGuard startup. WireGuard remains OFF.'
    }

    Write-Host ''
    Write-Host 'STEP 2 OF 2: starting WireGuard and checking the tunnel' -ForegroundColor Cyan

    $tunnel = Get-Service | Where-Object { $_.DisplayName -eq $TunnelDisplayName } | Select-Object -First 1
    if ($null -eq $tunnel) {
        Write-Host 'WireGuard will open. Activate only BatyaVPN-Laptop.' -ForegroundColor Yellow
        Start-Process -FilePath $WireGuardExe
        $serviceDeadline = (Get-Date).AddMinutes(3)
        while ((Get-Date) -lt $serviceDeadline) {
            $tunnel = Get-Service | Where-Object { $_.DisplayName -eq $TunnelDisplayName } | Select-Object -First 1
            if ($null -ne $tunnel) { break }
            Start-Sleep -Seconds 2
        }
        if ($null -eq $tunnel) {
            Invoke-FailSafe 'The BatyaVPN-Laptop tunnel was not activated within three minutes.'
        }
    }

    Set-Service -InputObject $tunnel -StartupType Manual
    $tunnel.Refresh()
    if ($tunnel.Status -ne 'Running') {
        Start-Service -InputObject $tunnel
        $tunnel.WaitForStatus('Running', [TimeSpan]::FromSeconds(15))
    }

    $handshakeOk = $false
    $handshakeDeadline = (Get-Date).AddSeconds(120)
    while ((Get-Date) -lt $handshakeDeadline) {
        $raw = & $WgExe show $TunnelName latest-handshakes 2>$null
        $now = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
        foreach ($line in @($raw)) {
            $parts = $line -split '\s+'
            $epoch = 0L
            if ($parts.Count -ge 2 -and [long]::TryParse($parts[-1], [ref]$epoch) -and $epoch -gt 0) {
                if (($now - $epoch) -le 120) {
                    $handshakeOk = $true
                    break
                }
            }
        }
        if ($handshakeOk) { break }
        Start-Sleep -Seconds 3
    }

    if (-not $handshakeOk) {
        Invoke-FailSafe 'WireGuard did not establish a fresh handshake. Returning to safe OFF.'
    }

    if (-not (Test-NetConnection 1.1.1.1 -Port 443 -InformationLevel Quiet)) {
        Invoke-FailSafe 'Internet verification through BatyaVPN failed. Returning to safe OFF.'
    }

    # Give the user the full requested duration after successful verification.
    Set-AutoOffTask

    Write-Log "Started successfully after explicit FreeTurn confirmation; auto-OFF in $Minutes minutes."
    Write-Host ''
    Write-Host "BatyaVPN is ON. Auto-OFF is set for $Minutes minutes." -ForegroundColor Green
    Read-Host 'You may close this window. Press Enter to close now'
}
catch {
    Invoke-FailSafe ("Unexpected startup error: " + $_.Exception.Message)
}
