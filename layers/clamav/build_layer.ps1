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

# This script runs INSIDE amazonlinux:2 and builds the layer under /build/layer
# IMPORTANT: We do NOT include the virus DB here, only:
#   - clamscan + freshclam binaries
#   - needed shared libraries
# The DB will be downloaded at runtime into /tmp/clamav by the Lambda function.
$innerScript = @'
set -e

cd /build

yum -y install clamav clamav-update tar gzip

mkdir -p layer/bin layer/lib64

# Copy binaries (best effort)
cp /usr/bin/clamscan layer/bin/ 2>/dev/null || true
cp /usr/bin/freshclam layer/bin/ 2>/dev/null || true

# Copy key shared libraries for ClamAV
if [ -d /usr/lib64 ]; then
  cp /usr/lib64/libclamav.so*     layer/lib64/ 2>/dev/null || true
  cp /usr/lib64/libfreshclam.so*  layer/lib64/ 2>/dev/null || true
fi

echo "Contents of layer/ after build:"
ls -R layer
'@

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
