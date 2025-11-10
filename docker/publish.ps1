param(
    [string]$Tag = "bluscream1/flirc-bridge:latest"
)

$scriptDir = Split-Path -Parent $PSCommandPath
$repoRoot = Split-Path $scriptDir -Parent
Set-Location $repoRoot

docker buildx build `
    --platform linux/amd64,linux/arm64,linux/arm/v7 `
    -f docker/Dockerfile `
    -t $Tag `
    --push `
    .
