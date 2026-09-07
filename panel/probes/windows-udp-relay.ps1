param(
    [Parameter(Mandatory = $true)][string]$SourceIp,
    [Parameter(Mandatory = $true)][string]$ServerIp,
    [int]$LocalPort = 24443
)

$ErrorActionPreference = 'Stop'
$localSocket = [Net.Sockets.Socket]::new(
    [Net.Sockets.AddressFamily]::InterNetwork,
    [Net.Sockets.SocketType]::Dgram,
    [Net.Sockets.ProtocolType]::Udp
)
$remoteSocket = [Net.Sockets.Socket]::new(
    [Net.Sockets.AddressFamily]::InterNetwork,
    [Net.Sockets.SocketType]::Dgram,
    [Net.Sockets.ProtocolType]::Udp
)
try {
    $localSocket.Bind([Net.IPEndPoint]::new([Net.IPAddress]::Loopback, $LocalPort))
    $remoteSocket.Bind([Net.IPEndPoint]::new([Net.IPAddress]::Parse($SourceIp), 0))
    $remoteSocket.Connect([Net.IPEndPoint]::new([Net.IPAddress]::Parse($ServerIp), 443))
    $localSocket.Blocking = $false
    $remoteSocket.Blocking = $false
    [Net.EndPoint]$clientEndpoint = $null
    $buffer = [byte[]]::new(65535)

    while ($true) {
        $readable = [Collections.ArrayList]::new()
        [void]$readable.Add($localSocket)
        [void]$readable.Add($remoteSocket)
        [Net.Sockets.Socket]::Select($readable, $null, $null, 500000)
        foreach ($socket in $readable) {
            if ($socket -eq $localSocket) {
                [Net.EndPoint]$sender = [Net.IPEndPoint]::new([Net.IPAddress]::Any, 0)
                $length = $localSocket.ReceiveFrom($buffer, [ref]$sender)
                $clientEndpoint = $sender
                [void]$remoteSocket.Send($buffer, 0, $length, [Net.Sockets.SocketFlags]::None)
            }
            elseif ($null -ne $clientEndpoint) {
                $length = $remoteSocket.Receive($buffer)
                [void]$localSocket.SendTo(
                    $buffer,
                    0,
                    $length,
                    [Net.Sockets.SocketFlags]::None,
                    $clientEndpoint
                )
            }
        }
    }
}
finally {
    $localSocket.Dispose()
    $remoteSocket.Dispose()
}
