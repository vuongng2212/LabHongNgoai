"""Dual-port serial transport separating the TX and RX physical COM ports."""

from __future__ import annotations

import logging

import serial as serial

from ir_terminal.core.interfaces import ITransport
from ir_terminal.protocol.constants import DEFAULT_BAUDRATE
from ir_terminal.utils.logger import LogTag, format_hex_preview

logger = logging.getLogger(__name__)

__all__ = ["DualSerialTransport", "serial"]


class DualSerialTransport(ITransport):
    """ITransport writing to a TX port and reading from a separate RX port."""

    def __init__(
        self,
        tx_port: str,
        rx_port: str | None = None,
        baudrate: int = DEFAULT_BAUDRATE,
    ) -> None:
        """Initializes the dual-port transport without opening any handle.

        Args:
            tx_port: Serial device used for outgoing bytes.
            rx_port: Serial device used for incoming bytes. ``None`` or an empty
                value reuses ``tx_port`` (unified single-port mode).
            baudrate: Baud rate applied to both physical UART links.
        """
        self._tx_port = (tx_port or "").strip()
        requested_rx = (rx_port or "").strip()
        self._rx_port = requested_rx or self._tx_port
        self._baudrate = int(baudrate)
        self._tx_serial: serial.Serial | None = None
        self._rx_serial: serial.Serial | None = None

    @property
    def tx_port(self) -> str:
        """Returns the physical TX port name."""
        return self._tx_port

    @property
    def rx_port(self) -> str:
        """Returns the physical RX port name."""
        return self._rx_port

    @property
    def baudrate(self) -> int:
        """Returns the configured baud rate."""
        return self._baudrate

    @property
    def is_single_port(self) -> bool:
        """Returns True when TX and RX share one serial handle."""
        return self._tx_port == self._rx_port

    def _create_serial(self, port_name: str, *, writable: bool) -> serial.Serial:
        """Builds one 8-N-1 serial handle for the requested direction."""
        return serial.Serial(
            port=port_name,
            baudrate=self._baudrate,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=0,
            write_timeout=1.0 if writable else None,
        )

    def open(self) -> None:
        """Opens the TX handle first and the RX handle second, rolling back on error."""
        if self.is_open:
            self.close()
        logger.info(
            "%s DualSerial open: tx=%s rx=%s baud=%d single=%s (8N1)",
            LogTag.HW_SERIAL,
            self._tx_port,
            self._rx_port,
            self._baudrate,
            self.is_single_port,
        )
        tx_handle: serial.Serial | None = None
        try:
            tx_handle = self._create_serial(self._tx_port, writable=True)
            self._tx_serial = tx_handle
            if self.is_single_port:
                self._rx_serial = tx_handle
            else:
                self._rx_serial = self._create_serial(self._rx_port, writable=False)
        except Exception:
            logger.exception(
                "%s DualSerial open FAILED: tx=%s rx=%s baud=%d",
                LogTag.HW_SERIAL,
                self._tx_port,
                self._rx_port,
                self._baudrate,
            )
            self._close_handle(tx_handle)
            self._tx_serial = None
            self._rx_serial = None
            raise
        logger.info(
            "%s DualSerial opened: tx=%s rx=%s baud=%d is_open=%s",
            LogTag.HW_SERIAL,
            self._tx_port,
            self._rx_port,
            self._baudrate,
            self.is_open,
        )

    def _close_handle(self, handle: serial.Serial | None) -> None:
        """Closes one handle, swallowing errors so no descriptor leaks."""
        if handle is None:
            return
        try:
            if handle.is_open:
                handle.close()
        except Exception:
            logger.exception("%s DualSerial handle close error", LogTag.HW_SERIAL)

    def close(self) -> None:
        """Closes both handles safely, including the unified single-handle case."""
        tx_handle = self._tx_serial
        rx_handle = self._rx_serial
        self._tx_serial = None
        self._rx_serial = None
        self._close_handle(tx_handle)
        if rx_handle is not tx_handle:
            self._close_handle(rx_handle)
        logger.info(
            "%s DualSerial closed: tx=%s rx=%s",
            LogTag.HW_SERIAL,
            self._tx_port,
            self._rx_port,
        )

    @property
    def is_open(self) -> bool:
        """Returns True only when both TX and RX handles are open."""
        tx_ready = bool(self._tx_serial and self._tx_serial.is_open)
        rx_ready = bool(self._rx_serial and self._rx_serial.is_open)
        return tx_ready and rx_ready

    def write(self, data: bytes) -> int:
        """Writes binary data to the TX serial handle.

        Args:
            data: Raw bytes to transmit.

        Returns:
            Number of bytes written.
        """
        if not self._tx_serial or not self._tx_serial.is_open:
            logger.warning(
                "%s DualSerial write rejected (tx closed): tx=%s len=%dB hex=%s",
                LogTag.HW_SERIAL,
                self._tx_port,
                len(data),
                format_hex_preview(data),
            )
            raise RuntimeError("Serial port not open for write")
        try:
            written = self._tx_serial.write(data)
        except Exception:
            logger.exception(
                "%s DualSerial write FAILED: tx=%s len=%dB hex=%s",
                LogTag.HW_SERIAL,
                self._tx_port,
                len(data),
                format_hex_preview(data),
            )
            raise
        count = int(written) if written is not None else 0
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(
                "%s DualSerial TX: tx=%s wrote=%d/%dB hex=%s",
                LogTag.HW_SERIAL,
                self._tx_port,
                count,
                len(data),
                format_hex_preview(data),
            )
        return count

    def read(self, size: int | None = None) -> bytes:
        """Reads incoming bytes from the RX serial handle.

        Args:
            size: Number of bytes to read, or None to read all available.

        Returns:
            Extracted bytes sequence.
        """
        if not self._rx_serial or not self._rx_serial.is_open:
            return b""
        read_size = self._rx_serial.in_waiting if size is None else size
        if read_size <= 0:
            return b""
        try:
            data = self._rx_serial.read(read_size)
        except Exception:
            logger.exception(
                "%s DualSerial read FAILED: rx=%s requested=%dB",
                LogTag.HW_SERIAL,
                self._rx_port,
                read_size,
            )
            raise
        result = bytes(data) if data is not None else b""
        if result and logger.isEnabledFor(logging.DEBUG):
            logger.debug(
                "%s DualSerial RX: rx=%s req=%dB got=%dB hex=%s",
                LogTag.HW_SERIAL,
                self._rx_port,
                read_size,
                len(result),
                format_hex_preview(result),
            )
        return result

    @property
    def in_waiting(self) -> int:
        """Returns number of bytes buffered on the RX serial handle."""
        if not self._rx_serial or not self._rx_serial.is_open:
            return 0
        waiting = self._rx_serial.in_waiting
        return int(waiting) if waiting is not None else 0

    def flush(self) -> None:
        """Flushes the outgoing transmit buffer on the TX serial handle."""
        if not self._tx_serial or not self._tx_serial.is_open:
            return
        try:
            self._tx_serial.flush()
        except Exception:
            logger.exception(
                "%s DualSerial flush FAILED: tx=%s",
                LogTag.HW_SERIAL,
                self._tx_port,
            )
            raise
