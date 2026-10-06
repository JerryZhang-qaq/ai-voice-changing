#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
command -v ffmpeg >/dev/null || { echo '需要系统安装 ffmpeg 和 ffprobe' >&2; exit 1; }
command -v ffprobe >/dev/null
python3 -m venv .venv
setup_cache="${WORKBENCH_SETUP_CACHE:-/tmp/voice-workbench-install-cache}"
.venv/bin/python -m pip install --cache-dir "$setup_cache/pip" 'setuptools==80.9.0' 'wheel==0.45.1'
.venv/bin/python -m pip install --cache-dir "$setup_cache/pip" -r requirements.lock
.venv/bin/python -m pip install --cache-dir "$setup_cache/pip" --no-build-isolation --no-deps -e .
cd apps/web
npm ci --cache "$setup_cache/npm"
npm run build
