"""Abstract Base Classes and Protocol contracts for hardware I/O and transport."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable


class ITransport(ABC):
    """Abstract interface for byte stream transport (Serial / Virtual COM / Mock)."""

    @property
    def poll_interval_ms(self) -> int:
        """Returns the RX polling interval in milliseconds for this transport."""
        return 10

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

    @abstractmethod
    def flush(self) -> None:
        """Flushes the outgoing transmit buffer to the physical medium."""

    def send_baud_command(self, baud: int) -> str:
        """Sends a baud switch command over the control channel.

        Args:
            baud: Target optical baudrate.

        Returns:
            The two-character command code accepted by the firmware.

        Raises:
            NotImplementedError: When the transport has no control channel.
        """
        raise NotImplementedError(f"{type(self).__name__} does not expose a control channel")

    def reconfigure_data_baud(self, baud: int) -> None:
        """Updates the optical data channel baudrate.

        Args:
            baud: Target optical baudrate.

        Raises:
            NotImplementedError: When the transport cannot reconfigure its data rate.
        """
        raise NotImplementedError(f"{type(self).__name__} cannot reconfigure the data baudrate")

    on_link_lost: Callable[[], None] | None = None
