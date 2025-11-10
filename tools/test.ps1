param(
    [Parameter(Mandatory = $true)]
    [string]$Endpoint,

    [Parameter(Mandatory = $true)]
    [string]$Device,

    [Parameter(Mandatory = $true)]
    [string]$Action,

    [Parameter()]
    [Microsoft.PowerShell.Commands.WebRequestMethod]$Method = [Microsoft.PowerShell.Commands.WebRequestMethod]::Post,

    [Parameter()]
    [string]$ContentType = "application/json"
)

$payload = @{
    device = $Device
    action = $Action
} | ConvertTo-Json

$response = Invoke-WebRequest -Uri $Endpoint -Method $Method -ContentType $ContentType -Body $payload -SkipHttpErrorCheck

Write-Output "Status: $($response.StatusCode) $($response.StatusDescription)"
Write-Output $response.Content
