param(
    [ValidateRange(1, 1440)]
    [int]$Minutes = 60
)

$ErrorActionPreference = 'Stop'

function Test-IsAdministrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

if (-not (Test-IsAdministrator)) {
    $arguments = '-NoProfile -ExecutionPolicy Bypass -File "{0}" -Minutes {1}' -f $PSCommandPath, $Minutes
    $elevated = Start-Process -FilePath 'powershell.exe' -Verb RunAs -ArgumentList $arguments -Wait -PassThru
    exit $elevated.ExitCode
}

$sourceDirectory = Split-Path -Parent $PSCommandPath
$destination = 'C:\BatyaVPN'
$desktop = [Environment]::GetFolderPath('Desktop')

$requiredDependencies = @(
    'C:\FreeTurn\client.exe',
    'C:\FreeTurn\Start-BatyaVPN.ps1',
    'C:\Program Files\WireGuard\wireguard.exe',
    'C:\Program Files\WireGuard\wg.exe'
)

foreach ($path in $requiredDependencies) {
    if (-not (Test-Path -LiteralPath $path)) {
        throw "Required dependency not found: $path"
    }
}

New-Item -ItemType Directory -Path $destination -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $sourceDirectory 'on.ps1') -Destination (Join-Path $destination 'on.ps1') -Force
Copy-Item -LiteralPath (Join-Path $sourceDirectory 'off.ps1') -Destination (Join-Path $destination 'off.ps1') -Force

$shell = New-Object -ComObject WScript.Shell

function New-ElevatedShortcut {
    param(
        [string]$Name,
        [string]$Arguments,
        [string]$Description
    )

    $path = Join-Path $desktop $Name
    $shortcut = $shell.CreateShortcut($path)
    $shortcut.TargetPath = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
    $shortcut.Arguments = $Arguments
    $shortcut.WorkingDirectory = $destination
    $shortcut.IconLocation = 'C:\Program Files\WireGuard\wireguard.exe,0'
    $shortcut.Description = $Description
    $shortcut.Save()

    # Set the ShellLink RunAsUser flag so the shortcut asks for UAC directly.
    [byte[]]$bytes = [IO.File]::ReadAllBytes($path)
    $bytes[0x15] = $bytes[0x15] -bor 0x20
    [IO.File]::WriteAllBytes($path, $bytes)
}

New-ElevatedShortcut `
    -Name 'BatyaVPN ON.lnk' `
    -Arguments ('-NoLogo -NoProfile -ExecutionPolicy Bypass -File "C:\BatyaVPN\on.ps1" -Minutes {0}' -f $Minutes) `
    -Description "Safely start BatyaVPN for $Minutes minutes"

New-ElevatedShortcut `
    -Name 'BatyaVPN OFF.lnk' `
    -Arguments '-NoLogo -NoProfile -ExecutionPolicy Bypass -File "C:\BatyaVPN\off.ps1"' `
    -Description 'Stop BatyaVPN and restore normal Internet'

& (Join-Path $destination 'off.ps1')
if ($LASTEXITCODE -ne 0) {
    throw 'Installation finished, but BatyaVPN did not reach the safe OFF state.'
}

Write-Host 'BatyaVPN Windows whitelist-bypass fail-safe installed. Current state: OFF.' -ForegroundColor Green
