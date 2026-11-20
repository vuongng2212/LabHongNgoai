"""Luồng làm việc nền xử lý cổng nối tiếp bất đồng bộ (Serial Worker).

QObject Worker chạy trên một QThread chuyên biệt, chịu trách nhiệm:
1. Giao tiếp đọc/ghi bất đồng bộ (non-blocking) với cặp cổng COM1 (dữ liệu quang) và COM2 (điều khiển STM32).
2. Chu kỳ định thời (QTimer polling 10 ms) để liên tục kiểm tra và trích xuất dữ liệu từ UART.
3. Tích lũy dòng byte nhận, tự động giải mã các khung truyền WinForm C# và phát tín hiệu cho UI/Services.
4. Giám sát cắm/rút thiết bị phần cứng (Hotplug Port Watcher) và quản lý chuyển đổi Baudrate.
"""

from __future__ import annotations

import importlib
import logging
import re
from dataclasses import dataclass
from typing import ClassVar

from PySide6.QtCore import QObject, QTimer, Signal, Slot
from serial.tools import list_ports

from ir_terminal.core.interfaces import ITransport
from ir_terminal.core.transport_factory import create_transport
from ir_terminal.protocol.constants import (
    CONTROL_BAUDRATE,
    DEFAULT_BAUDRATE,
    RING_BUFFER_CAPACITY,
    SOCKET_CONNECT_TIMEOUT_SEC,
)
from ir_terminal.protocol.framer import parse_stream
from ir_terminal.utils.logger import LogTag, format_hex_preview
from ir_terminal.utils.path_helpers import is_dev_mode, is_dual_mock_enabled

logger = logging.getLogger(__name__)


def _natural_port_key(name: str) -> str:
    """Tạo khóa sắp xếp tự nhiên theo thứ tự số học (ví dụ: COM2 đứng trước COM10)."""
    return "".join(
        f"{int(part):08d}" if part.isdigit() else part.lower() for part in re.split(r"(\d+)", name)
    )


def _windows_registry_ports() -> list[str]:
    """Truy vấn danh sách cổng COM đã đăng ký trong Registry Windows (khóa SERIALCOMM)."""
    try:
        winreg = importlib.import_module("winreg")
    except ImportError:
        return []
    ports: list[str] = []
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DEVICEMAP\SERIALCOMM") as key:
            index = 0
            while True:
                try:
                    _name, value, _kind = winreg.EnumValue(key, index)
                except OSError:
                    break
                if value:
                    ports.append(str(value))
                index += 1
    except OSError:
        return []
    return ports


@dataclass(slots=True)
class PortInfo:
    """Siêu dữ liệu mô tả chi tiết một cổng nối tiếp khả dụng trong hệ thống."""

    device: str
    description: str
    manufacturer: str | None
    vid: str | None
    pid: str | None
    hwid: str
    label: str

    def to_dict(self) -> dict[str, str | None]:
        """Tuần tự hóa thông tin cổng nối tiếp thành từ điển (dictionary) chuẩn JSON."""
        return {
            "device": self.device,
            "description": self.description,
            "manufacturer": self.manufacturer,
            "vid": self.vid,
            "pid": self.pid,
            "hwid": self.hwid,
            "label": self.label,
        }


def _format_hex_identifier(value: int | None) -> str | None:
    """Định dạng mã định danh USB (VID/PID) thành chuỗi thập lục phân 4 ký tự viết hoa."""
    if value is None:
        return None
    return f"{int(value) & 0xFFFF:04X}"


def _friendly_label(device: str, description: str, vid: str | None, pid: str | None) -> str:
    """Tạo nhãn hiển thị trực quan, thân thiện cho cổng nối tiếp phần cứng."""
    if vid and pid:
        return f"{description or device} [{vid}:{pid}]"
    if description:
        return f"{device} - {description}"
    return device


def _virtual_port_info(device: str, description: str) -> PortInfo:
    """Tạo đối tượng PortInfo đại diện cho một cổng nối tiếp ảo chạy trong bộ nhớ."""
    return PortInfo(
        device=device,
        description=description,
        manufacturer=None,
        vid=None,
        pid=None,
        hwid="",
        label=f"{device} ({description})",
    )


class SerialWorker(QObject):
    """Worker chạy trên QThread nền chịu trách nhiệm I/O COM1/COM2 và giải mã khung WinForm."""

    connected: ClassVar[Signal] = Signal(str, int)
    ports_connected: ClassVar[Signal] = Signal(str, str, int)
    disconnected: ClassVar[Signal] = Signal()
    error_occurred: ClassVar[Signal] = Signal(str)
    raw_data_received: ClassVar[Signal] = Signal(bytes)
    raw_data_sent: ClassVar[Signal] = Signal(bytes)
    packet_received: ClassVar[Signal] = Signal(object)
    ports_changed: ClassVar[Signal] = Signal(list)
    baud_changed: ClassVar[Signal] = Signal(int)
    baud_command_sent: ClassVar[Signal] = Signal(int, str)

    def __init__(
        self,
        link_endpoints: dict[str, tuple[str, int]] | None = None,
        *,
        include_link: bool | None = None,
        link_connect_retries: int = 0,
        link_retry_interval_sec: float = 0.25,
        link_connect_timeout_sec: float = SOCKET_CONNECT_TIMEOUT_SEC,
        link_token: str | None = None,
    ) -> None:
        """Khởi tạo worker serial mà chưa mở cổng kết nối.

        Args:
            link_endpoints: Ánh xạ tùy chọn LINK_* tới địa chỉ (host, port).
            include_link: Cho phép cổng LINK_A/B hiển thị trong danh mục quét cổng hay không.
            link_connect_retries: Số lần thử lại kết nối LINK bổ sung.
            link_retry_interval_sec: Khoảng thời gian giãn cách giữa các lần thử kết nối lại LINK (giây).
            link_connect_timeout_sec: Thời gian timeout kết nối socket LINK cho mỗi lần thử (giây).
            link_token: Token xác thực tùy chọn khi bắt tay với LinkBroker.
        """
        super().__init__()
        self._transport: ITransport | None = None
        self._rx_accumulation = bytearray()
        self._rx_dropped = 0
        self._last_ports: list[PortInfo] = []
        self._port_poll_timer: QTimer | None = None
        self._poll_timer: QTimer | None = None
        self._link_endpoints = link_endpoints
        self._include_link = is_dual_mock_enabled() if include_link is None else include_link
        self._link_connect_retries = link_connect_retries
        self._link_retry_interval_sec = link_retry_interval_sec
        self._link_connect_timeout_sec = link_connect_timeout_sec
        self._link_token = link_token
        self._link_lost_pending = False
        self._current_data_port = ""
        self._current_ctrl_port = ""
        self._current_baud = DEFAULT_BAUDRATE
        self._current_ctrl_baud = CONTROL_BAUDRATE
        self._packets_received = 0
        logger.debug(
            "%s SerialWorker initialized: include_link=%s link_retries=%d",
            LogTag.HW_SERIAL,
            self._include_link,
            self._link_connect_retries,
        )

    @property
    def current_data_port(self) -> str:
        """Tên định danh cổng nối tiếp dữ liệu COM1 đang mở."""
        return self._current_data_port

    @property
    def current_ctrl_port(self) -> str:
        """Tên định danh cổng nối tiếp điều khiển COM2 đang mở."""
        return self._current_ctrl_port

    @property
    def current_tx_port(self) -> str:
        """Tên cổng phát dữ liệu (tương thích ngược với các caller chỉ định TX)."""
        return self._current_data_port

    @property
    def current_rx_port(self) -> str:
        """Tên cổng thu dữ liệu (tương thích ngược với các caller chỉ định RX)."""
        return self._current_data_port

    @property
    def current_baud(self) -> int:
        """Tốc độ Baud quang hiện hành của kênh dữ liệu COM1."""
        return self._current_baud

    @property
    def current_control_baud(self) -> int:
        """Tốc độ Baud cố định của kênh điều khiển COM2 (115200 bps)."""
        return self._current_ctrl_baud

    @property
    def packets_received(self) -> int:
        """Tổng số lượng khung truyền WinFormPacket đã giải mã thành công kể từ lần mở cổng gần nhất."""
        return self._packets_received

    def _on_link_lost(self) -> None:
        """Ghi nhận sự cố mất kết nối đường truyền ảo để xử lý trong chu kỳ thăm dò tiếp theo."""
        self._link_lost_pending = True

    @Slot(str, int)
    def open_port(self, port_name: str, baudrate: int = DEFAULT_BAUDRATE) -> None:
        """Mở cổng dùng chung cho cả kênh dữ liệu và điều khiển (tương thích ngược).

        Args:
            port_name: Tên cổng nối tiếp dùng cho cả 2 vai trò.
            baudrate: Tốc độ Baud quang học.
        """
        self.open_ports(port_name, port_name, baudrate)

    @Slot(str, str, int)
    def open_ports(
        self,
        data_port: str,
        ctrl_port: str = "",
        baudrate: int = DEFAULT_BAUDRATE,
    ) -> None:
        """Mở COM1 ở tốc độ Baud quang và COM2 ở tốc độ Baud chuẩn của vi điều khiển.

        Args:
            data_port: Tên cổng nối tiếp dữ liệu quang COM1.
            ctrl_port: Tên cổng nối tiếp điều khiển COM2 (hoặc chuỗi rỗng).
            baudrate: Tốc độ Baud quang học.
        """
        self.open_hardware_ports(data_port, ctrl_port, baudrate, CONTROL_BAUDRATE)

    @Slot(str, str, int, int)
    def open_hardware_ports(
        self,
        data_port: str,
        ctrl_port: str,
        baudrate: int = DEFAULT_BAUDRATE,
        ctrl_baudrate: int = CONTROL_BAUDRATE,
    ) -> None:
        """Mở đồng thời hai cổng phần cứng COM1/COM2 và khởi chạy định thời thăm dò nhận dữ liệu.

        Args:
            data_port: Tên cổng dữ liệu quang COM1.
            ctrl_port: Tên cổng điều khiển STM32 COM2.
            baudrate: Tốc độ Baud quang học áp dụng cho COM1.
            ctrl_baudrate: Tốc độ Baud cố định cho COM2 (115200 bps).
        """
        data_name = (data_port or "").strip()
        ctrl_name = (ctrl_port or "").strip()
        try:
            self.close_port()
            logger.info(
                "%s Opening hardware ports: COM1=%s @%d COM2=%s @%d",
                LogTag.HW_SERIAL,
                data_name,
                baudrate,
                ctrl_name or "<none>",
                ctrl_baudrate,
            )
            self._transport = create_transport(
                data_port=data_name,
                ctrl_port=ctrl_name or None,
                baudrate=baudrate,
                ctrl_baudrate=ctrl_baudrate,
                link_endpoints=self._link_endpoints,
                link_connect_retries=self._link_connect_retries,
                link_retry_interval_sec=self._link_retry_interval_sec,
                link_connect_timeout_sec=self._link_connect_timeout_sec,
                link_token=self._link_token,
            )
            transport_kind = type(self._transport).__name__
            if hasattr(self._transport, "on_link_lost"):
                self._transport.on_link_lost = self._on_link_lost
            self._link_lost_pending = False
            self._transport.open()
            self._rx_accumulation = bytearray()
            self._rx_dropped = 0
            self._packets_received = 0
            self._current_data_port = data_name
            self._current_ctrl_port = ctrl_name
            self._current_baud = int(baudrate)
            self._current_ctrl_baud = int(ctrl_baudrate)

            poll_ms = self._transport.poll_interval_ms
            self._poll_timer = QTimer(self)
            self._poll_timer.setInterval(poll_ms)
            self._poll_timer.timeout.connect(self._poll_serial)
            self._poll_timer.start()
            self._start_port_watcher()

            logger.info(
                "%s Hardware OPEN: COM1=%s @%d COM2=%s @%d via %s poll=%dms",
                LogTag.HW_SERIAL,
                data_name,
                baudrate,
                ctrl_name or "<none>",
                ctrl_baudrate,
                transport_kind,
                poll_ms,
            )
            self.connected.emit(data_name, int(baudrate))
            self.ports_connected.emit(data_name, ctrl_name, int(baudrate))
        except Exception as exc:
            self._current_data_port = ""
            self._current_ctrl_port = ""
            logger.exception(
                "%s Hardware open FAILED: COM1=%s COM2=%s (%s: %s)",
                LogTag.HW_SERIAL,
                data_name,
                ctrl_name or "<none>",
                type(exc).__name__,
                exc,
            )
            self.error_occurred.emit(f"Connection failed: {exc}")

    @Slot()
    def close_port(self) -> None:
        """Dừng định thời thăm dò và đóng an toàn các kết nối cổng nối tiếp."""
        was_active = self._transport is not None or bool(
            self._current_data_port or self._current_ctrl_port
        )
        transport_kind = type(self._transport).__name__ if self._transport else "none"
        buffered = len(self._rx_accumulation)
        if self._poll_timer is not None:
            self._poll_timer.stop()
            self._poll_timer = None
        self._stop_port_watcher()
        if self._transport is not None:
            try:
                self._transport.close()
            except Exception as exc:
                logger.warning(
                    "%s Transport close warning (%s): %s",
                    LogTag.HW_SERIAL,
                    transport_kind,
                    exc,
                )
        self._transport = None
        self._rx_accumulation = bytearray()
        self._link_lost_pending = False
        self._current_data_port = ""
        self._current_ctrl_port = ""
        if was_active:
            logger.info(
                "%s Hardware CLOSED (%s): buffered=%dB packets=%d dropped=%dB",
                LogTag.HW_SERIAL,
                transport_kind,
                buffered,
                self._packets_received,
                self._rx_dropped,
            )
            self.disconnected.emit()

    @Slot(bytes)
    def inject_rx_data(self, data: bytes) -> None:
        """Bơm trực tiếp dòng byte nhận vào bộ đệm tích lũy RX để giải mã.

        Args:
            data: Chuỗi byte thô nhận từ kênh quang học.
        """
        if not data:
            return
        self._append_rx_chunk(data)
        self._drain_rx_buffer()

    def _append_rx_chunk(self, chunk: bytes) -> None:
        """Nối thêm dữ liệu nhận vào bộ đệm tích lũy và giới hạn kích thước theo dung lượng RingBuffer."""
        self._rx_accumulation.extend(chunk)
        overflow = len(self._rx_accumulation) - RING_BUFFER_CAPACITY
        if overflow > 0:
            del self._rx_accumulation[:overflow]
            self._rx_dropped += overflow
            logger.warning(
                "%s RX accumulation overflow: dropped %d oldest bytes (cap=%dB)",
                LogTag.HW_SERIAL,
                overflow,
                RING_BUFFER_CAPACITY,
            )

    def _drain_rx_buffer(self) -> None:
        """Giải mã toàn bộ các khung truyền WinForm hoàn chỉnh trong bộ đệm và phát tín hiệu cho từng gói."""
        packets, remaining = parse_stream(self._rx_accumulation)
        self._rx_accumulation = remaining
        for packet in packets:
            self._packets_received += 1
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug(
                    "%s RX packet seq=0x%08X len=%dB payload_hex=%s",
                    LogTag.PROTO_FRAME,
                    packet.sequence,
                    packet.length,
                    format_hex_preview(packet.data, max_bytes=16),
                )
            self.packet_received.emit(packet)

    @Slot(bytes)
    def send_bytes(self, data: bytes) -> None:
        """Ghi trực tiếp dòng byte thô xuống cổng dữ liệu COM1.

        Args:
            data: Chuỗi byte thô cần phát.
        """
        if not self._transport or not self._transport.is_open:
            logger.warning(
                "%s TX rejected (not connected): len=%dB hex=%s",
                LogTag.HW_SERIAL,
                len(data),
                format_hex_preview(data),
            )
            self.error_occurred.emit("Cannot send data: serial link is not connected")
            return
        try:
            self._transport.write(data)
            self.raw_data_sent.emit(data)
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug(
                    "%s TX bytes len=%dB hex=%s",
                    LogTag.PROTO_FRAME,
                    len(data),
                    format_hex_preview(data),
                )
        except Exception as exc:
            logger.exception(
                "%s Write error (%s: %s): len=%dB",
                LogTag.HW_SERIAL,
                type(exc).__name__,
                exc,
                len(data),
            )
            self.error_occurred.emit(f"Write error: {exc}")

    @Slot(int)
    def set_baud(self, baud: int) -> None:
        """Gửi lệnh đổi tốc độ Baud qua cổng điều khiển COM2 và cấu hình lại COM1.

        Args:
            baud: Tốc độ Baud quang học đích (2400, 4800, 9600, 19200 bps).
        """
        if not self._transport or not self._transport.is_open:
            self.error_occurred.emit("Cannot change baudrate: serial link is not connected")
            return
        try:
            code = self._transport.send_baud_command(baud)
            self._transport.reconfigure_data_baud(baud)
            self._current_baud = int(baud)
            logger.info(
                "%s Baud switch complete: code=%r baud=%d",
                LogTag.BAUD_CTRL,
                code,
                baud,
            )
            self.baud_command_sent.emit(int(baud), code)
            self.baud_changed.emit(int(baud))
        except Exception as exc:
            logger.exception(
                "%s Baud switch FAILED (%s: %s) target=%d",
                LogTag.BAUD_CTRL,
                type(exc).__name__,
                exc,
                baud,
            )
            self.error_occurred.emit(f"Baud switch failed: {exc}")

    @Slot()
    def _poll_serial(self) -> None:
        """Thăm dò dữ liệu mới trên cổng dữ liệu COM1 và kích hoạt giải mã khung truyền."""
        if self._transport is None:
            return
        if self._link_lost_pending:
            self._link_lost_pending = False
            logger.error(
                "%s Link LOST: peer or broker disconnected (transport=%s)",
                LogTag.HW_SERIAL,
                type(self._transport).__name__,
            )
            self.error_occurred.emit("Link lost: peer or broker disconnected")
            self.close_port()
            return
        if not self._transport.is_open:
            return
        try:
            available = self._transport.in_waiting
            if available <= 0:
                return
            chunk = self._transport.read(available)
            if not chunk:
                return
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug(
                    "%s Poll RX: req=%dB got=%dB hex=%s",
                    LogTag.HW_SERIAL,
                    available,
                    len(chunk),
                    format_hex_preview(chunk),
                )
            self.raw_data_received.emit(chunk)
            self._append_rx_chunk(chunk)
            self._drain_rx_buffer()
        except Exception as exc:
            logger.exception(
                "%s Serial read poll error (%s: %s); closing port",
                LogTag.HW_SERIAL,
                type(exc).__name__,
                exc,
            )
            self.error_occurred.emit(f"Read error: {exc}")
            self.close_port()

    def _start_port_watcher(self) -> None:
        """Khởi động bộ định thời giám sát cắm/rút thiết bị cổng nối tiếp (Hotplug Port Watcher)."""
        self._last_ports = scan_available_port_infos(self._include_link)
        if self._port_poll_timer is None:
            self._port_poll_timer = QTimer(self)
            self._port_poll_timer.setInterval(1500)
            self._port_poll_timer.timeout.connect(self._poll_ports)
            self._port_poll_timer.start()

    def _stop_port_watcher(self) -> None:
        """Dừng bộ định thời giám sát cắm/rút thiết bị."""
        if self._port_poll_timer is not None:
            self._port_poll_timer.stop()
            self._port_poll_timer = None

    @Slot()
    def _poll_ports(self) -> None:
        """Phát tín hiệu ports_changed khi phát hiện thay đổi danh sách cổng COM trên hệ điều hành."""
        try:
            current = scan_available_port_infos(self._include_link)
            if current != self._last_ports:
                logger.info(
                    "%s Hotplug ports changed: %s -> %s",
                    LogTag.HW_SERIAL,
                    self._last_ports,
                    current,
                )
                self._last_ports = current
                self.ports_changed.emit(current)
        except Exception as exc:
            logger.debug(
                "%s Port watcher poll failed (%s: %s)",
                LogTag.HW_SERIAL,
                type(exc).__name__,
                exc,
            )

    @property
    def rx_dropped_bytes(self) -> int:
        """Tổng số lượng byte nhận bị tràn bộ đệm và buộc phải loại bỏ."""
        return self._rx_dropped

    @property
    def framer_stats_snapshot(self) -> dict[str, int]:
        """Trả về bản sao thống kê giải mã khung truyền để tương thích ngược."""
        return {
            "frames_ok": self._packets_received,
            "noise_bytes": 0,
            "crc_errors": 0,
            "eof_errors": 0,
            "len_errors": 0,
        }

    def _describe_tx_bytes(self, data: bytes) -> None:
        """Ghi log mô tả nhanh nội dung byte phát ra phục vụ gỡ lỗi."""
        logger.debug(
            "%s TX summary len=%dB packet=%s",
            LogTag.PROTO_FRAME,
            len(data),
            isinstance(data, (bytes, bytearray)),
        )


def scan_available_port_infos(include_link: bool | None = None) -> list[PortInfo]:
    """Quét toàn bộ cổng nối tiếp vật lý của máy chủ và bổ sung các cổng nối tiếp ảo mô phỏng.

    Args:
        include_link: Cho phép liệt kê các cổng ảo LINK_A/B hay không.

    Returns:
        Danh sách các bản ghi PortInfo đã loại bỏ trùng lặp và sắp xếp tự nhiên.
    """
    infos: list[PortInfo] = []
    seen: set[str] = set()
    for listed in list_ports.comports():
        device = listed.device
        if device in seen:
            continue
        description = (listed.description or "").strip()
        if description.lower() in ("n/a", "none"):
            description = ""
        hwid_str = (listed.hwid or "").strip()
        if hwid_str.lower() in ("n/a", "none"):
            hwid_str = ""
        subsystem_str = str(getattr(listed, "subsystem", "") or "").strip().lower()
        if (
            device.startswith("/dev/ttyS")
            and not hwid_str
            and not description
            and subsystem_str in ("serial-base", "")
        ):
            continue
        seen.add(device)
        vid = _format_hex_identifier(listed.vid)
        pid = _format_hex_identifier(listed.pid)
        infos.append(
            PortInfo(
                device=device,
                description=description,
                manufacturer=listed.manufacturer or None,
                vid=vid,
                pid=pid,
                hwid=listed.hwid or "",
                label=_friendly_label(device, description, vid, pid),
            )
        )
    for extra in _windows_registry_ports():
        if extra in seen:
            continue
        seen.add(extra)
        infos.append(
            PortInfo(
                device=extra,
                description="Windows Registry Serial Port",
                manufacturer=None,
                vid=None,
                pid=None,
                hwid="",
                label=f"{extra} - Windows Registry Serial Port",
            )
        )
    if is_dev_mode():
        infos.append(_virtual_port_info("MOCK_COM_LOOPBACK", "Virtual Loopback"))
        infos.append(_virtual_port_info("MOCK_COM1", "Virtual COM1 Data"))
        infos.append(_virtual_port_info("MOCK_COM2", "Virtual COM2 Control"))
        link_enabled = is_dual_mock_enabled() if include_link is None else include_link
        if link_enabled:
            infos.append(_virtual_port_info("LINK_A", "Virtual Dual-Link"))
            infos.append(_virtual_port_info("LINK_B", "Virtual Dual-Link"))
            infos.append(_virtual_port_info("LINK_A_TX", "Virtual Dual-Link TX"))
            infos.append(_virtual_port_info("LINK_A_RX", "Virtual Dual-Link RX"))
            infos.append(_virtual_port_info("LINK_B_TX", "Virtual Dual-Link TX"))
            infos.append(_virtual_port_info("LINK_B_RX", "Virtual Dual-Link RX"))
    return sorted(infos, key=lambda info: _natural_port_key(info.device))


def scan_available_ports(include_link: bool | None = None) -> list[str]:
    """Quét và trả về danh sách tên định danh của các cổng serial vật lý và cổng ảo.

    Args:
        include_link: Cho phép liệt kê các cổng ảo LINK_A/B hay không.

    Returns:
        Danh sách tên định danh cổng đã được sắp xếp tự nhiên theo thứ tự số học.
    """
    return [info.device for info in scan_available_port_infos(include_link)]
