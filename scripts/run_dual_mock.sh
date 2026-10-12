#!/usr/bin/env bash
# Dual-mock launcher: 1 broker + 2 app instances on the same PC.
# Usage: ./scripts/run_dual_mock.sh [--loss 0.0] [--latency-ms 5]
# Then in App A connect LINK_A, in App B connect LINK_B and test Ping/Chat/File.
set -euo pipefail
cd "$(dirname "$0")/.."

export IR_ENABLE_DUAL_MOCK=1

cleanup() {
  echo "Stopping dual-mock processes..."
  kill "$BROKER_PID" "$APP_A_PID" "$APP_B_PID" 2>/dev/null || true
}
trap cleanup INT TERM EXIT

echo "=== Starting virtual optical link broker (6001=A, 6002=B) ==="
uv run python scripts/virtual_optical_link.py "$@" &
BROKER_PID=$!

echo "Waiting for broker ports 6001/6002 (uv cold start can take several seconds)..."
for i in $(seq 1 60); do
  if uv run python -c "import socket; socket.create_connection(('127.0.0.1', 6001), timeout=1).close(); socket.create_connection(('127.0.0.1', 6002), timeout=1).close()" 2>/dev/null; then
    echo "Broker is listening."
    break
  fi
  if [ "$i" -eq 60 ]; then
    echo "ERROR: broker did not open ports 6001/6002 after 60s. Check broker log above."
    exit 1
  fi
  sleep 1
done

echo "=== Starting App A (instance A) ==="
IR_INSTANCE=A IR_ENABLE_DUAL_MOCK=1 uv run python -m ir_terminal --instance A &
APP_A_PID=$!

echo "=== Starting App B (instance B) ==="
IR_INSTANCE=B IR_ENABLE_DUAL_MOCK=1 uv run python -m ir_terminal --instance B &
APP_B_PID=$!

echo "Broker=$BROKER_PID AppA=$APP_A_PID AppB=$APP_B_PID"
echo "IMPORTANT ORDER:"
echo "  1. Wait until broker logs 'Waiting for App A/B' (above)."
echo "  2. In App A: Scan ports -> connect LINK_A. Wait for 'Connected' header."
echo "  3. In App B: Scan ports -> connect LINK_B. Wait for 'Connected' header."
echo "  4. Only then press Ping/Chat/Transmit. Pressing them before Connect"
echo "     gives 'Cannot send data: Serial port is not connected'."
echo "Type 'b' + Enter in the broker terminal to toggle LOS blockage."
echo "Ctrl+C to stop all."
wait
