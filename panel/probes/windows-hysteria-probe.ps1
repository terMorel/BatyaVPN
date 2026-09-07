$ErrorActionPreference = 'Stop'
$probeRoot = Split-Path -Parent $PSCommandPath
$serverIp = '109.94.171.38'
$hysteria = Join-Path $probeRoot 'hysteria-windows-amd64.exe'
$clientConfig = Join-Path $probeRoot 'client.yaml'
$probeKey = Join-Path $probeRoot 'probe_ed25519'
$knownHosts = Join-Path $probeRoot 'known_hosts'
$statePath = Join-Path $probeRoot 'state.json'
$relayScript = Join-Path $probeRoot 'windows-udp-relay.ps1'

function Get-PhysicalSourceIp {
    $candidates = foreach ($adapter in [Net.NetworkInformation.NetworkInterface]::GetAllNetworkInterfaces()) {
        if ($adapter.OperationalStatus -ne [Net.NetworkInformation.OperationalStatus]::Up) {
            continue
        }
        if ($adapter.NetworkInterfaceType -notin @(
            [Net.NetworkInformation.NetworkInterfaceType]::Ethernet,
            [Net.NetworkInformation.NetworkInterfaceType]::Wireless80211
        )) {
            continue
        }
        if ($adapter.Name -match 'virtual|tunnel|tap|tun|wsl|hyper-v|v2ray|wireguard') {
            continue
        }
        $properties = $adapter.GetIPProperties()
        $gateway = $properties.GatewayAddresses | Where-Object {
            $_.Address.AddressFamily -eq [Net.Sockets.AddressFamily]::InterNetwork
        } | Select-Object -First 1
        $address = $properties.UnicastAddresses | Where-Object {
            $_.Address.AddressFamily -eq [Net.Sockets.AddressFamily]::InterNetwork -and
            -not [Net.IPAddress]::IsLoopback($_.Address)
        } | Select-Object -First 1
        if ($null -ne $gateway -and $null -ne $address) {
            [pscustomobject]@{ Address = $address.Address.IPAddressToString }
        }
    }
    return ($candidates | Select-Object -First 1).Address
}

function Wait-TcpPort([int]$Port) {
    $deadline = [DateTime]::UtcNow.AddSeconds(8)
    while ([DateTime]::UtcNow -lt $deadline) {
        $client = [Net.Sockets.TcpClient]::new()
        try {
            $task = $client.ConnectAsync('127.0.0.1', $Port)
            if ($task.Wait(300) -and $client.Connected) { return $true }
        }
        catch { }
        finally { $client.Dispose() }
        Start-Sleep -Milliseconds 150
    }
    return $false
}

function Read-State {
    try { return Get-Content -Raw -LiteralPath $statePath | ConvertFrom-Json }
    catch { return [pscustomobject]@{ consecutive_failures = 0 } }
}

function Write-State([int]$Failures, [bool]$Ok) {
    [pscustomobject]@{
        checked_at = [DateTime]::UtcNow.ToString('o')
        consecutive_failures = $Failures
        ok = $Ok
    } | ConvertTo-Json -Compress | Set-Content -LiteralPath $statePath -Encoding utf8
}

$sourceIp = Get-PhysicalSourceIp
$relay = $null
$client = $null
$timer = [Diagnostics.Stopwatch]::StartNew()
$ok = $false
try {
    if (-not $sourceIp) { throw 'No physical IPv4 interface with a gateway is active' }
    foreach ($required in @($hysteria, $clientConfig, $probeKey, $knownHosts, $relayScript)) {
        if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
            throw "Missing probe component"
        }
    }
    $relay = Start-Process -FilePath 'powershell.exe' -ArgumentList @(
        '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $relayScript,
        '-SourceIp', $sourceIp, '-ServerIp', $serverIp
    ) -WindowStyle Hidden -PassThru
    Start-Sleep -Milliseconds 300
    if ($relay.HasExited) { throw 'Direct UDP relay did not start' }
    $client = Start-Process -FilePath $hysteria -ArgumentList @(
        'client', '--config', $clientConfig, '--log-level', 'error', '--disable-update-check'
    ) -WindowStyle Hidden -PassThru
    if (-not (Wait-TcpPort -Port 18082)) { throw 'Hysteria SOCKS endpoint did not start' }
    $observedIp = & curl.exe --fail --silent --show-error --connect-timeout 5 --max-time 12 `
        --socks5-hostname 127.0.0.1:18082 https://api.ipify.org 2>$null
    if ($LASTEXITCODE -ne 0 -or $observedIp.Trim() -ne $serverIp) {
        throw 'Hysteria tunnel did not return the expected exit IP'
    }
    $ok = $true
}
catch {
    $ok = $false
}
finally {
    $timer.Stop()
    foreach ($process in @($client, $relay)) {
        if ($null -ne $process -and -not $process.HasExited) {
            Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
        }
    }
}

$oldState = Read-State
if ($ok) {
    Write-State -Failures 0 -Ok $true
    $command = "report ok $([Math]::Min(600000, [int]$timer.ElapsedMilliseconds)) windows-direct"
}
else {
    $failures = [Math]::Max(0, [int]$oldState.consecutive_failures) + 1
    Write-State -Failures $failures -Ok $false
    if ($failures -lt 3) { exit 1 }
    $command = 'report fail 0 windows-direct'
}

& ssh.exe -b $sourceIp -i $probeKey -o BatchMode=yes -o IdentitiesOnly=yes `
    -o StrictHostKeyChecking=yes -o "UserKnownHostsFile=$knownHosts" `
    "root@$serverIp" $command 1>$null 2>$null
exit $LASTEXITCODE
