"""Tầng truyền tải phần cứng hai cổng COM (Dual Hardware COM Transport).

Module này trực tiếp điều khiển giao tiếp nối tiếp vật lý với mạch nhúng:
- COM1 (data_port): Cổng nối tiếp dữ liệu nối tới transceiver quang (MCP2122 + TFDU4101),
  hoạt động ở tốc độ baudrate quang biến thiên (2400, 4800, 9600, 19200 bps, 8-N-1).
- COM2 (ctrl_port): Cổng nối tiếp điều khiển nối tới vi điều khiển STM32F407,
  hoạt động ở tốc độ cố định 115200 bps (8-N-1), chuyên dùng gửi lệnh đổi baud quang ("00\\r" .. "11\\r").
- ResilientWin32Serial: Lớp con mở rộng từ serial.Serial, chủ động dọn lỗi driver và bỏ qua WinError 31
  khi cấu hình DCB trên hệ điều hành Windows.
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
    """Tạm dừng chương trình trong khoảng thời gian xác định (tách riêng hàm để phục vụ mock kiểm thử)."""
    time.sleep(duration)


class _Win32Api(Protocol):
    """Khai báo cấu trúc các hàm Win32 API cấp thấp của hệ điều hành Windows dùng để dọn cổng nối tiếp."""

    PURGE_TXCLEAR: int
    PURGE_TXABORT: int
    PURGE_RXCLEAR: int
    PURGE_RXABORT: int

    DWORD: type[ctypes.c_ulong]
    COMSTAT: type[ctypes.Structure]

    def ClearCommError(self, *args: object) -> int: ...

    def PurgeComm(self, *args: object) -> int: ...


def _is_windows() -> bool:
    """Kiểm tra xem ứng dụng có đang chạy trên hệ điều hành Windows hay không."""
    return sys.platform == "win32"


def _win32_api() -> _Win32Api | None:
    """Truy xuất giao diện Win32 API từ thư viện pyserial khi chạy trên Windows (hoặc None nếu chạy trên Linux/macOS)."""
    if not _is_windows():
        return None
    try:
        import serial.win32 as win32_module
    except Exception:
        logger.warning("%s pyserial Win32 backend unavailable", LogTag.HW_SERIAL)
        return None
    return cast(_Win32Api, win32_module)


def _read_ctypes_ulong(container: object, attribute: str) -> int:
    """Đọc giá trị số nguyên không dấu từ cấu trúc ctypes của Windows (cbInQue hoặc cbOutQue).

    Args:
        container: Thể hiện cấu trúc ctypes hoặc đối tượng mock kiểm thử.
        attribute: Tên trường cần đọc (ví dụ: 'value', 'cbInQue' hoặc 'cbOutQue').

    Returns:
        Giá trị số nguyên đọc được, hoặc 0 nếu không đọc được.
    """
    member = getattr(container, attribute, None)
    if isinstance(member, ctypes.c_ulong):
        return int(member.value)
    if isinstance(member, int):
        return member
    return 0


def _win32_error_flag_names(errors_value: int) -> str:
    """Chuyển đổi mã bit lỗi của Windows thành tên cờ lỗi trực quan (ví dụ: CE_OVERRUN, CE_FRAME).

    Args:
        errors_value: Mã lỗi DWORD trả về từ hàm ClearCommError.

    Returns:
        Chuỗi tên các cờ lỗi phân cách bởi dấu gạch đứng, hoặc 'none' nếu không có lỗi.
    """
    if errors_value == 0:
        return "none"
    names = [name for bit, name in _WIN32_CE_ERROR_FLAGS if errors_value & bit]
    unknown = errors_value & ~_WIN32_CE_KNOWN_MASK
    if unknown:
        names.append(f"UNKNOWN(0x{unknown:04X})")
    return "|".join(names) if names else "none"


def _input_buffer_bytes(handle: serial.Serial) -> int:
    """Đếm số byte đang tồn đọng trong bộ đệm nhận của driver cổng nối tiếp.

    Args:
        handle: Cổng COM đang mở kết nối.

    Returns:
        Số lượng byte đang chờ đọc, hoặc 0 nếu không xác định được.
    """
    try:
        waiting = handle.in_waiting
    except Exception:
        return 0
    return int(waiting) if waiting is not None else 0


def _win32_clear_and_purge_port(port_handle: int | None) -> None:
    """Tự động dọn sạch rác và cờ lỗi trong cổng nối tiếp của Windows trước khi đổi tốc độ để không bị lỗi không mở được cổng.

    Args:
        port_handle: Mã quản lý native handle cổng COM trả về từ hàm CreateFile của Windows.
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
    """Kiểm tra xem lỗi mở cổng COM có thuộc dạng tạm thời và có thể thử lại được hay không (ví dụ: driver bận)."""
    if isinstance(exc, PermissionError):
        return True
    return isinstance(exc, serial.SerialException)


def _is_win32_error_31(exc: BaseException) -> bool:
    """Kiểm tra xem ngoại lệ có phải là lỗi đặc thù WinError 31 (ERROR_GEN_FAILURE) của Windows hay không."""
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
    """Lớp con mở rộng từ serial.Serial tự động dọn sạch rác trong cổng nối tiếp của Windows trước khi đổi tốc độ để không bị lỗi không mở được cổng."""

    def _reconfigure_port(self, force_update: bool = False) -> None:
        """Xóa sạch trạng thái lỗi và hàng đợi comm driver trước khi áp dụng cấu hình DCB (Baudrate/Framing).

        Trên hệ điều hành Windows, các adapter USB-UART thường phát sinh WinError 31 (ERROR_GEN_FAILURE)
        khi thay đổi tham số cổng nếu hàng đợi driver còn sót lỗi (overrun, frame error). Hàm này gọi
        ClearCommError và PurgeComm trực tiếp trên native handle trước khi thực hiện SetCommState.

        Args:
            force_update: Tham số chuyển tiếp cho backend POSIX (Linux/macOS).
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
        """Gọi hàm cấu hình gốc của thư viện pyserial để áp dụng tham số Baudrate và khung truyền 8-N-1."""
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
    """Bộ điều khiển trực tiếp cặp cổng nối tiếp phần cứng COM1 (quang) và COM2 (STM32)."""

    def __init__(
        self,
        data_port: str,
        ctrl_port: str | None = None,
        baudrate: int = DEFAULT_BAUDRATE,
        ctrl_baudrate: int = CONTROL_BAUDRATE,
    ) -> None:
        """Khởi tạo cấu hình cho cặp cổng nối tiếp phần cứng (chưa mở cổng).

        Args:
            data_port: Tên cổng COM1 nối với mạch quang (MCP2122 + TFDU4101).
            ctrl_port: Tên cổng COM2 nối với chân UART của vi điều khiển STM32 (hoặc None nếu không dùng).
            baudrate: Tốc độ Baud quang học áp dụng cho COM1 (2400, 4800, 9600, 19200 bps).
            ctrl_baudrate: Tốc độ Baud cố định của STM32 trên COM2 (115200 bps).
        """
        self._data_port = (data_port or "").strip()
        self._ctrl_port = (ctrl_port or "").strip()
        self._baudrate = int(baudrate)
        self._ctrl_baudrate = int(ctrl_baudrate)
        self._data_serial: serial.Serial | None = None
        self._ctrl_serial: serial.Serial | None = None

    @property
    def data_port(self) -> str:
        """Tên cổng nối tiếp dữ liệu quang COM1."""
        return self._data_port

    @property
    def ctrl_port(self) -> str:
        """Tên cổng nối tiếp điều khiển vi điều khiển STM32 COM2."""
        return self._ctrl_port

    @property
    def tx_port(self) -> str:
        """Tên cổng phát dữ liệu quang (COM1)."""
        return self._data_port

    @property
    def rx_port(self) -> str:
        """Tên cổng thu dữ liệu quang (COM1)."""
        return self._data_port

    @property
    def baudrate(self) -> int:
        """Tốc độ Baud quang hiện hành của kênh dữ liệu COM1."""
        return self._baudrate

    @property
    def control_baudrate(self) -> int:
        """Tốc độ Baud cố định của kênh điều khiển COM2 (115200 bps)."""
        return self._ctrl_baudrate

    @property
    def has_control_port(self) -> bool:
        """Kiểm tra xem có cấu hình cổng điều khiển COM2 riêng cho STM32 hay không."""
        return bool(self._ctrl_port)

    def _create_serial(self, port_name: str, baudrate: int, *, writable: bool) -> serial.Serial:
        """Mở một cổng COM phần cứng chuẩn 8-N-1 (8 bit dữ liệu, không kiểm tra chẵn lẻ, 1 bit dừng).

        Args:
            port_name: Tên cổng COM vật lý (ví dụ: 'COM3' hoặc '/dev/ttyUSB0').
            baudrate: Tốc độ Baud giao tiếp.
            writable: True nếu cổng cần đặt thời gian timeout khi ghi byte.

        Returns:
            Đối tượng cổng nối tiếp đã mở thành công.
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
        """Thực hiện mở cặp cổng COM dữ liệu và điều khiển một lần.

        Returns:
            Cặp đối tượng serial đã mở; cổng điều khiển là None nếu không cấu hình COM2.
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
        """Dọn sạch các byte rác còn sót lại trong bộ đệm ngay sau khi mở cổng thành công.

        Args:
            handle: Đối tượng cổng nối tiếp vừa mở.
            port_name: Tên cổng COM hiển thị trong thông điệp ghi log.
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
        """Mở đồng thời hai cổng nối tiếp COM1 (dữ liệu quang) và COM2 (điều khiển MCU).

        Tự động dọn sạch rác bộ đệm (purge in/out queues) sau khi mở thành công.
        Áp dụng thuật toán thử lại theo cấp số nhân (exponential backoff retry tối đa 3 lần)
        nếu phát hiện lỗi có thể phục hồi từ driver phần cứng.
        """
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
        """Đóng an toàn một cổng nối tiếp và bỏ qua lỗi để không bị rò rỉ tài nguyên hệ thống."""
        if handle is None:
            return
        try:
            if handle.is_open:
                handle.close()
        except Exception:
            logger.exception("%s Dual serial handle close error", LogTag.HW_SERIAL)

    def close(self) -> None:
        """Đóng an toàn cả hai cổng COM1 và COM2."""
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
        """Kiểm tra xem kênh dữ liệu COM1 và kênh điều khiển COM2 (nếu có) có đang mở thông suốt hay không."""
        data_ready = bool(self._data_serial and self._data_serial.is_open)
        if not self.has_control_port:
            return data_ready
        ctrl_ready = bool(self._ctrl_serial and self._ctrl_serial.is_open)
        return data_ready and ctrl_ready

    def write(self, data: bytes) -> int:
        """Ghi dữ liệu byte nhị phân xuống cổng dữ liệu COM1 để phát ra mắt phát quang.

        Args:
            data: Chuỗi byte thô cần truyền đi.

        Returns:
            Số byte thực tế đã đẩy thành công xuống cổng COM.
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
        """Đọc các byte nhận được từ cổng dữ liệu quang COM1.

        Args:
            size: Số lượng byte tối đa cần đọc, hoặc None để đọc toàn bộ byte đang chờ trong buffer.

        Returns:
            Chuỗi byte nhị phân vừa nhặt được từ chân RX.
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
        """Số byte hiện có sẵn trong bộ đệm nhận của cổng dữ liệu quang COM1."""
        if not self._data_serial or not self._data_serial.is_open:
            return 0
        waiting = self._data_serial.in_waiting
        return int(waiting) if waiting is not None else 0

    def flush(self) -> None:
        """Đẩy sạch toàn bộ các byte còn sót trong bộ đệm phát của cổng dữ liệu COM1."""
        if not self._data_serial or not self._data_serial.is_open:
            return
        try:
            self._data_serial.flush()
        except Exception:
            logger.exception("%s Data flush FAILED: port=%s", LogTag.HW_SERIAL, self._data_port)
            raise

    def send_baud_command(self, baud: int) -> str:
        """Gửi lệnh chuyển đổi tốc độ Baud quang sang bo mạch STM32 qua cổng COM2.

        Mã lệnh ASCII 2 ký tự tương ứng:
            - 19200 bps -> "00\\r" (30 30 0D)
            - 9600 bps  -> "01\\r" (30 31 0D)
            - 4800 bps  -> "10\\r" (31 30 0D)
            - 2400 bps  -> "11\\r" (31 31 0D)

        Sau khi ghi lệnh và flush dữ liệu xuống COM2, hàm sẽ tạm dừng 50 ms (BAUD_SWITCH_GUARD_MS)
        để vi điều khiển STM32 kịp chép dữ liệu và chuyển mạch phần cứng trước khi PC đổi tốc độ COM1.

        Args:
            baud: Tốc độ Baud quang học đích (phải thuộc tập 2400, 4800, 9600, 19200 bps).

        Returns:
            Chuỗi 2 ký tự mã lệnh ASCII đã gửi ("00", "01", "10" hoặc "11").

        Raises:
            ValueError: Nếu tốc độ baud không nằm trong danh mục hỗ trợ của firmware.
            RuntimeError: Nếu cổng COM2 chưa được mở kết nối.
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
        """Tái cấu hình tốc độ Baud của kênh dữ liệu quang COM1.

        Thực hiện thay đổi tốc độ in-place trên handle serial đang mở và xóa sạch buffer.
        Nếu gặp lỗi từ driver (ví dụ WinError 31), tự động kích hoạt cơ chế fallback an toàn:
        đóng cổng, trễ 100 ms và mở lại cổng với thuật toán exponential backoff tối đa 3 lần.

        Args:
            new_baud: Tốc độ Baud quang học mới cần áp dụng cho COM1.

        Raises:
            ValueError: Nếu tốc độ baud không được hỗ trợ.
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
        """Đóng cổng COM1 và mở lại ở tốc độ Baud mới nếu việc đổi tốc độ trực tiếp bị driver từ chối.

        Args:
            target_baud: Tốc độ Baud mới cần áp dụng cho COM1.

        Raises:
            Exception: Lỗi cuối cùng nếu tất cả các lần thử mở lại đều thất bại.
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
