"""Cầu nối hai chiều QWebChannel giữa lõi Python và giao diện Web SPA JavaScript.

Module này phơi bày các API nghiệp vụ (slots) và tín hiệu sự kiện (signals) cho Chromium WebEngine:
- Điều khiển kết nối hai cổng nối tiếp COM1 (dữ liệu quang) và COM2 (điều khiển STM32).
- Nhắn tin quang thời gian thực (ChatService qua virtual file envelope).
- Truyền nhận file Open-Loop điều tốc (FileTransferService).
- Giám sát viễn trắc thông lượng, tỷ lệ rớt gói và bộ bắt gói tin quang Sniffer.
- Lưu trữ bền vững lịch sử, cấu hình cài đặt và xuất dữ liệu báo cáo ra tệp CSV.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import ClassVar

from PySide6.QtCore import QObject, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QFileDialog, QWidget

from ir_terminal.config import (
    DEFAULT_BAUD,
    DEFAULT_LINK_BASE_PORT,
    DEFAULT_LINK_HOST,
    InstanceContext,
    LaunchConfig,
)
from ir_terminal.core.serial_manager import SerialManager
from ir_terminal.core.serial_worker import PortInfo
from ir_terminal.protocol.constants import (
    CONTROL_BAUDRATE,
    DEFAULT_OPTICAL_BAUDRATE,
    SUPPORTED_BAUDRATES,
)
from ir_terminal.protocol.packet import WinFormPacket
from ir_terminal.services.chat_service import ChatService
from ir_terminal.services.file_transfer_service import FileTransferService
from ir_terminal.services.storage_service import StorageService
from ir_terminal.services.telemetry_service import TelemetryService
from ir_terminal.utils.logger import LogTag, format_hex_preview
from ir_terminal.utils.path_helpers import get_logs_dir

logger = logging.getLogger(__name__)


class BackendBridge(QObject):
    """Cầu nối QWebChannel phơi bày các API Python và phát tín hiệu hai chiều cho JavaScript SPA."""

    portConnected: ClassVar[Signal] = Signal(str, str, int)
    portDisconnected: ClassVar[Signal] = Signal()
    errorOccurred: ClassVar[Signal] = Signal(str)
    pingResult: ClassVar[Signal] = Signal(float, int)
    healthResult: ClassVar[Signal] = Signal(float, int)
    rawRxData: ClassVar[Signal] = Signal(str)
    rawTxData: ClassVar[Signal] = Signal(str)

    closeWindowRequested: ClassVar[Signal] = Signal()
    minimizeWindowRequested: ClassVar[Signal] = Signal()
    maximizeWindowRequested: ClassVar[Signal] = Signal()

    chatMessageReceived: ClassVar[Signal] = Signal(str, str, bool)

    transferProgress: ClassVar[Signal] = Signal(int, float, float, int, int)
    transferCompleted: ClassVar[Signal] = Signal(str, int, float, str)
    transferFailed: ClassVar[Signal] = Signal(str)
    transferStateChanged: ClassVar[Signal] = Signal(str)

    opticalBaudChanged: ClassVar[Signal] = Signal(int)
    opticalBaudPending: ClassVar[Signal] = Signal(int)
    baudControlStatus: ClassVar[Signal] = Signal(str, str)

    telemetryUpdated: ClassVar[Signal] = Signal(float, float, float, int, int)
    snifferFrameLogged: ClassVar[Signal] = Signal(str, str, str, str, str, str, float)
    portsChanged: ClassVar[Signal] = Signal(str)
    settingsChanged: ClassVar[Signal] = Signal(str)

    def __init__(
        self,
        serial_manager: SerialManager,
        chat_service: ChatService | None = None,
        file_service: FileTransferService | None = None,
        telemetry_service: TelemetryService | None = None,
        storage_service: StorageService | None = None,
        launch_config: LaunchConfig | None = None,
        instance_context: InstanceContext | None = None,
    ) -> None:
        """Khởi tạo cầu nối BackendBridge liên kết các dịch vụ lõi với giao diện Web.

        Args:
            serial_manager: Đối tượng SerialManager điều phối kết nối cổng nối tiếp.
            chat_service: Dịch vụ tin nhắn trò chuyện ChatService (hoặc None để tự tạo).
            file_service: Dịch vụ truyền nhận file FileTransferService (hoặc None để tự tạo).
            telemetry_service: Dịch vụ đo lường viễn trắc TelemetryService (hoặc None để tự tạo).
            storage_service: Dịch vụ lưu trữ StorageService (hoặc None để tự tạo).
            launch_config: Cấu hình tham số dòng lệnh khởi động ứng dụng.
            instance_context: Ngữ cảnh thông tin định danh của phiên tiến trình hiện hành.
        """
        super().__init__()
        self._serial_manager = serial_manager
        self._launch_config = launch_config
        self._instance_context = instance_context
        self._storage = storage_service or StorageService()
        self._file_service = file_service or FileTransferService(
            self._serial_manager, storage_service=self._storage
        )
        self._chat_service = chat_service or ChatService(
            self._serial_manager, self._storage, file_transfer_service=self._file_service
        )
        if self._chat_service.file_transfer_service is None:
            self._chat_service.set_file_transfer_service(self._file_service)
        self._telemetry = telemetry_service or TelemetryService(storage_service=self._storage)

        self._last_ping_time: float = 0.0
        self._ping_seq: int = 1

        requested_optical = launch_config.baud if launch_config else DEFAULT_OPTICAL_BAUDRATE
        self._optical_baud: int = (
            int(requested_optical)
            if requested_optical in SUPPORTED_BAUDRATES
            else DEFAULT_OPTICAL_BAUDRATE
        )
        self._pending_optical_baud: int | None = None
        self._is_connected = False

        self._wire_signals()

    def _wire_signals(self) -> None:
        """Đăng ký lắng nghe tín hiệu từ các dịch vụ lõi và ánh xạ tới các sự kiện cầu nối."""
        self._serial_manager.ports_connected.connect(self._on_link_up)
        self._serial_manager.disconnected.connect(self.portDisconnected)
        self._serial_manager.disconnected.connect(self._on_link_down)
        self._serial_manager.error_occurred.connect(self.errorOccurred)
        self._serial_manager.raw_rx.connect(self._on_raw_rx)
        self._serial_manager.raw_tx.connect(self._on_raw_tx)
        self._serial_manager.packet_received.connect(self._on_packet_received)
        self._serial_manager.baud_changed.connect(self._on_baud_changed)
        self._serial_manager.baud_command_sent.connect(self._on_baud_command_sent)
        try:
            self._serial_manager.ports_changed.connect(self._on_ports_changed)
        except Exception:
            pass

        self._chat_service.message_received.connect(self.chatMessageReceived)

        self._file_service.progress_updated.connect(self._on_transfer_progress)
        self._file_service.transfer_completed.connect(self.transferCompleted)
        self._file_service.transfer_failed.connect(self._on_transfer_failed)
        self._file_service.state_changed.connect(self.transferStateChanged)

        self._telemetry.telemetry_updated.connect(self.telemetryUpdated)
        self._telemetry.sniffer_frame_logged.connect(self.snifferFrameLogged)

    def _on_link_up(self, data_port: str, ctrl_port: str, baudrate: int) -> None:
        """Xử lý sự kiện mở kết nối thành công cặp cổng COM1 và COM2.

        Args:
            data_port: Tên cổng dữ liệu quang COM1.
            ctrl_port: Tên cổng điều khiển STM32 COM2 (hoặc chuỗi rỗng).
            baudrate: Tốc độ Baud quang áp dụng cho COM1.
        """
        self._is_connected = True
        self._optical_baud = int(baudrate)
        self._pending_optical_baud = None
        logger.info(
            "%s Bridge link UP: COM1=%s @%d COM2=%s @%d",
            LogTag.IPC_BRIDGE,
            data_port,
            baudrate,
            ctrl_port or "<none>",
            CONTROL_BAUDRATE,
        )
        self.portConnected.emit(data_port, ctrl_port, self._optical_baud)
        self.opticalBaudChanged.emit(self._optical_baud)
        self.baudControlStatus.emit(
            "ok",
            f"COM1 {data_port} @ {self._optical_baud} bps • COM2 "
            f"{ctrl_port or '<none>'} @ {CONTROL_BAUDRATE} bps",
        )
        if ctrl_port:
            QTimer.singleShot(200, lambda: self._request_baud(self._optical_baud))

    def _on_link_down(self) -> None:
        """Xóa trạng thái kết nối của cầu nối khi phần cứng bị ngắt."""
        was_connected = self._is_connected
        self._is_connected = False
        self._pending_optical_baud = None
        logger.info("%s Bridge link DOWN (was_connected=%s)", LogTag.IPC_BRIDGE, was_connected)

    def _request_baud(self, baudrate: int) -> None:
        """Gửi yêu cầu đổi tốc độ Baud qua COM2 và tái cấu hình lại COM1.

        Args:
            baudrate: Tốc độ Baud quang học đích (2400, 4800, 9600, 19200 bps).
        """
        if not self._is_connected:
            self.baudControlStatus.emit("error", "Link is not connected")
            return
        if baudrate not in SUPPORTED_BAUDRATES:
            self.baudControlStatus.emit("error", f"Unsupported optical baudrate {baudrate}")
            return
        self._pending_optical_baud = baudrate
        logger.info("%s Sending COM2 baud command: %d", LogTag.BAUD_CTRL, baudrate)
        self._serial_manager.send_baud_command(baudrate)

    def _on_baud_command_sent(self, baudrate: int, code: str) -> None:
        """Báo cáo mã lệnh ASCII chính xác đã gửi thành công sang vi điều khiển STM32 qua COM2.

        Args:
            baudrate: Tốc độ Baud quang học tương ứng.
            code: Chuỗi 2 ký tự mã lệnh ASCII ("00", "01", "10", "11").
        """
        logger.info(
            "%s COM2 command sent: %r -> %d bps", LogTag.BAUD_CTRL, code, baudrate
        )
        self.baudControlStatus.emit("ok", f"COM2 command {code!r} for {baudrate} bps")

    def _on_baud_changed(self, baudrate: int) -> None:
        """Lưu trữ tốc độ Baud quang học đã xác nhận và phát tín hiệu thông báo cho Web UI.

        Args:
            baudrate: Tốc độ Baud mới của kênh dữ liệu quang COM1.
        """
        previous = self._optical_baud
        self._optical_baud = int(baudrate)
        self._pending_optical_baud = None
        logger.info(
            "%s Optical baudrate applied: %d -> %d", LogTag.BAUD_CTRL, previous, self._optical_baud
        )
        self.opticalBaudChanged.emit(self._optical_baud)
        self.baudControlStatus.emit("ok", f"COM1 reconfigured to {self._optical_baud} bps")

    def _on_ports_changed(self, ports: list[PortInfo]) -> None:
        """Chuyển tiếp danh sách cổng cắm/rút mới cập nhật tới giao diện Web SPA."""
        try:
            payload = [self._serialize_port(port) for port in ports]
            logger.info("%s Hotplug ports -> UI: %s", LogTag.IPC_BRIDGE, payload)
            self.portsChanged.emit(json.dumps(payload))
        except Exception as exc:
            logger.debug(
                "%s portsChanged forward failed (%s: %s) ports=%s",
                LogTag.IPC_BRIDGE,
                type(exc).__name__,
                exc,
                ports,
            )

    @staticmethod
    def _serialize_port(port: object) -> dict[str, str | None]:
        """Chuẩn hóa đối tượng PortInfo (hoặc chuỗi) thành từ điển JSON."""
        if isinstance(port, PortInfo):
            return port.to_dict()
        device = str(port)
        return {
            "device": device,
            "description": "",
            "manufacturer": None,
            "vid": None,
            "pid": None,
            "hwid": "",
            "label": device,
        }

    def shutdown(self) -> None:
        """Dừng an toàn các dịch vụ và hủy phiên truyền file khi đóng cửa sổ hoặc thoát ứng dụng."""
        try:
            self._file_service.cancel_transfer()
        except Exception:
            pass

    def _on_raw_rx(self, data: bytes) -> None:
        """Xử lý dữ liệu byte thô nhận được từ kênh truyền và phát tín hiệu lên Web UI."""
        self._telemetry.record_bytes_received(len(data))
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(
                "%s Bridge RAW RX: len=%dB hex=%s",
                LogTag.IPC_BRIDGE,
                len(data),
                format_hex_preview(data),
            )
        self.rawRxData.emit(data.hex(" ").upper())

    def _on_raw_tx(self, data: bytes) -> None:
        """Xử lý dữ liệu byte thô phát đi qua kênh truyền và phát tín hiệu lên Web UI."""
        self._telemetry.record_bytes_sent(len(data))
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(
                "%s Bridge RAW TX: len=%dB hex=%s",
                LogTag.IPC_BRIDGE,
                len(data),
                format_hex_preview(data),
            )
        self.rawTxData.emit(data.hex(" ").upper())

    def _on_packet_received(self, pkt: object) -> None:
        """Đăng ký một khung truyền WinFormPacket hợp lệ với dịch vụ viễn trắc Telemetry.

        Args:
            pkt: Khung truyền do worker phân tích cú pháp thành công.
        """
        if not isinstance(pkt, WinFormPacket):
            logger.debug(
                "%s Bridge ignored non-WinFormPacket: type=%s",
                LogTag.IPC_BRIDGE,
                type(pkt).__name__,
            )
            return
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(
                "%s Bridge PKT seq=0x%08X len=%dB payload_hex=%s",
                LogTag.PROTO_FRAME,
                pkt.sequence,
                pkt.length,
                format_hex_preview(pkt.data, max_bytes=16),
            )
        self._telemetry.record_packet_received(pkt, latency_ms=0.0)

    def _on_transfer_progress(
        self, pct: int, speed_kbps: float, eta_sec: float, cur_chunk: int, total_chunks: int
    ) -> None:
        """Chuyển tiếp tiến độ truyền file và ghi nhận chunk dữ liệu vào viễn trắc."""
        self._telemetry.record_chunk_transferred()
        self.transferProgress.emit(pct, speed_kbps, eta_sec, cur_chunk, total_chunks)

    def _on_transfer_failed(self, reason: str) -> None:
        """Ghi nhận sự cố mất gói tin khi truyền file thất bại và phát tín hiệu lỗi."""
        self._telemetry.record_packet_lost()
        self.transferFailed.emit(reason)

    @property
    def telemetry_service(self) -> TelemetryService:
        """Đối tượng dịch vụ viễn trắc TelemetryService."""
        return self._telemetry

    @property
    def chat_service(self) -> ChatService:
        """Đối tượng dịch vụ tin nhắn ChatService."""
        return self._chat_service

    @property
    def file_service(self) -> FileTransferService:
        """Đối tượng dịch vụ truyền nhận file FileTransferService."""
        return self._file_service

    @Slot(result=str)
    def scanPorts(self) -> str:
        """Quét và trả về danh sách các cổng COM khả dụng kèm metadata dưới dạng JSON."""
        ports = self._serial_manager.get_port_info_list()
        return json.dumps([self._serialize_port(port) for port in ports])

    @Slot(result=str)
    def getLaunchConfig(self) -> str:
        """Trả về cấu hình khởi động và định danh ứng dụng dưới dạng chuỗi JSON cho Web UI.

        Returns:
            Đối tượng JSON chứa thông tin instance id, tên hiển thị, vai trò, cổng ưu tiên,
            tốc độ baud, danh sách baudrate hỗ trợ và đường dẫn thư mục dữ liệu.
        """
        ctx = self._instance_context
        cfg = self._launch_config
        payload: dict[str, object] = {
            "instance": ctx.instance_id if ctx else None,
            "display_name": ctx.display_name if ctx else "Single Node",
            "role": ctx.role_label if ctx else "",
            "window_title": ctx.window_title if ctx else "SpectraLink IR-OWC Workstation",
            "data_dir": str(ctx.data_dir) if ctx else "",
            "preferred_port": cfg.preferred_port if cfg else None,
            "com1_port": cfg.effective_tx_port if cfg else None,
            "com2_port": cfg.effective_rx_port if cfg else None,
            "tx_port": cfg.effective_tx_port if cfg else None,
            "rx_port": cfg.effective_rx_port if cfg else None,
            "baud": cfg.baud if cfg else DEFAULT_BAUD,
            "default_baudrate": cfg.baud if cfg else DEFAULT_BAUD,
            "physical_baudrate": CONTROL_BAUDRATE,
            "control_baudrate": CONTROL_BAUDRATE,
            "default_optical_baudrate": self._optical_baud,
            "supported_baudrates": self._configured_baudrates(),
            "transport": cfg.transport if cfg else "auto",
            "link_role": cfg.effective_link_role if cfg else "off",
            "link_host": cfg.link_host if cfg else DEFAULT_LINK_HOST,
            "link_base_port": cfg.link_base_port if cfg else DEFAULT_LINK_BASE_PORT,
            "auto_connect": cfg.auto_connect if cfg else False,
        }
        return json.dumps(payload)

    def _configured_baudrates(self) -> list[int]:
        """Lấy danh sách các tốc độ Baud được cấu hình từ cài đặt hoặc dùng mặc định."""
        raw = self._storage.get_setting("supported_baudrates", "")
        if raw:
            try:
                parsed: object = json.loads(raw)
            except (TypeError, ValueError):
                parsed = [part.strip() for part in str(raw).split(",")]
            if isinstance(parsed, list):
                values: list[int] = []
                for item in parsed:
                    try:
                        value = int(item)
                    except (TypeError, ValueError):
                        continue
                    if value in SUPPORTED_BAUDRATES:
                        values.append(value)
                if values:
                    return values
        return list(SUPPORTED_BAUDRATES)

    def _default_export_dir(self) -> str:
        """Trả về đường dẫn thư mục xuất báo cáo tương ứng cho phiên tiến trình."""
        if self._instance_context is not None:
            return str(self._instance_context.logs_dir)
        return str(get_logs_dir())

    @Slot(result=str)
    def getAvailableBaudrates(self) -> str:
        """Trả về danh sách các tốc độ Baud khả dụng dưới dạng mảng JSON."""
        try:
            return json.dumps(self._configured_baudrates())
        except Exception as exc:
            logger.exception(
                "%s getAvailableBaudrates FAILED (%s: %s)",
                LogTag.IPC_BRIDGE,
                type(exc).__name__,
                exc,
            )
            return json.dumps(list(SUPPORTED_BAUDRATES))

    @Slot(result=str)
    def getConnectedPorts(self) -> str:
        """Trả về thông tin cặp cổng COM1/COM2 đang mở và tốc độ Baud dưới dạng JSON."""
        payload = {
            "com1_port": self._serial_manager.current_data_port,
            "com2_port": self._serial_manager.current_ctrl_port,
            "tx_port": self._serial_manager.current_data_port,
            "rx_port": self._serial_manager.current_data_port,
            "physical_baudrate": CONTROL_BAUDRATE,
            "control_baudrate": CONTROL_BAUDRATE,
            "baudrate": self._optical_baud,
            "pending_baudrate": self._pending_optical_baud,
        }
        return json.dumps(payload)

    @Slot(str, int)
    def connectPort(self, port_name: str, baudrate: int) -> None:
        """Mở kết nối một cổng dữ liệu đơn lẻ không kèm kênh điều khiển.

        Args:
            port_name: Tên định danh cổng dữ liệu COM1.
            baudrate: Tốc độ Baud quang học.
        """
        self.connectDualPorts(port_name, "", baudrate)

    @Slot(str, str, int)
    def connectDualPorts(self, com1_data: str, com2_ctrl: str, baudrate: int) -> None:
        """Mở đồng thời hai cổng COM1 (dữ liệu quang) và COM2 (điều khiển STM32 cố định 115200 bps).

        Args:
            com1_data: Tên cổng dữ liệu quang COM1.
            com2_ctrl: Tên cổng điều khiển STM32 COM2, hoặc chuỗi rỗng.
            baudrate: Tốc độ Baud quang học áp dụng cho COM1.
        """
        try:
            data_name = (com1_data or "").strip()
            ctrl_name = (com2_ctrl or "").strip()
            if not data_name:
                logger.warning(
                    "%s Connect rejected: empty COM1 port name (baud=%d)",
                    LogTag.HW_SERIAL,
                    baudrate,
                )
                self.errorOccurred.emit("Connect failed: empty COM1 port name")
                return
            if baudrate == CONTROL_BAUDRATE:
                baudrate = DEFAULT_OPTICAL_BAUDRATE
            if baudrate not in SUPPORTED_BAUDRATES:
                logger.warning(
                    "%s Connect rejected: unsupported optical baudrate=%d COM1=%s COM2=%s",
                    LogTag.BAUD_CTRL,
                    baudrate,
                    data_name,
                    ctrl_name or "<none>",
                )
                self.errorOccurred.emit(
                    f"Connect failed: unsupported optical baudrate {baudrate}"
                )
                return
            self._optical_baud = int(baudrate)
            self._pending_optical_baud = None
            logger.info(
                "%s UI dual connect requested: COM1=%s @%d COM2=%s @%d connected=%s",
                LogTag.HW_SERIAL,
                data_name,
                self._optical_baud,
                ctrl_name or "<none>",
                CONTROL_BAUDRATE,
                self._is_connected,
            )
            self._serial_manager.connect_ports(
                data_name, ctrl_name or None, self._optical_baud, CONTROL_BAUDRATE
            )
        except Exception as exc:
            logger.exception(
                "%s connectDualPorts slot FAILED (%s: %s) COM1=%s COM2=%s baud=%d",
                LogTag.HW_SERIAL,
                type(exc).__name__,
                exc,
                com1_data,
                com2_ctrl,
                baudrate,
            )
            self.errorOccurred.emit(f"Connect failed: {exc}")

    @Slot(str, str, int)
    def connectHardwarePorts(self, com1_data: str, com2_ctrl: str, baud_ir: int) -> None:
        """Tên hàm tương thích kiểu WinForm cho phương thức connectDualPorts.

        Args:
            com1_data: Tên cổng nối tiếp dữ liệu quang COM1.
            com2_ctrl: Tên cổng nối tiếp điều khiển STM32 COM2.
            baud_ir: Tốc độ Baud quang học.
        """
        self.connectDualPorts(com1_data, com2_ctrl, baud_ir)

    @Slot(int, result=str)
    def setOpticalBaud(self, baudrate: int) -> str:
        """Yêu cầu đổi tốc độ Baud quang học bằng cách gửi lệnh qua COM2 và cấu hình lại COM1.

        Args:
            baudrate: Tốc độ Baud thuộc danh mục hỗ trợ (2400, 4800, 9600, 19200 bps).

        Returns:
            Chuỗi JSON chứa trạng thái kết quả và baudrate mới.
        """
        return self.sendBaudCommand(baudrate)

    @Slot(int, result=str)
    def sendBaudCommand(self, baudrate: int) -> str:
        """Gửi mã lệnh 2 ký tự ASCII chuyển đổi tốc độ Baud quang qua cổng điều khiển COM2.

        Args:
            baudrate: Tốc độ Baud quang học cần chuyển đổi.

        Returns:
            Chuỗi JSON chứa trạng thái ok, kết nối và trạng thái đang chờ thực thi.
        """
        try:
            request = int(baudrate)
        except (TypeError, ValueError):
            return json.dumps({"ok": False, "error": f"Invalid baudrate {baudrate!r}"})
        if request not in SUPPORTED_BAUDRATES:
            logger.warning(
                "%s Baud command rejected: unsupported rate=%d", LogTag.BAUD_CTRL, request
            )
            return json.dumps(
                {"ok": False, "error": f"Unsupported optical baudrate {request}"}
            )
        if not self._is_connected:
            self._optical_baud = request
            logger.info(
                "%s Baud command while disconnected: storing desired=%d",
                LogTag.BAUD_CTRL,
                request,
            )
            return json.dumps({"ok": True, "connected": False, "baudrate": request})
        if self._file_service.state.is_active:
            logger.warning(
                "%s Baud command rejected during transfer: state=%s requested=%d",
                LogTag.BAUD_CTRL,
                self._file_service.state.name,
                request,
            )
            return json.dumps(
                {
                    "ok": False,
                    "error": "Cannot change optical baudrate during an active transfer",
                    "state": self._file_service.state.name,
                }
            )
        if request == self._optical_baud and self._pending_optical_baud is None:
            return json.dumps({"ok": True, "connected": True, "baudrate": request})
        self._request_baud(request)
        return json.dumps(
            {"ok": True, "connected": True, "baudrate": request, "pending": True}
        )

    @Slot(int, result=str)
    def sendBaudSelection(self, baudrate: int) -> str:
        """Tên hàm tương thích WinForm cho phương thức sendBaudCommand.

        Args:
            baudrate: Tốc độ Baud quang học đích.

        Returns:
            Chuỗi JSON chứa kết quả thực thi.
        """
        return self.sendBaudCommand(baudrate)

    @Slot()
    def disconnectPort(self) -> None:
        """Yêu cầu ngắt kết nối toàn bộ các cổng phần cứng COM1 và COM2 đang mở."""
        try:
            logger.info(
                "%s UI disconnect requested (connected=%s)", LogTag.HW_SERIAL, self._is_connected
            )
            self._serial_manager.disconnect_port()
        except Exception as exc:
            logger.exception(
                "%s Disconnect slot FAILED (%s: %s)",
                LogTag.HW_SERIAL,
                type(exc).__name__,
                exc,
            )
            self.errorOccurred.emit(f"Disconnect failed: {exc}")

    @Slot()
    def sendPing(self) -> None:
        """Từ chối yêu cầu Ping cũ: giao thức chuẩn WinForm C# và firmware không có opcode PING."""
        logger.warning("%s Legacy ping rejected: unsupported by WinForm protocol", LogTag.PROTO_FRAME)
        self.errorOccurred.emit("Ping is not supported by the WinForm firmware protocol")

    @Slot(float)
    def setAutoPingInterval(self, seconds: float) -> None:
        """Bỏ qua chu kỳ kiểm tra sức khỏe tự động ping cũ (giữ lại để tương thích UI).

        Args:
            seconds: Khoảng thời gian định kỳ được yêu cầu.
        """
        logger.info("%s Auto-ping disabled (requested %.2fs)", LogTag.IPC_BRIDGE, seconds)

    @Slot(str)
    def sendChat(self, text: str) -> None:
        """Truyền tin nhắn văn bản UTF-8 tới trạm đối tác qua ChatService.

        Args:
            text: Nội dung văn bản tin nhắn người dùng nhập.
        """
        try:
            if not (text or "").strip():
                logger.debug("%s sendChat rejected: empty message", LogTag.CHAT_SVC)
                self.errorOccurred.emit("Chat message is empty")
                return
            preview = text.strip()[:80]
            logger.debug(
                "%s UI sendChat: len=%dB preview=%r connected=%s",
                LogTag.CHAT_SVC,
                len(text.encode("utf-8")),
                preview,
                self._is_connected,
            )
            success = self._chat_service.send_message(text)
            if not success:
                self.errorOccurred.emit(
                    "Optical link is busy. Please wait for the current transfer to complete."
                )
        except Exception as exc:
            logger.exception(
                "%s sendChat FAILED (%s: %s) preview=%r",
                LogTag.CHAT_SVC,
                type(exc).__name__,
                exc,
                (text or "")[:80],
            )
            self.errorOccurred.emit(f"Chat failed: {exc}")

    @Slot(str)
    def startFileTransfer(self, file_path: str) -> None:
        """Kích hoạt phiên truyền file qua đường truyền quang Open-Loop.

        Args:
            file_path: Đường dẫn tệp tin trên ổ đĩa cần truyền đi.
        """
        try:
            if not (file_path or "").strip():
                logger.warning("%s startFileTransfer rejected: empty path", LogTag.FILE_SVC)
                self.errorOccurred.emit("File transfer failed: empty path")
                return
            candidate = Path(file_path)
            if not candidate.is_file():
                logger.warning(
                    "%s startFileTransfer rejected: not found path=%s connected=%s",
                    LogTag.FILE_SVC,
                    file_path,
                    self._is_connected,
                )
                self.errorOccurred.emit(f"File transfer failed: not found: {file_path}")
                return
            try:
                size = candidate.stat().st_size
            except OSError:
                size = -1
            logger.info(
                "%s UI startFileTransfer: path=%s size=%dB connected=%s stream_state=%s",
                LogTag.FILE_SVC,
                file_path,
                size,
                self._is_connected,
                self._file_service.state.name,
            )
            self._file_service.start_transfer(file_path)
        except Exception as exc:
            logger.exception(
                "%s startFileTransfer FAILED (%s: %s) path=%s",
                LogTag.FILE_SVC,
                type(exc).__name__,
                exc,
                file_path,
            )
            self.errorOccurred.emit(f"File transfer failed: {exc}")

    @Slot()
    def abortFileTransfer(self) -> None:
        """Hủy bỏ phiên truyền hoặc nhận file quang học đang diễn ra."""
        try:
            logger.info(
                "%s UI abortFileTransfer: stream_state=%s",
                LogTag.FILE_SVC,
                self._file_service.state.name,
            )
            self._file_service.cancel_transfer()
        except Exception as exc:
            logger.exception(
                "%s abortFileTransfer FAILED (%s: %s)",
                LogTag.FILE_SVC,
                type(exc).__name__,
                exc,
            )
            self.errorOccurred.emit(f"Abort failed: {exc}")

    @Slot()
    def stopFileTransfer(self) -> None:
        """Tên hàm tương thích kiểu WinForm cho phương thức abortFileTransfer."""
        self.abortFileTransfer()

    @Slot(result=str)
    def chooseSaveFolder(self) -> str:
        """Mở hộp thoại chọn thư mục lưu trữ file nhận và cập nhật vào cấu hình cài đặt.

        Returns:
            Đường dẫn thư mục đã chọn, hoặc chuỗi rỗng nếu người dùng hủy hộp thoại.
        """
        try:
            current = str(self._file_service.transfers_dir_path)
            chosen = QFileDialog.getExistingDirectory(
                self._dialog_parent(),
                "Select Receive Folder",
                current,
            )
            if not chosen:
                return ""
            applied = self._file_service.set_transfers_dir(chosen)
            payload = json.dumps(self._file_service.get_config())
            self.settingsChanged.emit(payload)
            logger.info("%s Receive folder set via UI: %s", LogTag.IPC_BRIDGE, applied)
            return str(applied)
        except Exception as exc:
            logger.exception("%s chooseSaveFolder failed: %s", LogTag.IPC_BRIDGE, exc)
            self.errorOccurred.emit(f"Choose folder failed: {exc}")
            return ""

    @Slot(result=str)
    def chooseDestinationFolder(self) -> str:
        """Tên hàm tương thích WinForm cho phương thức chooseSaveFolder."""
        return self.chooseSaveFolder()

    @Slot(bool, result=str)
    def setAutoSaveOption(self, enabled: bool) -> str:
        """Lưu trữ tùy chọn AutoSave (giữ lại file một phần khi lỗi) và trả về cấu hình mới.

        Args:
            enabled: True để giữ lại file nhận một phần (.part), False để xóa khi lỗi.

        Returns:
            Chuỗi JSON chứa cấu hình truyền nhận file đã cập nhật.
        """
        try:
            config = self._file_service.update_config(auto_save=bool(enabled))
            payload = json.dumps(config)
            self.settingsChanged.emit(payload)
            logger.info("%s AutoSave set via UI: %s", LogTag.IPC_BRIDGE, enabled)
            return payload
        except Exception as exc:
            logger.exception("%s setAutoSaveOption failed: %s", LogTag.IPC_BRIDGE, exc)
            self.errorOccurred.emit(f"AutoSave update failed: {exc}")
            return json.dumps({"ok": False, "error": str(exc)})

    def _dialog_parent(self) -> QWidget | None:
        """Trả về QWidget cha cho các hộp thoại hệ thống nếu có."""
        parent = self.parent()
        return parent if isinstance(parent, QWidget) else None

    @Slot(result=str)
    def openFileDialog(self) -> str:
        """Mở hộp thoại chọn tệp tin của hệ điều hành và trả về đường dẫn tệp đã chọn.

        Returns:
            Đường dẫn tệp tin được chọn hoặc chuỗi rỗng nếu bị hủy.
        """
        try:
            path, _ = QFileDialog.getOpenFileName(
                self._dialog_parent(),
                "Select File for Optical Transmission",
                "",
                "All Files (*.*);;Documents (*.pdf;*.docx;*.txt);;Images (*.png;*.jpg;*.bin)",
            )
            return path
        except Exception as exc:
            logger.exception("%s openFileDialog failed: %s", LogTag.IPC_BRIDGE, exc)
            self.errorOccurred.emit(f"Open file dialog failed: {exc}")
            return ""

    @Slot(str, result=str)
    def openTransferLocation(self, file_path: str) -> str:
        """Mở thư mục chứa file đã truyền nhận trong trình quản lý tệp tin của hệ điều hành.

        Args:
            file_path: Đường dẫn tệp tin ghi nhận trong lịch sử.

        Returns:
            Đường dẫn thư mục đã mở nếu thành công, hoặc chuỗi rỗng nếu tệp không tồn tại.
        """
        candidate = Path(file_path).expanduser()
        if not candidate.exists():
            logger.warning(
                "%s openTransferLocation missing: recorded=%s cwd=%s",
                LogTag.IPC_BRIDGE,
                file_path,
                Path.cwd(),
            )
            return ""

        target = candidate if candidate.is_dir() else candidate.parent
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))
        logger.info(
            "%s Opened transfer location: recorded=%s opened=%s exists=%s",
            LogTag.IPC_BRIDGE,
            file_path,
            target,
            candidate.exists(),
        )
        return str(target)

    @Slot(result=str)
    def getSettings(self) -> str:
        """Truy xuất cấu hình cài đặt truyền file hiện tại dưới dạng chuỗi JSON."""
        try:
            config = self._file_service.get_config()
        except Exception as exc:
            logger.exception(
                "%s getSettings FAILED (%s: %s)",
                LogTag.IPC_BRIDGE,
                type(exc).__name__,
                exc,
            )
            return json.dumps({"ok": False, "error": str(exc)})
        return json.dumps(config)

    @Slot(str, result=str)
    def saveSettings(self, settings_json: str) -> str:
        """Xác thực và lưu trữ cấu hình truyền nhận file người dùng nhập từ Web UI.

        Args:
            settings_json: Chuỗi JSON chứa các trường cấu hình cài đặt cần cập nhật.

        Returns:
            Chuỗi JSON chứa cờ ok và cấu hình sau khi áp dụng hoặc thông báo lỗi.
        """
        try:
            data = json.loads(settings_json or "{}")
        except (json.JSONDecodeError, TypeError) as exc:
            logger.warning(
                "%s saveSettings rejected: invalid JSON (%s)",
                LogTag.IPC_BRIDGE,
                exc,
            )
            return json.dumps({"ok": False, "error": f"Invalid settings JSON: {exc}"})
        if not isinstance(data, dict):
            return json.dumps({"ok": False, "error": "Settings must be a JSON object"})
        try:
            max_bytes: int | None = None
            if data.get("max_file_size_bytes") is not None:
                max_bytes = int(float(str(data["max_file_size_bytes"])))
            elif data.get("max_file_size_mb") is not None:
                max_bytes = int(float(str(data["max_file_size_mb"])) * 1024 * 1024)
            raw_dir = data.get("transfers_dir")
            transfers_dir: str | None = str(raw_dir) if raw_dir is not None else None
            auto_save: bool | None = None
            if "auto_save" in data:
                raw_flag = data["auto_save"]
                if isinstance(raw_flag, str):
                    auto_save = raw_flag.strip().lower() in ("1", "true", "yes", "on")
                else:
                    auto_save = bool(raw_flag)
            if (
                max_bytes is None
                and (transfers_dir is None or not transfers_dir.strip())
                and auto_save is None
            ):
                return json.dumps({"ok": False, "error": "No settings to update"})
            config = self._file_service.update_config(
                max_file_size_bytes=max_bytes,
                transfers_dir=transfers_dir,
                auto_save=auto_save,
            )
            payload = json.dumps(config)
            self.settingsChanged.emit(payload)
            logger.info("%s Settings saved via UI: %s", LogTag.IPC_BRIDGE, payload)
            return json.dumps({"ok": True, **config})
        except (ValueError, TypeError, OSError) as exc:
            logger.warning(
                "%s saveSettings rejected (%s: %s) input=%r",
                LogTag.IPC_BRIDGE,
                type(exc).__name__,
                exc,
                (settings_json or "")[:200],
            )
            return json.dumps({"ok": False, "error": str(exc)})

    @Slot(result=str)
    def browseTransfersDir(self) -> str:
        """Mở hộp thoại lựa chọn thư mục lưu trữ mặc định cho file nhận.

        Returns:
            Đường dẫn thư mục được chọn hoặc chuỗi rỗng nếu bị hủy.
        """
        try:
            current = str(self._file_service.transfers_dir_path)
            chosen = QFileDialog.getExistingDirectory(
                self._dialog_parent(),
                "Select Default Transfers Directory",
                current,
            )
            if not chosen:
                return ""
            applied = self._file_service.set_transfers_dir(chosen)
            self.settingsChanged.emit(json.dumps(self._file_service.get_config()))
            return str(applied)
        except Exception as exc:
            logger.exception("%s browseTransfersDir failed: %s", LogTag.IPC_BRIDGE, exc)
            self.errorOccurred.emit(f"Browse directory failed: {exc}")
            return ""

    @Slot(result=str)
    def getChatHistory(self) -> str:
        """Truy xuất lịch sử tin nhắn trò chuyện bền vững từ tệp JSON dưới dạng chuỗi JSON."""
        history = self._storage.get_chat_history(limit=200)
        return json.dumps(history)

    @Slot(result=str)
    def getFileHistory(self) -> str:
        """Truy xuất lịch sử các phiên truyền nhận file từ tệp JSON dưới dạng chuỗi JSON."""
        transfers = self._storage.get_file_transfers(limit=50)
        return json.dumps(transfers)

    @Slot(result=str)
    def getTelemetryStats(self) -> str:
        """Truy xuất bản sao tóm tắt các chỉ số viễn trắc hiện hành dưới dạng chuỗi JSON."""
        summary = self._telemetry.get_summary()
        return json.dumps(summary)

    @Slot(result=str)
    def getSnifferRecords(self) -> str:
        """Truy xuất danh sách các bản ghi bắt gói tin Sniffer gần nhất dưới dạng chuỗi JSON."""
        records = self._telemetry.get_sniffer_records(limit=100)
        return json.dumps(records)

    @Slot(result=str)
    def exportTelemetryCsv(self) -> str:
        """Xuất lịch sử dữ liệu viễn trắc ra tệp CSV thông qua hộp thoại lưu tệp của hệ thống.

        Returns:
            Đường dẫn tệp đích đã lưu thành công hoặc chuỗi rỗng nếu bị hủy.
        """
        try:
            default_dir = self._default_export_dir()
            dest_path, _ = QFileDialog.getSaveFileName(
                self._dialog_parent(),
                "Export Telemetry CSV",
                f"{default_dir}/telemetry_export_{int(time.time())}.csv",
                "CSV Files (*.csv);;All Files (*.*)",
            )
            if not dest_path:
                return ""

            self._telemetry.export_telemetry_csv(Path(dest_path))
            logger.info(
                "%s Exported telemetry CSV: dest=%s connected=%s",
                LogTag.IPC_BRIDGE,
                dest_path,
                self._is_connected,
            )
            return dest_path
        except Exception as exc:
            logger.exception(
                "%s Export telemetry CSV FAILED (%s: %s)",
                LogTag.IPC_BRIDGE,
                type(exc).__name__,
                exc,
            )
            self.errorOccurred.emit(f"Export telemetry failed: {exc}")
            return ""

    @Slot(result=str)
    def exportTransfersCsv(self) -> str:
        """Xuất lịch sử truyền nhận file ra tệp CSV thông qua hộp thoại lưu tệp của hệ thống.

        Returns:
            Đường dẫn tệp đích đã lưu thành công hoặc chuỗi rỗng nếu bị hủy.
        """
        try:
            default_dir = self._default_export_dir()
            dest_path, _ = QFileDialog.getSaveFileName(
                self._dialog_parent(),
                "Export File Transfers CSV",
                f"{default_dir}/transfers_export_{int(time.time())}.csv",
                "CSV Files (*.csv);;All Files (*.*)",
            )
            if not dest_path:
                return ""

            self._storage.export_transfers_csv(Path(dest_path))
            logger.info(
                "%s Exported transfers CSV: dest=%s connected=%s",
                LogTag.IPC_BRIDGE,
                dest_path,
                self._is_connected,
            )
            return dest_path
        except Exception as exc:
            logger.exception(
                "%s Export transfers CSV FAILED (%s: %s)",
                LogTag.IPC_BRIDGE,
                type(exc).__name__,
                exc,
            )
            self.errorOccurred.emit(f"Export transfers failed: {exc}")
            return ""

    @Slot()
    def resetTelemetryCounters(self) -> None:
        """Đặt lại toàn bộ các bộ đếm viễn trắc và bộ đệm bắt gói tin Sniffer."""
        summary = self._telemetry.get_summary()
        self._telemetry.reset_counters()
        logger.info("%s Reset telemetry via UI (was %s)", LogTag.IPC_BRIDGE, summary)

    @Slot()
    def closeWindow(self) -> None:
        """Phát tín hiệu yêu cầu đóng cửa sổ ứng dụng."""
        self.closeWindowRequested.emit()

    @Slot()
    def minimizeWindow(self) -> None:
        """Phát tín hiệu yêu cầu thu nhỏ cửa sổ ứng dụng xuống thanh tác vụ."""
        self.minimizeWindowRequested.emit()

    @Slot()
    def toggleMaximizeWindow(self) -> None:
        """Phát tín hiệu yêu cầu phóng to hoặc khôi phục kích thước cửa sổ ứng dụng."""
        self.maximizeWindowRequested.emit()
