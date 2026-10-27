#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ "${1:-}" == "--pty" ]]; then
  shift
  exec uv run python scripts/virtual_dual_com_pty.py --launch "$@"
fi

BASE_PORT="${IR_LINK_BASE_PORT:-6001}"

cleanup() {
  echo "Stopping dual-node processes..."
  kill "$APP_A_PID" "$APP_B_PID" 2>/dev/null || true
}
trap cleanup INT TERM EXIT

echo "=== Starting Node A (host, in-app broker) ==="
uv run python -m ir_terminal \
  --instance A --link-role host --link-base-port "$BASE_PORT" --auto-connect "$@" &
APP_A_PID=$!

echo "=== Starting Node B (client, retries until host is ready) ==="
uv run python -m ir_terminal \
  --instance B --link-role join --link-base-port "$BASE_PORT" --auto-connect &
APP_B_PID=$!

echo "NodeA=$APP_A_PID NodeB=$APP_B_PID base=$BASE_PORT"
echo "LOS toggle: use --los on Node A, or run scripts/virtual_optical_link.py separately."
echo "Ctrl+C to stop both nodes."
wait
