"""Bounded-memory file operations for streaming transfer sessions."""

from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path
from typing import BinaryIO


class ChunkBuffer:
    """Reads TX slices and manages ordered RX staging files."""

    @staticmethod
    def read_at(handle: BinaryIO, offset: int, size: int) -> bytes:
        """Reads one bounded TX slice at an absolute offset."""
        handle.seek(offset)
        return handle.read(size)

    @staticmethod
    def create_staging_file(directory: Path) -> tuple[BinaryIO, Path]:
        """Creates an exclusive temporary receive file inside its destination."""
        directory.mkdir(parents=True, exist_ok=True)
        descriptor, temp_name = tempfile.mkstemp(prefix=".ir_rx_", suffix=".part", dir=directory)
        return os.fdopen(descriptor, "wb"), Path(temp_name)

    @staticmethod
    def append(handle: BinaryIO, chunk: bytes) -> None:
        """Appends and flushes one ordered chunk to the staging file."""
        handle.write(chunk)
        handle.flush()

    @staticmethod
    def publish(temp_path: Path, directory: Path, filename: str) -> Path:
        """Atomically publishes a verified staging file, suffixing collisions."""
        safe_name = Path(filename).name or "received_file.bin"
        if safe_name in (".", ".."):
            safe_name = "received_file.bin"
        destination = directory / safe_name
        if destination.exists():
            destination = directory / (f"{destination.stem}_{int(time.time())}{destination.suffix}")
        os.replace(temp_path, destination)
        return destination
