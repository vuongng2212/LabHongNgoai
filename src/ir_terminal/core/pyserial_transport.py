"""Concrete wrapper around pyserial conforming to ITransport interface."""

from __future__ import annotations

import logging

import serial

from ir_terminal.core.interfaces import ITransport
from ir_terminal.protocol.constants import DEFAULT_BAUDRATE
from ir_terminal.utils.logger import LogTag, format_hex_preview

logger = logging.getLogger(__name__)


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
        logger.info(
            "%s PySerial open: port=%s baud=%d (8N1, timeout=0.05s, write_timeout=0.2s)",
            LogTag.HW_SERIAL,
            self._port_name,
            self._baudrate,
        )
        try:
            self._serial = serial.Serial(
                port=self._port_name,
                baudrate=self._baudrate,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=0.05,
                write_timeout=0.2,
            )
        except Exception:
            logger.exception(
                "%s PySerial open FAILED: port=%s baud=%d",
                LogTag.HW_SERIAL,
                self._port_name,
                self._baudrate,
            )
            raise
        logger.info(
            "%s PySerial opened: port=%s baud=%d is_open=%s",
            LogTag.HW_SERIAL,
            self._port_name,
            self._baudrate,
            self._serial.is_open,
        )

    def close(self) -> None:
        """Closes physical serial port connection."""
        was_open = bool(self._serial and self._serial.is_open)
        if self._serial and self._serial.is_open:
            try:
                self._serial.close()
            except Exception:
                logger.exception(
                    "%s PySerial close error: port=%s", LogTag.HW_SERIAL, self._port_name
                )
                raise
            finally:
                self._serial = None
        else:
            self._serial = None
        logger.info(
            "%s PySerial closed: port=%s was_open=%s",
            LogTag.HW_SERIAL,
            self._port_name,
            was_open,
        )

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
            logger.warning(
                "%s PySerial write rejected (port closed): port=%s len=%dB hex=%s",
                LogTag.HW_SERIAL,
                self._port_name,
                len(data),
                format_hex_preview(data),
            )
            raise RuntimeError("Serial port not open for write")
        try:
            written = self._serial.write(data)
        except Exception:
            logger.exception(
                "%s PySerial write FAILED: port=%s len=%dB hex=%s",
                LogTag.HW_SERIAL,
                self._port_name,
                len(data),
                format_hex_preview(data),
            )
            raise
        count = int(written) if written is not None else 0
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(
                "%s PySerial TX: port=%s wrote=%d/%dB hex=%s",
                LogTag.HW_SERIAL,
                self._port_name,
                count,
                len(data),
                format_hex_preview(data),
            )
        return count

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
        try:
            data = self._serial.read(read_size)
        except Exception:
            logger.exception(
                "%s PySerial read FAILED: port=%s requested=%dB",
                LogTag.HW_SERIAL,
                self._port_name,
                read_size,
            )
            raise
        result = bytes(data) if data is not None else b""
        if result and logger.isEnabledFor(logging.DEBUG):
            logger.debug(
                "%s PySerial RX: port=%s req=%dB got=%dB hex=%s",
                LogTag.HW_SERIAL,
                self._port_name,
                read_size,
                len(result),
                format_hex_preview(result),
            )
        return result

    @property
    def in_waiting(self) -> int:
        """Returns number of available bytes in the serial driver ring buffer."""
        if not self._serial or not self._serial.is_open:
            return 0
        waiting = self._serial.in_waiting
        return int(waiting) if waiting is not None else 0

    def flush(self) -> None:
        """Flushes the outgoing serial write buffer."""
        if self._serial and self._serial.is_open:
            self._serial.flush()
