#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ ! -x .venv/bin/python || ! -f apps/web/dist/index.html ]]; then
  echo '请先运行 bash scripts/install.sh' >&2
  exit 1
fi
worker_pid=''
api_pid=''
cleanup() {
  [[ -z "$worker_pid" ]] || kill "$worker_pid" 2>/dev/null || true
  [[ -z "$api_pid" ]] || kill "$api_pid" 2>/dev/null || true
  wait || true
}
trap cleanup EXIT INT TERM
.venv/bin/python -m voice_workbench_worker &
worker_pid=$!
.venv/bin/python -m uvicorn voice_workbench_api.app:app_factory --factory --host "${WORKBENCH_BIND:-127.0.0.1}" --port "${WORKBENCH_PORT:-8000}" &
api_pid=$!
wait -n "$worker_pid" "$api_pid"
