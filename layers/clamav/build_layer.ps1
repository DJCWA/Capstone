# layers/clamav/build_layer.ps1

param()

# Always run from the folder where this script lives
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

Write-Host "Building MINIMAL ClamAV Lambda layer in: $ScriptDir"

# This folder will map directly to /opt in the Lambda runtime
$layerRoot = Join-Path $ScriptDir "opt_root"
if (Test-Path $layerRoot) {
    Remove-Item -Recurse -Force $layerRoot
}
New-Item -ItemType Directory -Path $layerRoot | Out-Null

# Single-line shell script to avoid CRLF issues.
# IMPORTANT: paste this as ONE line between the single quotes.
$innerScript = 'cd /build; yum -y install clamav clamav-update tar gzip; mkdir -p opt_root/bin opt_root/lib64; cp /usr/bin/clamscan opt_root/bin/ 2>/dev/null || true; cp /usr/bin/freshclam opt_root/bin/ 2>/dev/null || true; cp /usr/lib64/libclamav.so* opt_root/lib64/ 2>/dev/null || true; cp /usr/lib64/libfreshclam.so* opt_root/lib64/ 2>/dev/null || true;'

# Make sure Docker is available
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Error "Docker is not installed or not in PATH. Please install Docker Desktop and try again."
    exit 1
}

# Run the Amazon Linux 2 container to build the layer in C:\...\layers\clamav\opt_root
docker run --rm `
  -v "${ScriptDir}:/build" `
  amazonlinux:2 `
  bash -lc "$innerScript"

# Now create the ZIP on the HOST using PowerShell
$zipPath = Join-Path $ScriptDir "clamav-layer.zip"
if (Test-Path $zipPath) {
    Remove-Item $zipPath -Force
}

# Zip the CONTENTS of opt_root so the runtime sees:
#   /opt/bin/...
#   /opt/lib64/...
Compress-Archive -Path (Join-Path $layerRoot "*") -DestinationPath $zipPath -Force

Write-Host "Done. Layer zip created at: $zipPath"
