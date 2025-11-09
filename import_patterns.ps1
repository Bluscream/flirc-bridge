# Import patterns.json into flirc-bridge service
param(
    [string]$PatternsFile = "..\patterns.json",
    [string]$ApiUrl = "http://127.0.0.1:8000/api/ingest"
)

$ErrorActionPreference = "Stop"

Write-Host "==> Importing patterns from $PatternsFile" -ForegroundColor Cyan

if (-not (Test-Path $PatternsFile)) {
    Write-Host "ERROR: Patterns file not found: $PatternsFile" -ForegroundColor Red
    exit 1
}

$payload = Get-Content $PatternsFile -Raw -Encoding UTF8

Write-Host "==> Sending to $ApiUrl..." -ForegroundColor Cyan

try {
    $response = Invoke-RestMethod -Uri $ApiUrl -Method Post -Body $payload -ContentType 'application/json; charset=utf-8'
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
    $patterns = Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/patterns.json"
    $deviceCount = ($patterns.PSObject.Properties | Measure-Object).Count
    Write-Host "==> Database now contains $deviceCount devices" -ForegroundColor Green
}
catch {
    Write-Warning "Could not verify patterns"
}

exit 0


