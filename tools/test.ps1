param(
    [Parameter(Mandatory = $true)]
    [string]$Endpoint,

    [Parameter()]
    [Microsoft.PowerShell.Commands.WebRequestMethod]$Method = [Microsoft.PowerShell.Commands.WebRequestMethod]::Post,

    [Parameter()]
    [string]$ContentType = "application/json",

    [Parameter()]
    [object]$Body,

    [Parameter()]
    [switch]$Quiet,

    [Parameter()]
    [switch]$RawContent
)

$bodyPayload = $null

if ($PSBoundParameters.ContainsKey("Body") -and $null -ne $Body) {
    if ($Body -is [string]) {
        $bodyPayload = $Body
    }
    elseif ($Body -is [hashtable] -or $Body -is [pscustomobject]) {
        $bodyPayload = $Body | ConvertTo-Json -Depth 16
    }
    else {
        $bodyPayload = [System.Text.Json.JsonSerializer]::Serialize($Body)
    }
}

$response = Invoke-WebRequest -Uri $Endpoint -Method $Method -ContentType $ContentType -Body $bodyPayload -SkipHttpErrorCheck

$parsedJson = $null
try {
    if ($response.Content -and -not $RawContent) {
        $parsedJson = $response.Content | ConvertFrom-Json -Depth 16
    }
}
catch {
    $parsedJson = $null
}

if (-not $Quiet) {
    Write-Host ("Status: {0} {1}" -f $response.StatusCode, $response.StatusDescription)
    if ($parsedJson) {
        Write-Host ($parsedJson | ConvertTo-Json -Depth 16)
    }
    else {
        Write-Host $response.Content
    }
}

[pscustomobject]@{
    StatusCode        = [int]$response.StatusCode
    StatusDescription = $response.StatusDescription
    Headers           = $response.Headers
    RawContent        = $response.Content
    Json              = $parsedJson
}
