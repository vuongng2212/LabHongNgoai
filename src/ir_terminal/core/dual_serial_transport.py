"""Dual-port serial transport separating the TX and RX physical COM ports."""

from __future__ import annotations

import ctypes
import logging
import sys
import time
from collections.abc import Callable
from typing import Protocol, cast

import serial as serial

from ir_terminal.core.interfaces import ITransport
from ir_terminal.protocol.constants import DEFAULT_BAUDRATE
from ir_terminal.utils.logger import LogTag, format_hex_preview

logger = logging.getLogger(__name__)

__all__ = ["DualSerialTransport", "ResilientWin32Serial", "serial"]

MAX_OPEN_RETRIES: int = 3
INITIAL_RETRY_DELAY_SEC: float = 0.05

_WIN32_CE_ERROR_FLAGS: tuple[tuple[int, str], ...] = (
    (0x0001, "CE_RXOVER"),
    (0x0002, "CE_OVERRUN"),
    (0x0004, "CE_RXPARITY"),
    (0x0008, "CE_FRAME"),
    (0x0010, "CE_BREAK"),
    (0x0100, "CE_TXFULL"),
    (0x0200, "CE_PTO"),
    (0x0400, "CE_IOE"),
    (0x0800, "CE_DNS"),
    (0x1000, "CE_OOP"),
    (0x8000, "CE_MODE"),
)
_WIN32_CE_KNOWN_MASK: int = sum(bit for bit, _name in _WIN32_CE_ERROR_FLAGS)


def _sleep(duration: float) -> None:
    """Suspends execution for duration seconds, isolated for test interception."""
    time.sleep(duration)


class _Win32Api(Protocol):
    """Structural type for the subset of pyserial's Win32 surface used here."""

    PURGE_TXCLEAR: int
    PURGE_TXABORT: int
    PURGE_RXCLEAR: int
    PURGE_RXABORT: int

    DWORD: type[ctypes.c_ulong]
    COMSTAT: type[ctypes.Structure]

    def ClearCommError(self, *args: object) -> int: ...

    def PurgeComm(self, *args: object) -> int: ...


def _is_windows() -> bool:
    """Returns True when the process runs on the Windows platform."""
    return sys.platform == "win32"


def _win32_api() -> _Win32Api | None:
    """Returns the pyserial Win32 API facade, or None when unavailable."""
    if not _is_windows():
        return None
    try:
        import serial.win32 as win32_module
    except Exception:
        logger.warning("%s pyserial Win32 backend unavailable", LogTag.HW_SERIAL)
        return None
    return cast(_Win32Api, win32_module)


def _read_ctypes_ulong(container: object, attribute: str) -> int:
    """Reads an unsigned-integer member from a ctypes structure, tolerating mocks.

    Args:
        container: ctypes instance, or a test double, exposing the member.
        attribute: Member name such as ``value``, ``cbInQue`` or ``cbOutQue``.

    Returns:
        Member value, or 0 when the member is absent or unreadable.
    """
    member = getattr(container, attribute, None)
    if isinstance(member, ctypes.c_ulong):
        return int(member.value)
    if isinstance(member, int):
        return member
    return 0


def _win32_error_flag_names(errors_value: int) -> str:
    """Renders a DWORD comm-error bitfield as readable Win32 flag names.

    Args:
        errors_value: Raw ClearCommError error DWORD bitfield.

    Returns:
        Pipe-separated flag names, "none" when no flag is set.
    """
    if errors_value == 0:
        return "none"
    names = [name for bit, name in _WIN32_CE_ERROR_FLAGS if errors_value & bit]
    unknown = errors_value & ~_WIN32_CE_KNOWN_MASK
    if unknown:
        names.append(f"UNKNOWN(0x{unknown:04X})")
    return "|".join(names) if names else "none"


def _input_buffer_bytes(handle: serial.Serial) -> int:
    """Reads the number of bytes queued in the handle driver RX buffer.

    Args:
        handle: Opened pyserial instance.

    Returns:
        Pending RX byte count, or 0 when the count is unavailable.
    """
    try:
        waiting = handle.in_waiting
    except Exception:
        return 0
    return int(waiting) if waiting is not None else 0


def _win32_clear_and_purge_port(port_handle: int | None) -> None:
    """Clears Win32 comm error flags and purges both queues of an open handle.

    Args:
        port_handle: Native handle returned by ``CreateFile`` for a COM port.
    """
    win32 = _win32_api()
    if win32 is None or not port_handle:
        logger.debug(
            "%s Win32 clear/purge skipped: win32=%s handle=%s",
            LogTag.HW_SERIAL,
            win32 is not None,
            port_handle,
        )
        return
    try:
        errors = win32.DWORD()
        comstat = win32.COMSTAT()
        win32.ClearCommError(port_handle, ctypes.byref(errors), ctypes.byref(comstat))
        errors_value = _read_ctypes_ulong(errors, "value")
        input_queue = _read_ctypes_ulong(comstat, "cbInQue")
        output_queue = _read_ctypes_ulong(comstat, "cbOutQue")
        if errors_value:
            logger.warning(
                "%s ClearCommError handle=%d errors=0x%04X flags=[%s] "
                "cbInQue=%d cbOutQue=%d",
                LogTag.HW_SERIAL,
                int(port_handle),
                errors_value,
                _win32_error_flag_names(errors_value),
                input_queue,
                output_queue,
            )
        else:
            logger.debug(
                "%s ClearCommError handle=%d errors=0x0000 flags=[none] "
                "cbInQue=%d cbOutQue=%d",
                LogTag.HW_SERIAL,
                int(port_handle),
                input_queue,
                output_queue,
            )
        win32.PurgeComm(
            port_handle,
            win32.PURGE_TXCLEAR
            | win32.PURGE_TXABORT
            | win32.PURGE_RXCLEAR
            | win32.PURGE_RXABORT,
        )
        logger.debug(
            "%s PurgeComm handle=%d mask=TXCLEAR|TXABORT|RXCLEAR|RXABORT",
            LogTag.HW_SERIAL,
            int(port_handle),
        )
    except Exception as exc:
        logger.warning("%s Win32 clear/purge warning: %s", LogTag.HW_SERIAL, exc)


def _is_retryable_serial_error(exc: BaseException) -> bool:
    """Returns True for pyserial or permission failures worth retrying."""
    if isinstance(exc, PermissionError):
        return True
    return isinstance(exc, serial.SerialException)


def _is_win32_error_31(exc: BaseException) -> bool:
    """Returns True when an exception identifies Win32 ERROR_GEN_FAILURE."""
    if getattr(exc, "winerror", None) == 31:
        return True
    if any(type(argument) is int and argument == 31 for argument in exc.args):
        return True
    message = str(exc).casefold()
    return (
        "a device attached to the system is not functioning" in message
        or "error_gen_failure" in message
        or "winerror 31" in message
        or "error 31" in message
    )


class ResilientWin32Serial(serial.Serial):
    """Serial subclass neutralizing Windows Error 31 before DCB configuration."""

    def _reconfigure_port(self, force_update: bool = False) -> None:
        """Clears driver error state before applying the DCB configuration.

        Args:
            force_update: Forwarded to the POSIX backend, which requires it.
        """
        if not _is_windows():
            self._invoke_base_reconfigure(force_update)
            return

        port_handle = cast(int | None, getattr(self, "_port_handle", None))
        if not port_handle:
            logger.debug(
                "%s DCB reconfigure: no native handle yet, backend defaults apply",
                LogTag.HW_SERIAL,
            )
            self._invoke_base_reconfigure()
            return

        logger.debug(
            "%s DCB reconfigure: clearing driver error state on handle=%d "
            "before SetCommState",
            LogTag.HW_SERIAL,
            port_handle,
        )
        _win32_clear_and_purge_port(port_handle)
        self._invoke_base_reconfigure()

    def _invoke_base_reconfigure(self, *args: object) -> None:
        """Delegates to the platform backend reconfigure routine.

        Args:
            *args: Positional arguments accepted by the platform backend.
        """
        base_method = "_reconfigure_port"
        base_reconfigure = cast(
            Callable[..., None],
            getattr(super(), base_method),
        )
        logger.debug(
            "%s DCB load starting (handle=%r args=%d)",
            LogTag.HW_SERIAL,
            cast(object, getattr(self, "_port_handle", None)),
            len(args),
        )
        try:
            base_reconfigure(*args)
        except Exception as exc:
            if _is_win32_error_31(exc):
                logger.warning(
                    "%s DCB load failed with WinError 31 (tolerated for USB-CDC adapter compatibility): %s",
                    LogTag.HW_SERIAL,
                    exc,
                )
                return
            logger.exception(
                "%s DCB load FAILED (%s: %s)",
                LogTag.HW_SERIAL,
                type(exc).__name__,
                exc,
            )
            raise
        logger.debug(
            "%s DCB load OK (baudrate=%d framing=8N1)",
            LogTag.HW_SERIAL,
            int(getattr(self, "baudrate", 0)),
        )


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
        return self._tx_port.strip().upper() == self._rx_port.strip().upper()

    def _create_serial(self, port_name: str, *, writable: bool) -> serial.Serial:
        """Builds one resilient 8-N-1 serial handle for the requested direction.

        Args:
            port_name: Physical port to open.
            writable: True when the handle needs a finite write timeout.

        Returns:
            Fully opened resilient serial instance.
        """
        serial_instance = ResilientWin32Serial(
            port=None,
            baudrate=self._baudrate,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=0,
            write_timeout=1.0 if writable else None,
            rtscts=False,
            dsrdtr=False,
        )
        logger.debug(
            "%s Serial handle config: port=%s baud=%d framing=8N1 read_timeout=0.0s "
            "write_timeout=%ss rtscts=False dsrdtr=False xonxoff=False force_update=False",
            LogTag.HW_SERIAL,
            port_name,
            self._baudrate,
            cast(float | None, getattr(serial_instance, "write_timeout", None)),
        )
        serial_instance.port = port_name
        try:
            serial_instance.open()
        except Exception as exc:
            logger.exception(
                "%s Serial handle open FAILED: port=%s (%s: %s)",
                LogTag.HW_SERIAL,
                port_name,
                type(exc).__name__,
                exc,
            )
            self._close_handle(serial_instance)
            raise
        logger.debug(
            "%s Serial handle open OK: port=%s baud=%d (handles busy, 8N1 active)",
            LogTag.HW_SERIAL,
            port_name,
            self._baudrate,
        )
        return serial_instance

    def _open_handles_once(self) -> tuple[serial.Serial, serial.Serial]:
        """Attempts a single-pass opening of TX and RX serial handles.

        Returns:
            Pair of open serial handles, identical in unified mode.
        """
        tx_handle: serial.Serial | None = None
        rx_handle: serial.Serial | None = None
        try:
            tx_handle = self._create_serial(self._tx_port, writable=True)
            if self.is_single_port:
                rx_handle = tx_handle
            else:
                rx_handle = self._create_serial(self._rx_port, writable=False)
            return tx_handle, rx_handle
        except Exception:
            self._close_handle(tx_handle)
            if rx_handle is not None and rx_handle is not tx_handle:
                self._close_handle(rx_handle)
            raise

    def _purge_after_open(
        self,
        tx_handle: serial.Serial,
        rx_handle: serial.Serial,
    ) -> None:
        """Drains residual bytes from both handles after a successful open.

        Args:
            tx_handle: Opened TX handle.
            rx_handle: Opened RX handle, possibly the same object.
        """
        try:
            tx_drained = _input_buffer_bytes(tx_handle)
            tx_handle.reset_input_buffer()
            tx_handle.reset_output_buffer()
            logger.debug(
                "%s Post-open purge tx: port=%s drained=%dB (in+out buffers cleared)",
                LogTag.HW_SERIAL,
                self._tx_port,
                tx_drained,
            )
            if rx_handle is tx_handle:
                logger.debug(
                    "%s Post-open purge rx: port=%s shared tx handle, nothing extra drained",
                    LogTag.HW_SERIAL,
                    self._rx_port,
                )
                return
            rx_drained = _input_buffer_bytes(rx_handle)
            rx_handle.reset_input_buffer()
            rx_handle.reset_output_buffer()
            logger.debug(
                "%s Post-open purge rx: port=%s drained=%dB (in+out buffers cleared)",
                LogTag.HW_SERIAL,
                self._rx_port,
                rx_drained,
            )
        except Exception as exc:
            logger.warning(
                "%s Post-open buffer purge warning: %s",
                LogTag.HW_SERIAL,
                exc,
            )

    def open(self) -> None:
        """Opens serial handles with bounded retry and hardware error recovery."""
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

        last_error: Exception | None = None
        for attempt in range(MAX_OPEN_RETRIES):
            logger.debug(
                "%s DualSerial open attempt %d/%d starting: tx=%s rx=%s",
                LogTag.HW_SERIAL,
                attempt + 1,
                MAX_OPEN_RETRIES,
                self._tx_port,
                self._rx_port,
            )
            try:
                tx_handle, rx_handle = self._open_handles_once()
                self._tx_serial = tx_handle
                self._rx_serial = rx_handle
                self._purge_after_open(tx_handle, rx_handle)
                logger.info(
                    "%s DualSerial opened: tx=%s rx=%s baud=%d is_open=%s",
                    LogTag.HW_SERIAL,
                    self._tx_port,
                    self._rx_port,
                    self._baudrate,
                    self.is_open,
                )
                return
            except Exception as exc:
                last_error = exc
                self._close_handle(self._tx_serial)
                if self._rx_serial is not self._tx_serial:
                    self._close_handle(self._rx_serial)
                self._tx_serial = None
                self._rx_serial = None
                if attempt < MAX_OPEN_RETRIES - 1 and _is_retryable_serial_error(exc):
                    delay = INITIAL_RETRY_DELAY_SEC * (2**attempt)
                    logger.warning(
                        "%s DualSerial open attempt %d/%d FAILED (%s: %s), "
                        "retrying in %.0fms",
                        LogTag.HW_SERIAL,
                        attempt + 1,
                        MAX_OPEN_RETRIES,
                        type(exc).__name__,
                        exc,
                        delay * 1000,
                    )
                    _sleep(delay)
                    continue
                logger.exception(
                    "%s DualSerial open attempt %d/%d FAILED permanently "
                    "(%s: %s), no retry",
                    LogTag.HW_SERIAL,
                    attempt + 1,
                    MAX_OPEN_RETRIES,
                    type(exc).__name__,
                    exc,
                )
                break

        if last_error is not None:
            raise last_error

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
