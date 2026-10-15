"""Concrete wrapper around pyserial conforming to ITransport interface."""

from __future__ import annotations

import serial

from ir_terminal.core.interfaces import ITransport
from ir_terminal.protocol.constants import DEFAULT_BAUDRATE


class PySerialTransport(ITransport):
    """Concrete wrapper around pyserial conforming to ITransport interface."""

    def __init__(self, port_name: str, baudrate: int = DEFAULT_BAUDRATE) -> None:
        """Initializes PySerial transport.

        Args:
            port_name: Serial device port identifier (e.g. '/dev/ttyUSB0' or 'COM3').
            baudrate: Baud rate for the physical UART connection.
        """
        self._port_name = port_name
        self._baudrate = baudrate
        self._serial: serial.Serial | None = None

    def open(self) -> None:
        """Opens physical serial port connection."""
        self._serial = serial.Serial(
            port=self._port_name,
            baudrate=self._baudrate,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=0.05,
            write_timeout=0.2,
        )

    def close(self) -> None:
        """Closes physical serial port connection."""
        if self._serial and self._serial.is_open:
            self._serial.close()
        self._serial = None

    @property
    def is_open(self) -> bool:
        """Returns True if physical serial port is currently open."""
        return bool(self._serial and self._serial.is_open)

    def write(self, data: bytes) -> int:
        """Writes binary data to serial interface.

        Args:
            data: Raw bytes to transmit.

        Returns:
            Number of bytes written.
        """
        if not self._serial or not self._serial.is_open:
            raise RuntimeError("Serial port not open for write")
        written = self._serial.write(data)
        return int(written) if written is not None else 0

    def read(self, size: int | None = None) -> bytes:
        """Reads incoming bytes from serial buffer.

        Args:
            size: Number of bytes to read, or None to read all available.

        Returns:
            Extracted bytes sequence.
        """
        if not self._serial or not self._serial.is_open:
            return b""
        read_size = self._serial.in_waiting if size is None else size
        if read_size <= 0:
            return b""
        data = self._serial.read(read_size)
        return bytes(data) if data is not None else b""

    @property
    def in_waiting(self) -> int:
        """Returns number of available bytes in the serial driver ring buffer."""
        if not self._serial or not self._serial.is_open:
            return 0
        waiting = self._serial.in_waiting
        return int(waiting) if waiting is not None else 0
