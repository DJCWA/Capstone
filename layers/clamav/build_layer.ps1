# layers/clamav/build_layer.ps1

param()

# Always run from the folder where this script lives
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

Write-Host "Building ClamAV Lambda layer in: $ScriptDir"

# Clean and recreate the layer directory
$layerDir = Join-Path $ScriptDir "layer"
if (Test-Path $layerDir) {
    Remove-Item -Recurse -Force $layerDir
}
New-Item -ItemType Directory -Path $layerDir | Out-Null

# Single-line shell script to avoid CRLF issues
# - Installs ClamAV + freshclam
# - Copies binaries
# - Copies virus DB (if present) into the layer
# - Builds /opt structure for Lambda under /build/layer/opt/...
$innerScript = 'cd /build; yum -y install clamav clamav-update tar gzip; mkdir -p layer/bin layer/share/clamav; cp /usr/bin/clamscan layer/bin/ 2>/dev/null || true; cp /usr/bin/freshclam layer/bin/ 2>/dev/null || true; freshclam || echo freshclam_failed; if [ -d /var/lib/clamav ]; then cp -r /var/lib/clamav/* layer/share/clamav/ 2>/dev/null || true; fi; mkdir -p layer/opt; mv layer/bin layer/opt/ 2>/dev/null || true; mv layer/share layer/opt/ 2>/dev/null || true;'

# Make sure Docker is available
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Error "Docker is not installed or not in PATH. Please install Docker Desktop and try again."
    exit 1
}

# Run the Amazon Linux container to build the layer under C:\...\layers\clamav\layer
docker run --rm `
  -v "${ScriptDir}:/build" `
  amazonlinux:2 `
  bash -lc "$innerScript"

# Now create the ZIP on the HOST using PowerShell
$zipPath = Join-Path $ScriptDir "clamav-layer.zip"
if (Test-Path $zipPath) {
    Remove-Item $zipPath -Force
}

Compress-Archive -Path (Join-Path $layerDir "*") -DestinationPath $zipPath -Force

Write-Host "Done. Layer zip created at: $zipPath"
