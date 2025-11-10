param(
    [switch]$Detached,
    [switch]$Internal
)

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $PSCommandPath
$projectRoot = Split-Path $scriptDir -Parent
Set-Location $projectRoot

Write-Host "==> Stopping flirc-bridge service if running" -ForegroundColor Cyan
try {
    Get-Process -Name python -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
    # Get-Process -Name pwsh -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -like '*flirc-bridge*start.ps1*' } | Stop-Process -Force -ErrorAction SilentlyContinue
}
catch {
    Write-Verbose $_
}

if ($Detached -and -not $Internal) {
    $argsList = @(
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", "`"$PSCommandPath`"",
        "-Internal"
    )
    $proc = Start-Process "pwsh" -ArgumentList $argsList -WindowStyle Hidden -PassThru
    Write-Host "Started flirc-bridge service in detached mode (process forked)." -ForegroundColor Green
    Write-Host ("Process Id: {0}" -f $proc.Id)
    Write-Host ("Process Name: {0}" -f $proc.ProcessName)
    Write-Host ("Start Time: {0}" -f $proc.StartTime)
    Write-Host ("Full details:" )
    $proc | Format-List * | Out-String | Write-Host
    exit
}

Write-Host "==> Refreshing PATH and tool environment" -ForegroundColor Cyan
Import-Module "$env:ChocolateyInstall\helpers\chocolateyProfile.psm1"
refreshenv

if (-not (Test-Path ".\.venv\")) {
    Write-Host "==> Creating project virtual environment (.venv)" -ForegroundColor Yellow
    python -m venv ".\.venv"
}

Write-Host "==> Activating virtual environment" -ForegroundColor Cyan
. ".\.venv\Scripts\Activate.ps1"

Write-Host "==> Updating pip, wheel, and setuptools" -ForegroundColor Cyan
python -m pip install --upgrade pip wheel setuptools | Out-Host

if (-not $env:PYO3_USE_ABI3_FORWARD_COMPATIBILITY) {
    Write-Host "==> Setting PYO3_USE_ABI3_FORWARD_COMPATIBILITY=1 for pydantic-core build" -ForegroundColor Cyan
    $env:PYO3_USE_ABI3_FORWARD_COMPATIBILITY = "1"
}

Write-Host "==> Ensuring Python dependencies are installed" -ForegroundColor Cyan
python -m pip install -r ".\requirements.txt" | Out-Host

# Check and install Flirc tools if needed
Write-Host "==> Checking for Flirc tools..." -ForegroundColor Cyan
$flircUtil = Get-Command flirc_util.exe -ErrorAction SilentlyContinue
if (-not $flircUtil) {
    Write-Host "==> flirc_util.exe not found, installing..." -ForegroundColor Yellow
    if (Test-Path ".\tools\install_flirc_tools.ps1") {
        & ".\tools\install_flirc_tools.ps1"
        # Refresh PATH after installation
        $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path", "User")
    }
    else {
        Write-Warning "install_flirc_tools.ps1 not found. Please install Flirc tools manually."
    }
}

function Test-Executable($exeName, $arg = "version") {
    if (Get-Command $exeName -ErrorAction SilentlyContinue) {
        Write-Host "==> $exeName $arg" -ForegroundColor Green
        & $exeName $arg | Out-Host
    }
    else {
        Write-Warning "$exeName not found on PATH. Set IRTOOLS_PATH in .env if installed elsewhere."
    }
}

Test-Executable "irtools"
Test-Executable "flirc_util"

Write-Host "==> Launching flirc-bridge service (Ctrl+C to stop)" -ForegroundColor Cyan
python ".\run_flirc_bridge.py"
