"""Tiến trình chạy ngầm phụ trách đọc/ghi trực tiếp cổng nối tiếp COM (Serial Worker).

Hoạt động độc lập trên một luồng riêng (QThread) để việc gửi nhận dữ liệu không làm đơ giao diện màn hình:
1. Giao tiếp hai cổng: COM1 (truyền nhận dữ liệu quang qua mạch MCP2122 + TFDU4101) và COM2 (gửi lệnh điều khiển vi điều khiển STM32).
2. Cứ 10 mili-giây tự động kiểm tra xem cổng COM1 có byte nào mới từ mạch quang gửi về hay không để nhặt vào bộ đệm tích lũy.
3. Tự động bóc tách các byte nhận được thành từng gói tin hoàn chỉnh (theo chuẩn khung WinForm C#) rồi báo cho giao diện và các dịch vụ xử lý.
4. Tự động phát hiện khi người dùng cắm hoặc rút dây cáp USB-UART và hỗ trợ đổi tốc độ Baudrate quang học.
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
    """Tạo khóa sắp xếp thứ tự cổng COM theo số tự nhiên (đảm bảo COM2 đứng trước COM10 thay vì bị xếp theo chữ cái)."""
    return "".join(
        f"{int(part):08d}" if part.isdigit() else part.lower() for part in re.split(r"(\d+)", name)
    )


def _windows_registry_ports() -> list[str]:
    """Đọc trực tiếp danh sách cổng COM từ Registry Windows (khóa SERIALCOMM) để tìm các cổng nối tiếp đã khai báo."""
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
    """Bảng thông tin chi tiết của một cổng COM phát hiện trên máy tính (tên cổng, chip nạp/chuyển đổi USB-UART, mã định danh phần cứng VID/PID)."""

    device: str
    description: str
    manufacturer: str | None
    vid: str | None
    pid: str | None
    hwid: str
    label: str

    def to_dict(self) -> dict[str, str | None]:
        """Chuyển đổi thông tin cổng COM thành dạng từ điển (dictionary) để gửi lên giao diện hiển thị cho người dùng chọn."""
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
    """Đổi mã định danh phần cứng USB (VID/PID) thành dạng chuỗi Hex 4 ký tự viết hoa (ví dụ: VID 0x0483 của chip STM32)."""
    if value is None:
        return None
    return f"{int(value) & 0xFFFF:04X}"


def _friendly_label(device: str, description: str, vid: str | None, pid: str | None) -> str:
    """Tạo tên hiển thị trực quan, dễ hiểu cho cổng COM trên giao diện (kèm tên chip hoặc mã VID:PID để người dùng dễ nhận biết cổng nào cắm vào mạch nào)."""
    if vid and pid:
        return f"{description or device} [{vid}:{pid}]"
    if description:
        return f"{device} - {description}"
    return device


def _virtual_port_info(device: str, description: str) -> PortInfo:
    """Tạo thông tin giả lập cho một cổng COM ảo chạy trong bộ nhớ phục vụ chạy thử nghiệm khi chưa có mạch thật."""
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
    """Tiến trình chạy ngầm làm nhiệm vụ nhặt từng byte từ cổng COM1 và đẩy byte ra chân TX.

    Tiến trình này hoạt động độc lập ở hậu trường, liên tục túc trực để:
    - Bơm dữ liệu từ máy tính ra chân TX của mạch phát quang (MCP2122 + TFDU4101).
    - Cứ 10 mili-giây tự động kiểm tra xem cổng COM1 có byte nào mới từ mạch quang gửi về hay không để nhặt vào bộ đệm, giúp giao diện không bị đơ.
    - Gửi chuỗi lệnh AT hoặc mã đổi tốc độ Baud qua cổng COM2 để điều khiển vi điều khiển STM32.
    """

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
        """Khởi tạo tiến trình giao tiếp cổng nối tiếp ở trạng thái nghỉ (chưa mở cổng).

        Args:
            link_endpoints: Bảng ánh xạ địa chỉ IP/Port cho các cổng quang học ảo (LINK_*).
            include_link: Cho phép cổng LINK_A/B hiển thị trong danh mục quét cổng hay không.
            link_connect_retries: Số lần thử cắm lại đường truyền ảo nếu lần đầu chưa thấy trạm đối tác mở.
            link_retry_interval_sec: Khoảng thời gian giãn cách giữa các lần thử kết nối lại LINK (tính bằng giây).
            link_connect_timeout_sec: Thời gian chờ tối đa cho mỗi lần kết nối socket LINK (giây).
            link_token: Mã khóa bảo mật tùy chọn để bắt tay với trạm trung chuyển ảo (LinkBroker).
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
        """Tên cổng COM1 dùng để truyền nhận dữ liệu qua tia hồng ngoại."""
        return self._current_data_port

    @property
    def current_ctrl_port(self) -> str:
        """Tên cổng COM2 nối với chân điều khiển của vi điều khiển STM32."""
        return self._current_ctrl_port

    @property
    def current_tx_port(self) -> str:
        """Tên cổng phát dữ liệu quang (tương thích ngược với các hàm gọi tên cũ TX)."""
        return self._current_data_port

    @property
    def current_rx_port(self) -> str:
        """Tên cổng thu dữ liệu quang (tương thích ngược với các hàm gọi tên cũ RX)."""
        return self._current_data_port

    @property
    def current_baud(self) -> int:
        """Tốc độ Baud hiện tại của mắt phát/thu quang trên cổng COM1 (2400, 4800, 9600, 19200 bps)."""
        return self._current_baud

    @property
    def current_control_baud(self) -> int:
        """Tốc độ Baud cố định của kênh điều khiển COM2 nối với STM32 (115200 bps)."""
        return self._current_ctrl_baud

    @property
    def packets_received(self) -> int:
        """Tổng số khung tin quang học (WinFormPacket) đã thu thập thành công kể từ lần mở cổng gần nhất."""
        return self._packets_received

    def _on_link_lost(self) -> None:
        """Đánh dấu sự cố đứt đường truyền quang ảo để xử lý ngắt kết nối an toàn ở chu kỳ quét kế tiếp."""
        self._link_lost_pending = True

    @Slot(str, int)
    def open_port(self, port_name: str, baudrate: int = DEFAULT_BAUDRATE) -> None:
        """Mở một cổng COM duy nhất dùng chung cho cả truyền dữ liệu quang và điều khiển vi điều khiển.

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
        """Mở đồng thời cổng COM1 (tốc độ quang) và COM2 (tốc độ 115200 bps cho vi điều khiển STM32).

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
        """Mở trực tiếp cặp cổng vật lý COM1/COM2 trên mạch và kích hoạt bộ hẹn giờ 10 ms để nhặt byte từ chân RX.

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
        """Dừng bộ định thời kiểm tra RX và đóng an toàn hai cổng COM để giải phóng tài nguyên cho hệ điều hành."""
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
        """Bơm trực tiếp dòng byte nhận vào bộ đệm tích lũy RX để giả lập việc nhận tín hiệu từ mạch quang.

        Args:
            data: Chuỗi byte thô nhận từ kênh quang học.
        """
        if not data:
            return
        self._append_rx_chunk(data)
        self._drain_rx_buffer()

    def _append_rx_chunk(self, chunk: bytes) -> None:
        """Nối thêm dữ liệu nhận vào bộ đệm tích lũy và giới hạn kích thước theo dung lượng RingBuffer (tự đẩy byte cũ nếu tràn)."""
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
        """Quét và bóc tách toàn bộ các khung tin WinForm C# hoàn chỉnh trong thùng đệm nhận rồi phát sự kiện cho từng gói."""
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
        """Bơm trực tiếp dòng byte ra chân TX của cổng COM1 để phát qua tia hồng ngoại.

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
        """Gửi mã lệnh 2 ký tự ASCII qua cổng điều khiển COM2 để vi điều khiển STM32 chuyển mạch, sau đó cấu hình lại tốc độ cổng COM1.

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
        """Cứ 10 mili-giây tự động kiểm tra xem cổng COM1 có byte nào mới từ mạch quang gửi về hay không để nhặt vào bộ đệm, giúp giao diện không bị đơ."""
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
        """Bắt đầu định thời (1.5 giây một lần) tự động rà soát sự kiện cắm hoặc rút cáp chuyển đổi USB-UART trên máy tính."""
        self._last_ports = scan_available_port_infos(self._include_link)
        if self._port_poll_timer is None:
            self._port_poll_timer = QTimer(self)
            self._port_poll_timer.setInterval(1500)
            self._port_poll_timer.timeout.connect(self._poll_ports)
            self._port_poll_timer.start()

    def _stop_port_watcher(self) -> None:
        """Dừng bộ hẹn giờ rà soát cắm/rút thiết bị cổng COM."""
        if self._port_poll_timer is not None:
            self._port_poll_timer.stop()
            self._port_poll_timer = None

    @Slot()
    def _poll_ports(self) -> None:
        """Tự động kiểm tra danh sách cổng COM của hệ điều hành, nếu phát hiện cắm thêm mạch mới hoặc rút cáp thì báo ngay lên giao diện."""
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
        """Tổng số byte nhận bị tràn bộ đệm và buộc phải hủy bỏ khi mạch quang gửi về dồn dập vượt quá sức chứa bộ đệm."""
        return self._rx_dropped

    @property
    def framer_stats_snapshot(self) -> dict[str, int]:
        """Bản sao các chỉ số thống kê số gói tin giải mã thành công phục vụ các dịch vụ đo lường viễn trắc."""
        return {
            "frames_ok": self._packets_received,
            "noise_bytes": 0,
            "crc_errors": 0,
            "eof_errors": 0,
            "len_errors": 0,
        }

    def _describe_tx_bytes(self, data: bytes) -> None:
        """Ghi log tóm tắt độ dài và cấu trúc dữ liệu vừa đẩy ra chân TX phục vụ theo dõi tín hiệu."""
        logger.debug(
            "%s TX summary len=%dB packet=%s",
            LogTag.PROTO_FRAME,
            len(data),
            isinstance(data, (bytes, bytearray)),
        )


def scan_available_port_infos(include_link: bool | None = None) -> list[PortInfo]:
    """Quét toàn bộ cổng COM vật lý cắm trên máy tính và bổ sung các cổng ảo nếu đang bật chế độ giả lập hai trạm.

    Args:
        include_link: Cho phép liệt kê các cổng ảo LINK_A/B hay không.

    Returns:
        Danh sách các bản ghi PortInfo đã loại bỏ trùng lặp và sắp xếp tự nhiên theo thứ tự số học.
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
    """Quét và trả về danh sách tên định danh của các cổng COM vật lý và cổng ảo (ví dụ: ['COM1', 'COM2', 'COM10']).

    Args:
        include_link: Cho phép liệt kê các cổng ảo LINK_A/B hay không.

    Returns:
        Danh sách tên cổng COM đã được sắp xếp tự nhiên theo thứ tự số học.
    """
    return [info.device for info in scan_available_port_infos(include_link)]
