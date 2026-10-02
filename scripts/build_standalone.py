#!/usr/bin/env python3
"""Standalone desktop binary build script using PyInstaller for IR-OWC Lab Terminal."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def build() -> int:
    """Executes PyInstaller build process and verifies output binary directory.

    Returns:
        0 on success, non-zero on failure.
    """
    root_dir = Path(__file__).resolve().parent.parent
    spec_path = root_dir / "ir_terminal.spec"

    print("=" * 70)
    print("Building Standalone IR-OWC Lab Terminal Desktop Application")
    print(f"Project root: {root_dir}")
    print(f"Spec file:    {spec_path}")
    print("=" * 70)

    if not spec_path.exists():
        print(f"Error: Specification file not found at {spec_path}", file=sys.stderr)
        return 1

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        str(spec_path),
    ]

    print(f"Running command: {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=root_dir)

    if result.returncode != 0:
        print(f"PyInstaller build failed with return code {result.returncode}", file=sys.stderr)
        return result.returncode

    dist_dir = root_dir / "dist" / "IR_OWC_Lab_Terminal"
    executable_path = dist_dir / "IR_OWC_Lab_Terminal"

    if not dist_dir.exists():
        print(f"Error: Output directory {dist_dir} was not created!", file=sys.stderr)
        return 1

    print("=" * 70)
    print("Build completed successfully!")
    print(f"Distribution directory: {dist_dir}")
    if executable_path.exists():
        print(f"Executable binary:      {executable_path}")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(build())
