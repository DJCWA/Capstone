#!/usr/bin/env bash
set -e

LAYER_DIR="layer"
rm -rf "$LAYER_DIR"
mkdir -p "$LAYER_DIR"

# Build using Amazon Linux (similar to Lambda runtime)
docker run --rm -v "$PWD":/build -w /build amazonlinux:2 bash -c "
  yum update -y &&
  yum install -y epel-release &&
  yum install -y clamav clamav-update tar gzip &&
  mkdir -p $LAYER_DIR/bin $LAYER_DIR/share/clamav &&
  # Update virus definitions (one-time, during build)
  freshclam --datadir=$LAYER_DIR/share/clamav ||
    echo 'freshclam failed (rate-limited?), using existing defs if any' &&
  # Copy binaries
  cp /usr/bin/clamscan $LAYER_DIR/bin/ &&
  cp /usr/bin/freshclam $LAYER_DIR/bin/ &&
  # Strip binaries (optional, reduces size)
  strip $LAYER_DIR/bin/clamscan || true
"

# Zip it in the structure Lambda expects: /opt/...
cd "$LAYER_DIR"
mkdir -p opt
mv bin opt/
mv share opt/
cd ..
zip -r clamav-layer.zip layer