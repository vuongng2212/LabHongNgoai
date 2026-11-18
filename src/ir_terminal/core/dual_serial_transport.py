"""Dual hardware COM transport with separate data and control channels.

COM1 (``data_port``) is wired to the IR transceiver and runs at the optical
baudrate. COM2 (``ctrl_port``) is wired to the STM32 MCU and always runs at
115200 bps, carrying the two-character baud switch commands defined by the
firmware contract.
"""

from __future__ import annotations

import ctypes
import logging
import sys
import time
from collections.abc import Callable
from typing import Protocol, cast

import serial as serial

from ir_terminal.core.interfaces import ITransport
from ir_terminal.protocol.constants import (
    BAUD_COMMAND_TERMINATOR,
    BAUD_COMMANDS,
    BAUD_SWITCH_GUARD_MS,
    CONTROL_BAUDRATE,
    DEFAULT_BAUDRATE,
)
from ir_terminal.utils.logger import LogTag, format_hex_preview

logger = logging.getLogger(__name__)

__all__ = ["DualSerialTransport", "ResilientWin32Serial", "serial"]

MAX_OPEN_RETRIES: int = 3
INITIAL_RETRY_DELAY_SEC: float = 0.05
PORT_RELEASE_DELAY_SEC: float = 0.1

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
                "%s ClearCommError handle=%d errors=0x%04X flags=[%s] cbInQue=%d cbOutQue=%d",
                LogTag.HW_SERIAL,
                int(port_handle),
                errors_value,
                _win32_error_flag_names(errors_value),
                input_queue,
                output_queue,
            )
        else:
            logger.debug(
                "%s ClearCommError handle=%d errors=0x0000 flags=[none] cbInQue=%d cbOutQue=%d",
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
            "%s DCB reconfigure: clearing driver error state on handle=%d before SetCommState",
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
                    "%s DCB load failed with WinError 31 (tolerated for USB-CDC "
                    "adapter compatibility): %s",
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
    """ITransport bound to COM1 data and COM2 control hardware channels."""

    def __init__(
        self,
        data_port: str,
        ctrl_port: str | None = None,
        baudrate: int = DEFAULT_BAUDRATE,
        ctrl_baudrate: int = CONTROL_BAUDRATE,
    ) -> None:
        """Initializes the dual-port transport without opening any handle.

        Args:
            data_port: COM1 device wired to the infrared transceiver.
            ctrl_port: COM2 device wired to the STM32 controller. ``None`` or an
                empty value disables control commands.
            baudrate: Optical baudrate applied to the COM1 data channel.
            ctrl_baudrate: Fixed MCU baudrate applied to the COM2 channel.
        """
        self._data_port = (data_port or "").strip()
        self._ctrl_port = (ctrl_port or "").strip()
        self._baudrate = int(baudrate)
        self._ctrl_baudrate = int(ctrl_baudrate)
        self._data_serial: serial.Serial | None = None
        self._ctrl_serial: serial.Serial | None = None

    @property
    def data_port(self) -> str:
        """Returns the COM1 data port name."""
        return self._data_port

    @property
    def ctrl_port(self) -> str:
        """Returns the COM2 control port name, or an empty string."""
        return self._ctrl_port

    @property
    def tx_port(self) -> str:
        """Returns the data port name for legacy TX-oriented callers."""
        return self._data_port

    @property
    def rx_port(self) -> str:
        """Returns the data port name for legacy RX-oriented callers."""
        return self._data_port

    @property
    def baudrate(self) -> int:
        """Returns the active COM1 optical baudrate."""
        return self._baudrate

    @property
    def control_baudrate(self) -> int:
        """Returns the fixed COM2 MCU baudrate."""
        return self._ctrl_baudrate

    @property
    def has_control_port(self) -> bool:
        """Returns True when a dedicated COM2 control port is configured."""
        return bool(self._ctrl_port)

    def _create_serial(self, port_name: str, baudrate: int, *, writable: bool) -> serial.Serial:
        """Builds one resilient 8-N-1 serial handle.

        Args:
            port_name: Physical port to open.
            baudrate: Baudrate applied to the handle.
            writable: True when the handle needs a finite write timeout.

        Returns:
            Fully opened resilient serial instance.
        """
        serial_instance = ResilientWin32Serial(
            port=None,
            baudrate=baudrate,
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
            "write_timeout=%ss rtscts=False dsrdtr=False xonxoff=False",
            LogTag.HW_SERIAL,
            port_name,
            baudrate,
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
            "%s Serial handle open OK: port=%s baud=%d (8N1 active)",
            LogTag.HW_SERIAL,
            port_name,
            baudrate,
        )
        return serial_instance

    def _open_handles_once(self) -> tuple[serial.Serial, serial.Serial | None]:
        """Attempts a single-pass opening of the data and control handles.

        Returns:
            Pair of opened handles; the control handle is None when disabled.
        """
        data_handle: serial.Serial | None = None
        ctrl_handle: serial.Serial | None = None
        try:
            data_handle = self._create_serial(self._data_port, self._baudrate, writable=True)
            if self.has_control_port:
                ctrl_handle = self._create_serial(
                    self._ctrl_port, self._ctrl_baudrate, writable=True
                )
            return data_handle, ctrl_handle
        except Exception:
            self._close_handle(data_handle)
            self._close_handle(ctrl_handle)
            raise

    def _purge_handle(self, handle: serial.Serial, port_name: str) -> None:
        """Drains residual bytes from one handle after a successful open.

        Args:
            handle: Opened serial handle.
            port_name: Human-readable port name used in logs.
        """
        try:
            drained = _input_buffer_bytes(handle)
            handle.reset_input_buffer()
            handle.reset_output_buffer()
            logger.debug(
                "%s Post-open purge: port=%s drained=%dB (in+out buffers cleared)",
                LogTag.HW_SERIAL,
                port_name,
                drained,
            )
        except Exception as exc:
            logger.warning("%s Post-open buffer purge warning: %s", LogTag.HW_SERIAL, exc)

    def open(self) -> None:
        """Opens COM1 and COM2 with bounded retry and hardware error recovery."""
        if self.is_open:
            self.close()

        logger.info(
            "%s Dual serial open: data=%s @%d rx=%s @%d (8N1)",
            LogTag.HW_SERIAL,
            self._data_port,
            self._baudrate,
            self._ctrl_port or "<none>",
            self._ctrl_baudrate,
        )

        last_error: Exception | None = None
        for attempt in range(MAX_OPEN_RETRIES):
            try:
                data_handle, ctrl_handle = self._open_handles_once()
                self._data_serial = data_handle
                self._ctrl_serial = ctrl_handle
                self._purge_handle(data_handle, self._data_port)
                if ctrl_handle is not None:
                    self._purge_handle(ctrl_handle, self._ctrl_port)
                logger.info(
                    "%s Dual serial opened: data=%s ctrl=%s baud=%d ctrl_baud=%d",
                    LogTag.HW_SERIAL,
                    self._data_port,
                    self._ctrl_port or "<none>",
                    self._baudrate,
                    self._ctrl_baudrate,
                )
                return
            except Exception as exc:
                last_error = exc
                self._close_handle(self._data_serial)
                self._close_handle(self._ctrl_serial)
                self._data_serial = None
                self._ctrl_serial = None
                if attempt < MAX_OPEN_RETRIES - 1 and _is_retryable_serial_error(exc):
                    delay = INITIAL_RETRY_DELAY_SEC * (2**attempt)
                    logger.warning(
                        "%s Dual serial open attempt %d/%d FAILED (%s: %s), retrying in %.0fms",
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
                    "%s Dual serial open attempt %d/%d FAILED permanently (%s: %s)",
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
            logger.exception("%s Dual serial handle close error", LogTag.HW_SERIAL)

    def close(self) -> None:
        """Closes both handles safely and idempotently."""
        data_handle = self._data_serial
        ctrl_handle = self._ctrl_serial
        self._data_serial = None
        self._ctrl_serial = None
        self._close_handle(data_handle)
        self._close_handle(ctrl_handle)
        logger.info(
            "%s Dual serial closed: data=%s ctrl=%s",
            LogTag.HW_SERIAL,
            self._data_port,
            self._ctrl_port or "<none>",
        )

    @property
    def is_open(self) -> bool:
        """Returns True when the data channel and any configured control channel are open."""
        data_ready = bool(self._data_serial and self._data_serial.is_open)
        if not self.has_control_port:
            return data_ready
        ctrl_ready = bool(self._ctrl_serial and self._ctrl_serial.is_open)
        return data_ready and ctrl_ready

    def write(self, data: bytes) -> int:
        """Writes binary data to the COM1 data channel.

        Args:
            data: Raw bytes to transmit.

        Returns:
            Number of bytes written.
        """
        if not self._data_serial or not self._data_serial.is_open:
            logger.warning(
                "%s Data write rejected (closed): port=%s len=%dB hex=%s",
                LogTag.HW_SERIAL,
                self._data_port,
                len(data),
                format_hex_preview(data),
            )
            raise RuntimeError("Serial port not open for write")
        try:
            written = self._data_serial.write(data)
        except Exception:
            logger.exception(
                "%s Data write FAILED: port=%s len=%dB hex=%s",
                LogTag.HW_SERIAL,
                self._data_port,
                len(data),
                format_hex_preview(data),
            )
            raise
        count = int(written) if written is not None else 0
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(
                "%s Data TX: port=%s wrote=%d/%dB hex=%s",
                LogTag.HW_SERIAL,
                self._data_port,
                count,
                len(data),
                format_hex_preview(data),
            )
        return count

    def read(self, size: int | None = None) -> bytes:
        """Reads incoming bytes from the COM1 data channel.

        Args:
            size: Number of bytes to read, or None to read all available.

        Returns:
            Extracted byte sequence.
        """
        if not self._data_serial or not self._data_serial.is_open:
            return b""
        read_size = self._data_serial.in_waiting if size is None else size
        if read_size <= 0:
            return b""
        try:
            data = self._data_serial.read(read_size)
        except Exception:
            logger.exception(
                "%s Data read FAILED: port=%s requested=%dB",
                LogTag.HW_SERIAL,
                self._data_port,
                read_size,
            )
            raise
        result = bytes(data) if data is not None else b""
        if result and logger.isEnabledFor(logging.DEBUG):
            logger.debug(
                "%s Data RX: port=%s req=%dB got=%dB hex=%s",
                LogTag.HW_SERIAL,
                self._data_port,
                read_size,
                len(result),
                format_hex_preview(result),
            )
        return result

    @property
    def in_waiting(self) -> int:
        """Returns the number of bytes buffered on the COM1 data channel."""
        if not self._data_serial or not self._data_serial.is_open:
            return 0
        waiting = self._data_serial.in_waiting
        return int(waiting) if waiting is not None else 0

    def flush(self) -> None:
        """Flushes the outgoing transmit buffer on the data channel."""
        if not self._data_serial or not self._data_serial.is_open:
            return
        try:
            self._data_serial.flush()
        except Exception:
            logger.exception("%s Data flush FAILED: port=%s", LogTag.HW_SERIAL, self._data_port)
            raise

    def send_baud_command(self, baud: int) -> str:
        """Sends the STM32 baud switch command over COM2.

        Args:
            baud: Target optical baudrate. Must be one of 2400, 4800, 9600 or
                19200.

        Returns:
            The two-character command code that was transmitted.

        Raises:
            ValueError: If the requested baudrate has no firmware command.
            RuntimeError: If the control port is not open.
        """
        code = BAUD_COMMANDS.get(int(baud))
        if code is None:
            raise ValueError(f"Unsupported optical baudrate {baud}; expected one of {sorted(BAUD_COMMANDS)}")
        if not self._ctrl_serial or not self._ctrl_serial.is_open:
            raise RuntimeError("Control port is not open for baud commands")
        payload = code.encode("ascii") + BAUD_COMMAND_TERMINATOR
        written = self._ctrl_serial.write(payload)
        self._ctrl_serial.flush()
        logger.info(
            "%s COM2 baud command: code=%r target=%d written=%s guard=%dms",
            LogTag.BAUD_CTRL,
            code,
            baud,
            written,
            BAUD_SWITCH_GUARD_MS,
        )
        _sleep(BAUD_SWITCH_GUARD_MS / 1000.0)
        return code

    def reconfigure_data_baud(self, new_baud: int) -> None:
        """Updates the COM1 baudrate in place with bounded reopen fallback.

        Args:
            new_baud: Target optical baudrate.

        Raises:
            ValueError: If the requested baudrate has no firmware command.
        """
        target = int(new_baud)
        if target not in BAUD_COMMANDS:
            raise ValueError(f"Unsupported optical baudrate {target}")
        if not self._data_serial or not self._data_serial.is_open:
            self._baudrate = target
            logger.info(
                "%s COM1 baudrate stored while closed: %d",
                LogTag.BAUD_CTRL,
                target,
            )
            return
        previous = self._baudrate
        try:
            self._data_serial.baudrate = target
            self._data_serial.reset_input_buffer()
            self._data_serial.reset_output_buffer()
            self._baudrate = target
            logger.info(
                "%s COM1 in-place reconfigure: %d -> %d bps (queues purged)",
                LogTag.BAUD_CTRL,
                previous,
                target,
            )
            return
        except Exception as exc:
            if not _is_retryable_serial_error(exc):
                raise
            logger.warning(
                "%s COM1 in-place reconfigure FAILED (%s: %s); rebuilding handle",
                LogTag.BAUD_CTRL,
                type(exc).__name__,
                exc,
            )
        self._reopen_data_port(target)
        logger.info(
            "%s COM1 reopen reconfigure: %d -> %d bps",
            LogTag.BAUD_CTRL,
            previous,
            target,
        )

    def _reopen_data_port(self, target_baud: int) -> None:
        """Closes and reopens COM1 at the target rate using bounded backoff.

        Args:
            target_baud: Baudrate applied to the reopened data handle.

        Raises:
            Exception: The last reopen failure when every attempt fails.
        """
        self._close_handle(self._data_serial)
        self._data_serial = None
        self._baudrate = target_baud
        _sleep(PORT_RELEASE_DELAY_SEC)
        last_error: Exception | None = None
        for attempt in range(MAX_OPEN_RETRIES):
            try:
                handle = self._create_serial(self._data_port, target_baud, writable=True)
                self._data_serial = handle
                self._purge_handle(handle, self._data_port)
                return
            except Exception as exc:
                last_error = exc
                if attempt < MAX_OPEN_RETRIES - 1 and _is_retryable_serial_error(exc):
                    _sleep(INITIAL_RETRY_DELAY_SEC * (2**attempt))
                    continue
                break
        if last_error is not None:
            raise last_error
