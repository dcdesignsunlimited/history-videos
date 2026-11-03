#!/usr/bin/env bash
# Installs everything engine.py needs in a fresh workspace. Safe to re-run.
set -e
cd "$(dirname "$0")"
pip install --break-system-packages -q kokoro-onnx soundfile scipy numpy pillow 2>&1 | grep -v -i warning || true
mkdir -p models
REL=https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0
[ -s models/kokoro-v1.0.onnx ] || curl -sSL -o models/kokoro-v1.0.onnx $REL/kokoro-v1.0.onnx
[ -s models/voices-v1.0.bin ] || curl -sSL -o models/voices-v1.0.bin $REL/voices-v1.0.bin
if [ ! -f /usr/share/fonts/opentype/inter/Inter-Black.otf ]; then
  tmp=$(mktemp -d); curl -sSL -o $tmp/inter.zip https://github.com/rsms/inter/releases/download/v4.0/Inter-4.0.zip
  (cd $tmp && unzip -q inter.zip); mkdir -p /usr/share/fonts/opentype/inter
  find $tmp -name 'Inter-*.otf' -exec cp {} /usr/share/fonts/opentype/inter/ \;
fi
[ -f /usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf ] || { echo "MISSING DejaVu fonts"; exit 1; }
command -v ffmpeg >/dev/null || { echo "MISSING ffmpeg"; exit 1; }
echo "setup ok"
