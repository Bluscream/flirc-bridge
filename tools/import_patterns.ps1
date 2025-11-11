# Import patterns.json into flirc-bridge service
param(
    [string]$PatternsFile,
    [string]$ApiUrl = "http://127.0.0.1:8000/api/ingest",
    [string]$VerifyUrl = "http://127.0.0.1:8000/api/pattern",
    [string]$Token
)

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $PSCommandPath
$projectRoot = Split-Path $scriptDir -Parent
$repoRoot = Split-Path $projectRoot -Parent

if (-not $PSBoundParameters.ContainsKey("PatternsFile") -or [string]::IsNullOrWhiteSpace($PatternsFile)) {
    $PatternsFile = Join-Path $repoRoot "patterns.json"
}

Write-Host "==> Importing patterns from $PatternsFile" -ForegroundColor Cyan

if (-not (Test-Path $PatternsFile)) {
    Write-Host "ERROR: Patterns file not found: $PatternsFile" -ForegroundColor Red
    exit 1
}

$payload = Get-Content $PatternsFile -Raw -Encoding UTF8

function Add-TokenToUrl {
    param(
        [Parameter(Mandatory)]
        [string]$Url,
        [string]$TokenValue
    )

    if (-not $TokenValue) {
        return $Url
    }

    if ($Url.Contains("?")) {
        return "$Url&token=$TokenValue"
    }

    return "$Url?token=$TokenValue"
}

if ($Token) {
    $ApiUrl = Add-TokenToUrl -Url $ApiUrl -TokenValue $Token
    $VerifyUrl = Add-TokenToUrl -Url $VerifyUrl -TokenValue $Token
}

$headers = @{}
if ($Token) {
    $headers["X-Auth-Token"] = $Token
}

Write-Host "==> Sending to $ApiUrl..." -ForegroundColor Cyan

try {
    $response = Invoke-RestMethod -Uri $ApiUrl -Method Post -Body $payload -ContentType 'application/json; charset=utf-8' -Headers $headers
    Write-Host "==> Import successful!" -ForegroundColor Green
    Write-Host "    Status: $($response.status)" -ForegroundColor Green
    Write-Host "    Imported: $($response.imported) patterns" -ForegroundColor Green
}
catch {
    Write-Host "ERROR: Failed to import patterns" -ForegroundColor Red
    Write-Host $_.Exception.Message -ForegroundColor Red
    if ($_.ErrorDetails.Message) {
        Write-Host $_.ErrorDetails.Message -ForegroundColor Red
    }
    exit 1
}

Write-Host ""
Write-Host "==> Verifying patterns..." -ForegroundColor Cyan
try {
    $patterns = Invoke-RestMethod -Uri $VerifyUrl -Headers $headers
    if ($patterns -and $patterns.devices) {
        $deviceCount = $patterns.devices.Count
    }
    else {
        $deviceCount = 0
    }
    Write-Host "==> Database now contains $deviceCount devices" -ForegroundColor Green
}
catch {
    Write-Warning "Could not verify patterns"
}

exit 0
