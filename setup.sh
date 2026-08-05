#!/usr/bin/env bash
set -e
command -v ffmpeg >/dev/null || { echo "Install FFmpeg first (brew install ffmpeg / apt install ffmpeg)"; exit 1; }
python3 -m pip install -r requirements.txt
echo "Done. Try: python3 -m reelfactory build products/sample-iron-shelf"
