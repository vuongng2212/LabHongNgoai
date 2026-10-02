#!/usr/bin/env bash
set -e

# Change to repository root
cd "$(dirname "$0")/.."

echo "=== Building IR-OWC Lab Terminal Standalone Executable ==="
uv run python scripts/build_standalone.py
