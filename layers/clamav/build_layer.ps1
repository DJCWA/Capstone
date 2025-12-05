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

# Remove any old zip
$zipPath = Join-Path $ScriptDir "clamav-layer.zip"
if (Test-Path $zipPath) {
    Remove-Item $zipPath -Force
}

# Single-line shell script run INSIDE amazonlinux:2
# - installs clamav + freshclam + zip
# - copies clamscan/freshclam binaries
# - finds and copies libclamav + deps into layer/lib64
# - copies virus DB into layer/share/clamav
# - moves everything under layer/opt
# - zips to clamav-layer.zip in /build
$innerScript = 'cd /build; yum -y install clamav clamav-update tar gzip zip; mkdir -p layer/bin layer/share/clamav layer/lib64; cp /usr/bin/clamscan layer/bin/ 2>/dev/null || true; cp /usr/bin/freshclam layer/bin/ 2>/dev/null || true; for lib in $(find /usr/lib64 /lib64 /usr/lib /lib -maxdepth 5 -type f \( -name "libclamav.so*" -o -name "libfreshclam.so*" -o -name "libjson-c.so*" -o -name "libpcre2-8.so*" \) 2>/dev/null); do cp -P "$lib" layer/lib64/ 2>/dev/null || true; done; freshclam || echo freshclam_failed; if [ -d /var/lib/clamav ]; then cp -r /var/lib/clamav/* layer/share/clamav/ 2>/dev/null || true; fi; mkdir -p layer/opt; mv layer/bin layer/share layer/lib64 layer/opt/ 2>/dev/null || true; zip -r clamav-layer.zip layer'

# Make sure Docker is available
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Error "Docker is not installed or not in PATH. Please install Docker Desktop and try again."
    exit 1
}

# Run the Amazon Linux container, mounting this folder at /build
docker run --rm `
  -v "${ScriptDir}:/build" `
  amazonlinux:2 `
  bash -lc "$innerScript"

Write-Host "Done. Layer zip created at: $zipPath"
