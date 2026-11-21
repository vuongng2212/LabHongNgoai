"""Bộ não trung gian tiếp nhận lệnh từ màn hình máy tính và giao việc cho tiến trình ngầm serial_worker (Serial Manager).

Đóng vai trò điều phối trung tâm:
- Tiếp nhận các thao tác của người dùng trên giao diện (bấm Kết nối cổng COM, bấm Gửi gói tin, chọn Tốc độ Baudrate).
- Chuyển giao các yêu cầu này xuống luồng chạy ngầm SerialWorker để trực tiếp điều khiển chân TX/RX phần cứng mà không làm treo hay giật lag giao diện.
- Lắng nghe các byte nhận về từ mạch quang và các khung tin WinFormPacket đã giải mã để phân phát tới các dịch vụ chat, truyền file và đo lường viễn trắc.
"""

from __future__ import annotations

import logging
from typing import ClassVar

from PySide6.QtCore import QObject, QThread, Signal

from ir_terminal.core.serial_worker import (
    PortInfo,
    SerialWorker,
    scan_available_port_infos,
    scan_available_ports,
)
from ir_terminal.protocol.constants import (
    CONTROL_BAUDRATE,
    DEFAULT_BAUDRATE,
    SHUTDOWN_WAIT_MS,
    SOCKET_CONNECT_TIMEOUT_SEC,
)
from ir_terminal.protocol.framer import pack_packet
from ir_terminal.utils.logger import LogTag, format_hex_preview
from ir_terminal.utils.path_helpers import is_dual_mock_enabled

logger = logging.getLogger(__name__)


class SerialManager(QObject):
    """Bộ não trung gian tiếp nhận lệnh từ màn hình máy tính và giao việc cho tiến trình ngầm serial_worker."""

    connected: ClassVar[Signal] = Signal(str, int)
    ports_connected: ClassVar[Signal] = Signal(str, str, int)
    disconnected: ClassVar[Signal] = Signal()
    error_occurred: ClassVar[Signal] = Signal(str)
    raw_tx: ClassVar[Signal] = Signal(bytes)
    raw_rx: ClassVar[Signal] = Signal(bytes)
    packet_received: ClassVar[Signal] = Signal(object)
    ports_changed: ClassVar[Signal] = Signal(list)
    baud_changed: ClassVar[Signal] = Signal(int)
    baud_command_sent: ClassVar[Signal] = Signal(int, str)

    _sig_open: ClassVar[Signal] = Signal(str, int)
    _sig_open_hw: ClassVar[Signal] = Signal(str, str, int, int)
    _sig_close: ClassVar[Signal] = Signal()
    _sig_send: ClassVar[Signal] = Signal(bytes)
    _sig_inject: ClassVar[Signal] = Signal(bytes)
    _sig_set_baud: ClassVar[Signal] = Signal(int)

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
        """Khởi tạo bộ não điều phối SerialManager và tạo luồng chạy ngầm riêng biệt cho SerialWorker.

        Args:
            link_endpoints: Ánh xạ tùy chọn cổng ảo LINK_* tới địa chỉ mạng (host, port).
            include_link: Cho phép cổng LINK hiển thị trong danh mục quét cổng hay không.
            link_connect_retries: Số lần thử kết nối lại bổ sung cho cổng LINK.
            link_retry_interval_sec: Khoảng thời gian giãn cách giữa các lần thử kết nối lại LINK (tính bằng giây).
            link_connect_timeout_sec: Thời gian chờ tối đa cho mỗi lần kết nối socket LINK (giây).
            link_token: Khóa bảo mật bắt tay xác thực tùy chọn với LinkBroker.
        """
        super().__init__()
        self._include_link = is_dual_mock_enabled() if include_link is None else include_link
        self._thread = QThread()
        self._worker = SerialWorker(
            link_endpoints=link_endpoints,
            include_link=self._include_link,
            link_connect_retries=link_connect_retries,
            link_retry_interval_sec=link_retry_interval_sec,
            link_connect_timeout_sec=link_connect_timeout_sec,
            link_token=link_token,
        )
        self._worker.moveToThread(self._thread)

        self._sig_open.connect(self._worker.open_port)
        self._sig_open_hw.connect(self._worker.open_hardware_ports)
        self._sig_close.connect(self._worker.close_port)
        self._sig_send.connect(self._worker.send_bytes)
        self._sig_inject.connect(self._worker.inject_rx_data)
        self._sig_set_baud.connect(self._worker.set_baud)

        self._worker.connected.connect(self.connected)
        self._worker.ports_connected.connect(self.ports_connected)
        self._worker.disconnected.connect(self.disconnected)
        self._worker.error_occurred.connect(self.error_occurred)
        self._worker.raw_data_sent.connect(self.raw_tx)
        self._worker.raw_data_received.connect(self.raw_rx)
        self._worker.packet_received.connect(self.packet_received)
        self._worker.ports_changed.connect(self.ports_changed)
        self._worker.baud_changed.connect(self.baud_changed)
        self._worker.baud_command_sent.connect(self.baud_command_sent)

        self._thread.start()
        self._is_connected = False
        self._current_data_port = ""
        self._current_ctrl_port = ""
        self._current_baud = DEFAULT_BAUDRATE
        self._current_ctrl_baud = CONTROL_BAUDRATE
        self.ports_connected.connect(self._on_ports_connected)
        self._worker.disconnected.connect(self._on_disconnected)
        self._worker.baud_changed.connect(self._on_baud_changed)
        logger.debug(
            "%s Manager worker thread started: running=%s",
            LogTag.HW_SERIAL,
            self._thread.isRunning(),
        )

    def _on_ports_connected(self, data_port: str, ctrl_port: str, baud: int) -> None:
        """Ghi nhận trạng thái mở cổng thành công từ tiến trình ngầm và lưu lại tốc độ Baud quang học."""
        self._is_connected = True
        self._current_data_port = data_port
        self._current_ctrl_port = ctrl_port
        self._current_baud = baud
        logger.info(
            "%s Manager link UP: COM1=%s @%d COM2=%s @%d",
            LogTag.HW_SERIAL,
            data_port,
            baud,
            ctrl_port or "<none>",
            self._current_ctrl_baud,
        )

    def _on_disconnected(self) -> None:
        """Xóa trạng thái kết nối sau khi tiến trình ngầm thông báo đã đóng cổng an toàn."""
        logger.info(
            "%s Manager link DOWN: was COM1=%s @%d",
            LogTag.HW_SERIAL,
            self._current_data_port,
            self._current_baud,
        )
        self._is_connected = False
        self._current_data_port = ""
        self._current_ctrl_port = ""

    def _on_baud_changed(self, baudrate: int) -> None:
        """Cập nhật giá trị tốc độ Baud quang học sau khi phần cứng xác nhận chuyển đổi thành công.

        Args:
            baudrate: Tốc độ Baud mới của cổng dữ liệu quang COM1 (2400, 4800, 9600, 19200 bps).
        """
        self._current_baud = int(baudrate)
        logger.info("%s Manager optical baudrate now %d bps", LogTag.BAUD_CTRL, baudrate)

    @property
    def is_connected(self) -> bool:
        """Kiểm tra xem cặp cổng nối tiếp phần cứng hiện có đang mở và sẵn sàng truyền nhận hay không."""
        return self._is_connected

    @property
    def current_port(self) -> str:
        """Tên định danh của cổng dữ liệu quang COM1 đang hoạt động."""
        return self._current_data_port

    @property
    def current_data_port(self) -> str:
        """Tên cổng COM1 đang kết nối với mạch thu phát quang hồng ngoại."""
        return self._current_data_port

    @property
    def current_ctrl_port(self) -> str:
        """Tên cổng COM2 đang nối với chân UART điều khiển của vi điều khiển STM32."""
        return self._current_ctrl_port

    @property
    def current_tx_port(self) -> str:
        """Tên cổng phát dữ liệu quang (COM1)."""
        return self._current_data_port

    @property
    def current_rx_port(self) -> str:
        """Tên cổng thu dữ liệu quang (COM1)."""
        return self._current_data_port

    @property
    def current_baud(self) -> int:
        """Tốc độ Baud quang hiện hành của kênh dữ liệu COM1 (2400, 4800, 9600, 19200 bps)."""
        return self._current_baud

    @property
    def current_control_baud(self) -> int:
        """Tốc độ Baud cố định của kênh điều khiển vi điều khiển STM32 trên cổng COM2 (115200 bps)."""
        return self._current_ctrl_baud

    def connect_ports(
        self,
        data_port: str,
        ctrl_port: str | None = None,
        baudrate: int = DEFAULT_BAUDRATE,
        ctrl_baudrate: int = CONTROL_BAUDRATE,
    ) -> None:
        """Giao việc cho tiến trình ngầm mở cặp cổng: COM1 (dữ liệu quang) và COM2 (điều khiển STM32).

        Args:
            data_port: Tên cổng dữ liệu quang COM1.
            ctrl_port: Tên cổng điều khiển STM32 COM2 (hoặc None nếu không dùng kênh điều khiển).
            baudrate: Tốc độ Baud quang áp dụng cho COM1 (mặc định 19200 bps).
            ctrl_baudrate: Tốc độ Baud cố định cho COM2 (mặc định 115200 bps).
        """
        data_name = (data_port or "").strip()
        ctrl_name = (ctrl_port or "").strip()
        self._current_ctrl_baud = int(ctrl_baudrate)
        logger.info(
            "%s Manager connect: COM1=%s @%d COM2=%s @%d",
            LogTag.HW_SERIAL,
            data_name,
            baudrate,
            ctrl_name or "<none>",
            ctrl_baudrate,
        )
        self._sig_open_hw.emit(data_name, ctrl_name, int(baudrate), int(ctrl_baudrate))

    def connect_port(self, port_name: str, baudrate: int = DEFAULT_BAUDRATE) -> None:
        """Giao việc mở một cổng nối tiếp đơn lẻ không kèm kênh điều khiển phụ.

        Args:
            port_name: Tên định danh cổng nối tiếp cho COM1.
            baudrate: Tốc độ Baud quang học.
        """
        self.connect_ports(port_name, None, baudrate)

    def disconnect_port(self) -> None:
        """Yêu cầu tiến trình ngầm đóng an toàn toàn bộ các cổng phần cứng đang mở."""
        logger.info(
            "%s Manager disconnect requested: COM1=%s",
            LogTag.HW_SERIAL,
            self._current_data_port,
        )
        self._sig_close.emit()

    def inject_raw_rx(self, data: bytes) -> None:
        """Bơm chuỗi byte giả lập vào tiến trình ngầm như thể vừa nhận được tín hiệu từ tia quang.

        Args:
            data: Chuỗi byte thô giả lập nhận được từ kênh truyền quang.
        """
        self._sig_inject.emit(data)

    def send_raw(self, data: bytes) -> None:
        """Chuyển chuỗi byte thô xuống tiến trình ngầm để phát ra chân TX của cổng COM1.

        Args:
            data: Dữ liệu nhị phân cần truyền tải.
        """
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(
                "%s Manager TX raw: len=%dB hex=%s connected=%s",
                LogTag.HW_SERIAL,
                len(data),
                format_hex_preview(data),
                self._is_connected,
            )
        self._sig_send.emit(data)

    def send_packet(self, sequence: int, data: bytes = b"") -> None:
        """Đóng gói mảng dữ liệu thành khung tin chuẩn WinForm C# (8 byte tiêu đề) rồi đẩy xuống phát qua COM1.

        Args:
            sequence: Mã định danh khung truyền (uint32: SEQ_HEADER, SEQ_END hoặc chỉ số chunk DATA).
            data: Dữ liệu payload đi kèm khung truyền.
        """
        try:
            frame = pack_packet(sequence, data)
        except Exception:
            logger.exception(
                "%s Manager pack FAILED: seq=0x%08X len=%dB",
                LogTag.PROTO_FRAME,
                sequence,
                len(data),
            )
            raise
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(
                "%s Manager TX frame: seq=0x%08X payload=%dB frame=%dB",
                LogTag.PROTO_FRAME,
                sequence,
                len(data),
                len(frame),
            )
        self.send_raw(frame)

    def send_baud_command(self, baudrate: int) -> None:
        """Giao việc cho tiến trình ngầm gửi lệnh đổi tốc độ qua COM2 cho STM32 và chỉnh lại tốc độ COM1.

        Args:
            baudrate: Tốc độ Baud quang học đích (2400, 4800, 9600, 19200 bps).
        """
        logger.info("%s Manager baud switch requested: %d", LogTag.BAUD_CTRL, baudrate)
        self._sig_set_baud.emit(int(baudrate))

    def get_port_list(self) -> list[str]:
        """Quét và trả về danh sách tên định danh của các cổng COM cắm trên máy."""
        return scan_available_ports(self._include_link)

    def get_port_info_list(self) -> list[PortInfo]:
        """Quét và trả về danh sách bảng thông tin chi tiết (tên chip, VID/PID) của các cổng COM."""
        return scan_available_port_infos(self._include_link)

    def shutdown(self) -> None:
        """Dừng an toàn luồng ngầm SerialWorker và giải phóng tài nguyên cổng nối tiếp trước khi tắt ứng dụng."""
        logger.info(
            "%s Manager shutdown: requesting worker close (COM1=%s connected=%s)",
            LogTag.HW_SERIAL,
            self._current_data_port,
            self._is_connected,
        )
        try:
            self._sig_close.emit()
            self._thread.quit()
            if not self._thread.wait(SHUTDOWN_WAIT_MS):
                logger.warning(
                    "%s SerialManager thread did not stop in %d ms",
                    LogTag.HW_SERIAL,
                    SHUTDOWN_WAIT_MS,
                )
            else:
                logger.info("%s SerialManager worker thread stopped cleanly", LogTag.HW_SERIAL)
        except Exception as exc:
            logger.warning(
                "%s SerialManager shutdown error (%s: %s)",
                LogTag.HW_SERIAL,
                type(exc).__name__,
                exc,
            )
