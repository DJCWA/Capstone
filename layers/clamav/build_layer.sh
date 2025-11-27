#!/usr/bin/env bash
set -euo pipefail

# Always run from the folder where this script lives
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

LAYER_DIR="layer"
rm -rf "$LAYER_DIR"
mkdir -p "$LAYER_DIR"

echo "Building ClamAV Lambda layer in: $SCRIPT_DIR"

# Build using Amazon Linux (similar to Lambda runtime)
# NOTE: no -w /build; we cd /build INSIDE the container to avoid Windows path issues
docker run --rm -v "$SCRIPT_DIR":/build amazonlinux:2 bash -lc "
  set -e
  cd /build
  yum update -y
  yum install -y epel-release
  yum install -y clamav clamav-update tar gzip zip
  mkdir -p $LAYER_DIR/bin $LAYER_DIR/share/clamav
  # Update virus definitions (one-time, during build)
  freshclam --datadir=$LAYER_DIR/share/clamav || echo 'freshclam failed (rate-limited?), using existing defs if any'
  # Copy binaries
  cp /usr/bin/clamscan $LAYER_DIR/bin/
  cp /usr/bin/freshclam $LAYER_DIR/bin/
  # Strip binaries (optional, reduces size)
  strip $LAYER_DIR/bin/clamscan || true
  # Build /opt structure
  cd $LAYER_DIR
  mkdir -p opt
  mv bin opt/
  mv share opt/
  # Back to /build to zip
  cd /build
  zip -r clamav-layer.zip layer
"

echo "Done. Layer zip created at: $SCRIPT_DIR/clamav-layer.zip"
