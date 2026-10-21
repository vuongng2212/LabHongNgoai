"""QWebChannel Bridge exposing Python Core Engine & Services to Web UI JavaScript."""

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
from ir_terminal.protocol.constants import DEFAULT_HEALTH_CHECK_SEC, Opcode
from ir_terminal.protocol.packet import Packet
from ir_terminal.services.chat_service import ChatService
from ir_terminal.services.file_transfer_service import FileTransferService, TransferState
from ir_terminal.services.storage_service import StorageService
from ir_terminal.services.telemetry_service import TelemetryService
from ir_terminal.utils.logger import LogTag, format_frame_summary, format_hex_preview, opcode_name
from ir_terminal.utils.path_helpers import get_logs_dir

logger = logging.getLogger(__name__)

# Module-level ping payload constants (refactor: single bytes objects, no
# per-tick re-allocation or len() recompute; wire bytes unchanged).
_PING_HEALTH_PAYLOAD: bytes = b"IR_HEALTH"
_PING_MANUAL_PAYLOAD: bytes = b"IR_PING"


class BackendBridge(QObject):
    """Exposes methods and signals to Chromium JavaScript via QWebChannel."""

    # Outgoing Link & Hardware Signals to JavaScript
    portConnected: ClassVar[Signal] = Signal(str, int)  # (port, baud)
    portDisconnected: ClassVar[Signal] = Signal()
    errorOccurred: ClassVar[Signal] = Signal(str)
    pingResult: ClassVar[Signal] = Signal(float, int)  # (rtt_ms, seq)
    healthResult: ClassVar[Signal] = Signal(float, int)  # (rtt_ms, seq) silent auto check
    rawRxData: ClassVar[Signal] = Signal(str)  # hex string
    rawTxData: ClassVar[Signal] = Signal(str)  # hex string

    # Window Control Signals
    closeWindowRequested: ClassVar[Signal] = Signal()
    minimizeWindowRequested: ClassVar[Signal] = Signal()
    maximizeWindowRequested: ClassVar[Signal] = Signal()

    # Chat Signals
    chatMessageReceived: ClassVar[Signal] = Signal(str, str, bool)  # (text, time, is_outgoing)
    chatFrameInspected: ClassVar[Signal] = Signal(str, int, int, int)  # (hex, seq, cmd, len)

    # File Transfer Signals
    transferProgress: ClassVar[Signal] = Signal(
        int, float, float, int, int
    )  # (pct, speed_kbps, eta_sec, cur_chunk, total_chunks)
    transferCompleted: ClassVar[Signal] = Signal(
        str, int, float, str
    )  # (filename, bytes_sent, elapsed_sec, crc_hex)
    transferFailed: ClassVar[Signal] = Signal(str)  # (reason)
    transferStateChanged: ClassVar[Signal] = Signal(str)  # (state)

    # Telemetry & Sniffer Signals
    telemetryUpdated: ClassVar[Signal] = Signal(
        float, float, float, int, int
    )  # (speed_kbps, loss_pct, rtt_ms, chunks_count, frames_count)
    snifferFrameLogged: ClassVar[Signal] = Signal(
        str, str, str, str, str, str, float
    )  # (time_str, frame_id, carrier, type_str, payload_preview, checksum_status, latency_ms)
    portsChanged: ClassVar[Signal] = Signal(str)  # JSON list of ports
    chatTruncated: ClassVar[Signal] = Signal(str, int)  # (reason, original_bytes)
    settingsChanged: ClassVar[Signal] = Signal(str)  # JSON transfer settings

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
        """Initializes backend bridge connecting injected services to Web UI.

        Args:
            serial_manager: Active SerialManager instance.
            chat_service: Optional injected ChatService instance.
            file_service: Optional injected FileTransferService instance.
            telemetry_service: Optional injected TelemetryService instance.
            storage_service: Optional injected StorageService instance.
            launch_config: Optional parsed launch options (exposed to the UI).
            instance_context: Optional per-instance layout/identity.
        """
        super().__init__()
        self._serial_manager = serial_manager
        self._launch_config = launch_config
        self._instance_context = instance_context
        self._storage = storage_service or StorageService()
        self._chat_service = chat_service or ChatService(self._serial_manager, self._storage)
        self._file_service = file_service or FileTransferService(
            self._serial_manager, storage_service=self._storage
        )
        self._telemetry = telemetry_service or TelemetryService(storage_service=self._storage)

        self._last_ping_time: float = 0.0
        self._ping_seq: int = 1

        self._is_connected = False
        self._health_interval_sec = DEFAULT_HEALTH_CHECK_SEC
        self._health_timer = QTimer(self)
        self._health_timer.setSingleShot(False)
        self._health_timer.setInterval(int(self._health_interval_sec * 1000))
        self._health_timer.timeout.connect(self._on_health_tick)

        self._wire_signals()

    def _wire_signals(self) -> None:
        """Connects underlying core services to bridge signals and telemetry listeners."""
        # Serial Manager
        self._serial_manager.connected.connect(self.portConnected)
        self._serial_manager.connected.connect(self._on_link_up)
        self._serial_manager.disconnected.connect(self.portDisconnected)
        self._serial_manager.disconnected.connect(self._on_link_down)
        self._serial_manager.error_occurred.connect(self.errorOccurred)
        self._serial_manager.raw_rx.connect(self._on_raw_rx)
        self._serial_manager.raw_tx.connect(self._on_raw_tx)
        self._serial_manager.packet_received.connect(self._on_packet_received)
        try:
            self._serial_manager.ports_changed.connect(self._on_ports_changed)
            self._serial_manager.framer_error.connect(self._on_framer_error)
        except Exception:
            pass

        # Chat Service
        self._chat_service.message_received.connect(self.chatMessageReceived)
        self._chat_service.frame_inspected.connect(self.chatFrameInspected)
        try:
            self._chat_service.message_truncated.connect(self.chatTruncated)
        except Exception:
            pass

        # File Transfer Service
        self._file_service.progress_updated.connect(self._on_transfer_progress)
        self._file_service.transfer_completed.connect(self.transferCompleted)
        self._file_service.transfer_failed.connect(self._on_transfer_failed)
        self._file_service.state_changed.connect(self.transferStateChanged)

        # Telemetry Service
        self._telemetry.telemetry_updated.connect(self.telemetryUpdated)
        self._telemetry.sniffer_frame_logged.connect(self.snifferFrameLogged)

    def _on_link_up(self, port: str, baud: int) -> None:
        """Starts periodic health checks once the optical link is connected."""
        self._is_connected = True
        if self._health_interval_sec > 0:
            self._health_timer.start()
        logger.info(
            "%s Bridge link UP: port=%s baud=%d health_every=%.1fs auto_ping=%s",
            LogTag.IPC_BRIDGE,
            port,
            baud,
            self._health_interval_sec,
            self._health_timer.isActive(),
        )

    def _on_link_down(self) -> None:
        """Stops periodic health checks once the optical link is closed."""
        was_connected = self._is_connected
        self._is_connected = False
        self._health_timer.stop()
        logger.info(
            "%s Bridge link DOWN (was_connected=%s) health_timer_stopped",
            LogTag.IPC_BRIDGE,
            was_connected,
        )

    def _on_ports_changed(self, ports: list[PortInfo]) -> None:
        """Forwards hotplug port-list changes to the Web UI."""
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
        """Normalizes a PortInfo (or legacy string) into a JSON-friendly dict."""
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

    def _on_framer_error(self, kind: str, count: int) -> None:
        """Counts framer corruption as link loss for telemetry."""
        try:
            logger.debug(
                "%s Framer %s x%d -> telemetry loss counter",
                LogTag.PROTO_FRAME,
                kind,
                count,
            )
            self._telemetry.record_framer_error(kind, int(count))
        except Exception as exc:
            logger.debug(
                "%s framer_error forward failed (%s: %s) kind=%s count=%d",
                LogTag.IPC_BRIDGE,
                type(exc).__name__,
                exc,
                kind,
                count,
            )

    def shutdown(self) -> None:
        """Stops health timer (called on window close / app quit)."""
        try:
            self._health_timer.stop()
        except Exception:
            pass

    def _on_health_tick(self) -> None:
        """Transmits one silent link health-check ping when the channel is idle."""
        if not self._is_connected:
            return
        if self._file_service.state in (
            TransferState.HANDSHAKING,
            TransferState.TRANSFERRING,
            TransferState.FINALIZING,
        ):
            return
        self._ping_seq = (self._ping_seq + 1) % 256
        self._last_ping_time = time.time()
        self._serial_manager.send_packet(seq=self._ping_seq, opcode=Opcode.PING, payload=_PING_HEALTH_PAYLOAD)
        self._telemetry.record_packet_sent(self._ping_seq, Opcode.PING, len(_PING_HEALTH_PAYLOAD))

    def _on_raw_rx(self, data: bytes) -> None:
        """Handles incoming raw bytes from transport."""
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
        """Handles outgoing raw bytes to transport."""
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
        """Handles parsed incoming packet for RTT and telemetry tracking."""
        if not isinstance(pkt, Packet):
            logger.debug(
                "%s Bridge ignored non-Packet: type=%s",
                LogTag.IPC_BRIDGE,
                type(pkt).__name__,
            )
            return

        rtt_ms = 0.0
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(
                "%s Bridge PKT %s (%s) crc=0x%08X payload_hex=%s",
                LogTag.PROTO_FRAME,
                format_frame_summary(pkt.seq, pkt.opcode, len(pkt.payload)),
                opcode_name(pkt.opcode),
                pkt.crc32 & 0xFFFFFFFF,
                format_hex_preview(pkt.payload, max_bytes=16),
            )
        if pkt.opcode == Opcode.PING:
            # RX responder: echo PONG so a remote PC gets RTT without firmware help.
            # PONG never triggers a further reply, so no ping-pong loop. Guard on
            # link state to avoid write-error spam when disconnected.
            if self._is_connected:
                self._serial_manager.send_packet(
                    seq=pkt.seq, opcode=Opcode.PONG, payload=pkt.payload
                )
                self._telemetry.record_packet_sent(pkt.seq, Opcode.PONG, len(pkt.payload))
                logger.info(
                    "%s Auto-PONG %s echo_len=%dB payload_hex=%s",
                    LogTag.PROTO_FRAME,
                    format_frame_summary(pkt.seq, Opcode.PONG, len(pkt.payload)),
                    len(pkt.payload),
                    format_hex_preview(pkt.payload, max_bytes=16),
                )
            else:
                logger.debug(
                    "%s PING ignored (link down): %s",
                    LogTag.PROTO_FRAME,
                    format_frame_summary(pkt.seq, pkt.opcode, len(pkt.payload)),
                )
        elif pkt.opcode == Opcode.PONG:
            if self._last_ping_time > 0:
                rtt_ms = (time.time() - self._last_ping_time) * 1000.0
            else:
                rtt_ms = 0.0
            self._telemetry.record_rtt(rtt_ms)
            is_health = pkt.payload == _PING_HEALTH_PAYLOAD
            logger.info(
                "%s PONG %s rtt=%.1fms kind=%s payload_hex=%s",
                LogTag.PROTO_FRAME,
                format_frame_summary(pkt.seq, pkt.opcode, len(pkt.payload)),
                rtt_ms,
                "health" if is_health else "manual",
                format_hex_preview(pkt.payload, max_bytes=16),
            )
            if is_health:
                self.healthResult.emit(rtt_ms, pkt.seq)
            else:
                self.pingResult.emit(rtt_ms, pkt.seq)

        self._telemetry.record_packet_received(pkt, latency_ms=rtt_ms)

    def _on_transfer_progress(
        self, pct: int, speed_kbps: float, eta_sec: float, cur_chunk: int, total_chunks: int
    ) -> None:
        """Forwards transfer progress and registers chunk in telemetry."""
        self._telemetry.record_chunk_transferred()
        self.transferProgress.emit(pct, speed_kbps, eta_sec, cur_chunk, total_chunks)

    def _on_transfer_failed(self, reason: str) -> None:
        """Registers packet loss on transfer failure and emits signal."""
        self._telemetry.record_packet_lost()
        self.transferFailed.emit(reason)

    @property
    def telemetry_service(self) -> TelemetryService:
        """Returns the TelemetryService instance."""
        return self._telemetry

    @property
    def chat_service(self) -> ChatService:
        """Returns the ChatService instance."""
        return self._chat_service

    @property
    def file_service(self) -> FileTransferService:
        """Returns the FileTransferService instance."""
        return self._file_service

    # Slots callable from JavaScript
    @Slot(result=str)
    def scanPorts(self) -> str:
        """Returns JSON list of available COM ports with detailed metadata."""
        ports = self._serial_manager.get_port_info_list()
        return json.dumps([self._serialize_port(port) for port in ports])

    @Slot(result=str)
    def getLaunchConfig(self) -> str:
        """Returns launch/identity config as JSON for UI preselect + branding.

        Returns:
            JSON object with instance id, display name, role, preferred port,
            baud, link settings and data directory.
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
            "baud": cfg.baud if cfg else DEFAULT_BAUD,
            "transport": cfg.transport if cfg else "auto",
            "link_role": cfg.effective_link_role if cfg else "off",
            "link_host": cfg.link_host if cfg else DEFAULT_LINK_HOST,
            "link_base_port": cfg.link_base_port if cfg else DEFAULT_LINK_BASE_PORT,
            "auto_connect": cfg.auto_connect if cfg else False,
        }
        return json.dumps(payload)

    def _default_export_dir(self) -> str:
        """Returns the per-instance logs dir (or the shared one) for exports."""
        if self._instance_context is not None:
            return str(self._instance_context.logs_dir)
        return str(get_logs_dir())

    @Slot(str, int)
    def connectPort(self, port_name: str, baudrate: int) -> None:
        """Connects to specified Serial or Mock COM port.

        Args:
            port_name: Target serial port device name.
            baudrate: Baud rate integer.
        """
        try:
            name = (port_name or "").strip()
            if not name:
                logger.warning("%s Connect rejected: empty port name (baud=%d)", LogTag.HW_SERIAL, baudrate)
                self.errorOccurred.emit("Connect failed: empty port name")
                return
            if baudrate <= 0:
                logger.warning(
                    "%s Connect rejected: invalid baudrate=%d port=%s",
                    LogTag.HW_SERIAL,
                    baudrate,
                    name,
                )
                self.errorOccurred.emit(f"Connect failed: invalid baudrate {baudrate}")
                return
            logger.info(
                "%s UI connect requested: port=%s baud=%d connected=%s",
                LogTag.HW_SERIAL,
                name,
                baudrate,
                self._is_connected,
            )
            self._serial_manager.connect_port(name, baudrate)
        except Exception as exc:
            logger.exception(
                "%s Connect slot FAILED (%s: %s) port=%s baud=%d",
                LogTag.HW_SERIAL,
                type(exc).__name__,
                exc,
                port_name,
                baudrate,
            )
            self.errorOccurred.emit(f"Connect failed: {exc}")

    @Slot()
    def disconnectPort(self) -> None:
        """Disconnects current Serial connection."""
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
        """Transmits manual PING frame over optical link (echo shown as toast)."""
        try:
            self._ping_seq = (self._ping_seq + 1) % 256
            self._last_ping_time = time.time()
            self._serial_manager.send_packet(seq=self._ping_seq, opcode=Opcode.PING, payload=_PING_MANUAL_PAYLOAD)
            self._telemetry.record_packet_sent(self._ping_seq, Opcode.PING, len(_PING_MANUAL_PAYLOAD))
            logger.info(
                "%s UI manual PING %s payload=%s connected=%s",
                LogTag.PROTO_FRAME,
                format_frame_summary(self._ping_seq, Opcode.PING, len(_PING_MANUAL_PAYLOAD)),
                _PING_MANUAL_PAYLOAD.decode("ascii", errors="replace"),
                self._is_connected,
            )
        except Exception as exc:
            logger.exception(
                "%s sendPing FAILED (%s: %s) seq=%d",
                LogTag.PROTO_FRAME,
                type(exc).__name__,
                exc,
                self._ping_seq,
            )
            self.errorOccurred.emit(f"Ping failed: {exc}")

    @Slot(float)
    def setAutoPingInterval(self, seconds: float) -> None:
        """Configures automatic link health-check period.

        Args:
            seconds: Interval between silent health pings; <= 0 disables.
        """
        self._health_interval_sec = seconds
        if seconds > 0:
            self._health_timer.setInterval(int(seconds * 1000))
            if self._is_connected:
                self._health_timer.start()
        else:
            self._health_timer.stop()
        logger.info(
            "%s Health interval set to %.2fs (connected=%s timer_active=%s)",
            LogTag.IPC_BRIDGE,
            seconds,
            self._is_connected,
            self._health_timer.isActive(),
        )

    @Slot(str)
    def sendChat(self, text: str) -> None:
        """Transmits chat message via ChatService.

        Args:
            text: UTF-8 message content string.
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
            self._chat_service.send_message(text)
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
        """Starts optical file transfer.

        Args:
            file_path: Local path to the file.
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
                "%s UI startFileTransfer: path=%s size=%dB connected=%s arq_state=%s",
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
        """Aborts current optical file transfer."""
        try:
            logger.info(
                "%s UI abortFileTransfer: arq_state=%s",
                LogTag.FILE_SVC,
                self._file_service.state.name,
            )
            self._file_service.abort_transfer()
        except Exception as exc:
            logger.exception(
                "%s abortFileTransfer FAILED (%s: %s)",
                LogTag.FILE_SVC,
                type(exc).__name__,
                exc,
            )
            self.errorOccurred.emit(f"Abort failed: {exc}")

    def _dialog_parent(self) -> QWidget | None:
        """Returns a QWidget parent for file dialogs when available."""
        parent = self.parent()
        return parent if isinstance(parent, QWidget) else None

    @Slot(result=str)
    def openFileDialog(self) -> str:
        """Opens native file chooser dialog and returns selected path.

        Returns:
            Selected file path string or empty string.
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
        """Reveals a transferred file's storage folder in the system file manager.

        Args:
            file_path: Local path recorded for the transfer history entry.

        Returns:
            Opened directory path string on success, or empty string when the
            recorded file no longer exists.
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
        """Returns current transfer settings as JSON string.

        Returns:
            JSON object with max_file_size_bytes (0 = unlimited),
            max_file_size_mb, max_file_size_label, transfers_dir and
            default_transfers_dir (used by the "Reset to default" action).
        """
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
        """Validates and persists transfer settings from the Web UI.

        Args:
            settings_json: JSON string with optional max_file_size_bytes
                (0 = unlimited) and transfers_dir fields.

        Returns:
            JSON string with ok flag plus applied config or error message.
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
            if max_bytes is None and (transfers_dir is None or not transfers_dir.strip()):
                return json.dumps({"ok": False, "error": "No settings to update"})
            config = self._file_service.update_config(
                max_file_size_bytes=max_bytes,
                transfers_dir=transfers_dir,
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
        """Opens a native directory chooser for the receive folder.

        Returns:
            Selected directory path string or empty string on cancellation.
        """
        try:
            current = str(self._file_service.transfers_dir_path)
            chosen = QFileDialog.getExistingDirectory(
                self._dialog_parent(),
                "Select Default Transfers Directory",
                current,
            )
            return chosen or ""
        except Exception as exc:
            logger.exception("%s browseTransfersDir failed: %s", LogTag.IPC_BRIDGE, exc)
            self.errorOccurred.emit(f"Browse directory failed: {exc}")
            return ""

    @Slot(result=str)
    def getChatHistory(self) -> str:
        """Retrieves durable chat history from hard drive store as JSON string.

        Returns:
            JSON formatted array of chat history objects.
        """
        history = self._storage.get_chat_history(limit=200)
        return json.dumps(history)

    @Slot(result=str)
    def getFileHistory(self) -> str:
        """Retrieves durable file transfer logs from hard drive store as JSON string.

        Returns:
            JSON formatted array of file transfer objects.
        """
        transfers = self._storage.get_file_transfers(limit=50)
        return json.dumps(transfers)

    @Slot(result=str)
    def getTelemetryStats(self) -> str:
        """Retrieves current telemetry summary as JSON string.

        Returns:
            JSON serialized TelemetrySummary dictionary.
        """
        summary = self._telemetry.get_summary()
        return json.dumps(summary)

    @Slot(result=str)
    def getSnifferRecords(self) -> str:
        """Retrieves recent sniffer records as JSON string.

        Returns:
            JSON serialized list of SnifferRecord items.
        """
        records = self._telemetry.get_sniffer_records(limit=100)
        return json.dumps(records)

    @Slot(result=str)
    def exportTelemetryCsv(self) -> str:
        """Exports telemetry history to CSV file via Save File dialog or default path.

        Returns:
            Destination file path string on success, or empty string on cancellation.
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
        """Exports file transfer history to CSV file via Save File dialog.

        Returns:
            Destination file path string on success, or empty string on cancellation.
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
        """Resets telemetry metrics and sniffer counters."""
        summary = self._telemetry.get_summary()
        self._telemetry.reset_counters()
        logger.info(
            "%s Reset telemetry via UI (was %s)", LogTag.IPC_BRIDGE, summary
        )

    @Slot()
    def closeWindow(self) -> None:
        """Requests application window closure."""
        self.closeWindowRequested.emit()

    @Slot()
    def minimizeWindow(self) -> None:
        """Requests application window minimization."""
        self.minimizeWindowRequested.emit()

    @Slot()
    def toggleMaximizeWindow(self) -> None:
        """Requests toggling application window maximization."""
        self.maximizeWindowRequested.emit()
