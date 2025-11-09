# Automatic Flirc tools installer for Windows
# Downloads and installs flirc_util.exe and GUI

param(
    [switch]$SkipGUI = $false,
    [string]$InstallDir = "$env:ProgramFiles\Flirc"
)

$ErrorActionPreference = "Stop"

$APPCAST_URL = "https://flirc.com/software/release/gui/windows/appcast.xml"
$TMP_DIR = "$env:TEMP\flirc_install"

Write-Host "==> Flirc Tools Installer for Windows" -ForegroundColor Cyan

# Fetch latest version from appcast
Write-Host "==> Fetching latest version info from appcast..." -ForegroundColor Cyan
try {
    $response = Invoke-WebRequest -Uri $APPCAST_URL -UseBasicParsing
    [xml]$appcast = $response.Content
    $latestItem = $appcast.rss.channel.item | Select-Object -First 1
    $FLIRC_VERSION = $latestItem.title -replace 'Version\s+', ''
    $FLIRC_SETUP_URL = $latestItem.enclosure.url
    Write-Host "==> Latest version: $FLIRC_VERSION" -ForegroundColor Green
    Write-Host "==> Download URL: $FLIRC_SETUP_URL" -ForegroundColor Gray
}
catch {
    Write-Host "WARNING: Could not fetch appcast, using fallback version" -ForegroundColor Yellow
    $FLIRC_VERSION = "3.27.19"
    $FLIRC_SETUP_URL = "https://flirc.com/software/flirc-usb/GUI/release/windows/Flirc-Setup-${FLIRC_VERSION}.exe"
    Write-Host "==> Fallback version: $FLIRC_VERSION" -ForegroundColor Yellow
}

# Check current installation
$currentVersion = $null
$flircUtilPath = Get-Command flirc_util.exe -ErrorAction SilentlyContinue
if ($flircUtilPath) {
    try {
        $versionOutput = & flirc_util.exe version 2>&1 | Select-Object -First 1
        Write-Host "==> Current flirc_util version: $versionOutput" -ForegroundColor Green
        if ($versionOutput -match $FLIRC_VERSION) {
            Write-Host "==> Already up to date!" -ForegroundColor Green
            exit 0
        }
    }
    catch {
        Write-Host "==> flirc_util found but version check failed" -ForegroundColor Yellow
    }
}
else {
    Write-Host "==> flirc_util not found, will install" -ForegroundColor Yellow
}

# Create temp directory
New-Item -ItemType Directory -Force -Path $TMP_DIR | Out-Null
$setupPath = Join-Path $TMP_DIR "Flirc-Setup-${FLIRC_VERSION}.exe"

Write-Host "==> Downloading Flirc installer..." -ForegroundColor Cyan
Write-Host "    URL: $FLIRC_SETUP_URL" -ForegroundColor Gray

try {
    # Use .NET WebClient for better progress
    $webClient = New-Object System.Net.WebClient
    $webClient.DownloadFile($FLIRC_SETUP_URL, $setupPath)
    Write-Host "==> Download complete: $setupPath" -ForegroundColor Green
}
catch {
    Write-Host "ERROR: Failed to download Flirc installer" -ForegroundColor Red
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
}

# Run installer silently
Write-Host "==> Running Flirc installer (requires elevation)..." -ForegroundColor Cyan
Write-Host "    Install directory: $InstallDir" -ForegroundColor Gray

try {
    $installArgs = @(
        "/S",  # Silent install
        "/D=$InstallDir"  # Destination directory
    )
    
    # Check if running as admin
    $isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    
    if ($isAdmin) {
        $process = Start-Process -FilePath $setupPath -ArgumentList $installArgs -Wait -PassThru -NoNewWindow
    }
    else {
        Write-Host "    Requesting administrator privileges..." -ForegroundColor Yellow
        $process = Start-Process -FilePath $setupPath -ArgumentList $installArgs -Wait -PassThru -Verb RunAs
    }
    
    if ($process.ExitCode -eq 0) {
        Write-Host "==> Installation successful!" -ForegroundColor Green
    }
    else {
        Write-Host "WARNING: Installer exited with code $($process.ExitCode)" -ForegroundColor Yellow
    }
}
catch {
    Write-Host "ERROR: Installation failed" -ForegroundColor Red
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
}

# Cleanup
Remove-Item -Recurse -Force $TMP_DIR -ErrorAction SilentlyContinue

# Verify installation
Write-Host "==> Verifying installation..." -ForegroundColor Cyan

# Refresh PATH
$env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path", "User")

$flircUtilNew = Get-Command flirc_util.exe -ErrorAction SilentlyContinue
if ($flircUtilNew) {
    $newVersion = & flirc_util.exe version 2>&1 | Select-Object -First 1
    Write-Host "==> Installed version: $newVersion" -ForegroundColor Green
    Write-Host "==> Location: $($flircUtilNew.Source)" -ForegroundColor Green
}
else {
    Write-Host "WARNING: flirc_util.exe not found in PATH after installation" -ForegroundColor Yellow
    Write-Host "         You may need to restart your shell or add $InstallDir to PATH" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "==> Installation complete!" -ForegroundColor Cyan
Write-Host "    Note: 'irtools' is a separate package. If needed, install it separately." -ForegroundColor Gray
Write-Host "    You can use flirc_util as a fallback by setting IRTOOLS_PATH=flirc_util in .env" -ForegroundColor Gray

exit 0
