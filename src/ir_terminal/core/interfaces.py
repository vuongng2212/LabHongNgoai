"""Abstract Base Classes and Protocol contracts for hardware I/O and transport."""

from __future__ import annotations

from abc import ABC, abstractmethod


class ITransport(ABC):
    """Abstract interface for byte stream transport (Serial / Virtual COM / Mock)."""

    @abstractmethod
    def open(self) -> None:
        """Opens the transport medium."""

    @abstractmethod
    def close(self) -> None:
        """Closes the transport medium."""

    @property
    @abstractmethod
    def is_open(self) -> bool:
        """Returns True if the transport connection is currently active."""

    @abstractmethod
    def write(self, data: bytes) -> int:
        """Writes binary data to the transport medium.

        Args:
            data: Raw bytes to transmit.

        Returns:
            Number of bytes written.
        """

    @abstractmethod
    def read(self, size: int | None = None) -> bytes:
        """Reads up to size bytes from the transport buffer.

        Args:
            size: Maximum number of bytes to read, or None to read all available.

        Returns:
            Bytes read from the buffer.
        """

    @property
    @abstractmethod
    def in_waiting(self) -> int:
        """Returns number of bytes currently available to read."""
