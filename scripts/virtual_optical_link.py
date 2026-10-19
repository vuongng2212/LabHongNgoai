"""Standalone CLI wrapper for the in-app virtual optical link broker.

The implementation now lives in :mod:`ir_terminal.core.link_broker` so it is
bundled into the packaged ``.exe`` (the host node starts it in-process). This
script remains for development workflows that run the broker separately.

Usage:
    uv run python scripts/virtual_optical_link.py [--loss 0.0] [--latency-ms 5]

Type ``b`` + Enter on stdin to toggle LOS blockage (emulates a blocked beam).
"""

from __future__ import annotations

from ir_terminal.core.link_broker import LinkBroker, VirtualOpticalLink, main

__all__ = ["LinkBroker", "VirtualOpticalLink", "main"]


if __name__ == "__main__":
    raise SystemExit(main())
