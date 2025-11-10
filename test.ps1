param(
    [Parameter(Mandatory = $true)]
    [string]$Endpoint,

    [Parameter(Mandatory = $true)]
    [string]$Device,

    [Parameter(Mandatory = $true)]
    [string]$Action
)

Write-Host "Endpoint: $Endpoint"
Write-Host "Device:   $Device"
Write-Host "Action:   $Action"
