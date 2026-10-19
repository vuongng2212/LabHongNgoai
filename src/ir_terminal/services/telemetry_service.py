"""Realtime optical telemetry service calculating throughput, packet loss, RTT, and sniffer data."""

from __future__ import annotations

import csv
import io
import logging
import time
from collections import deque
from pathlib import Path
from typing import ClassVar, TypedDict

from PySide6.QtCore import QObject, QTimer, Signal

from ir_terminal.protocol.constants import Opcode
from ir_terminal.protocol.packet import Packet
from ir_terminal.services.storage_service import StorageService
from ir_terminal.utils.logger import LogTag, format_frame_summary, format_hex_preview, opcode_name
from ir_terminal.utils.path_helpers import get_logs_dir

logger = logging.getLogger(__name__)

# Carrier label shown in sniffer (single source; TFDU4101 peak per PROTOCOL.md).
CARRIER_LABEL = "880nm NIR"

# In-memory sniffer cap (live view only; totals tracked separately).
SNIFFER_MAX_RECORDS = 200

# Minimum interval between JSON telemetry persists (s) to avoid rewrite-per-tick.
TELEMETRY_PERSIST_SEC = 5.0


class SnifferRecord(TypedDict):
    """Sniffer inspection record for raw optical frame trace."""

    time_str: str
    frame_id: str
    carrier: str
    type_str: str
    payload_preview: str
    checksum_status: str
    latency_ms: float
    is_valid: bool


class TelemetrySummary(TypedDict):
    """Snapshot of current channel telemetry state."""

    throughput_kbps: float
    rtt_ms: float
    packet_loss_rate: float
    total_packets_sent: int
    total_packets_received: int
    total_packets_lost: int
    total_chunks_transferred: int
    total_frames_captured: int


class TelemetryService(QObject):
    """Calculates optical link physical parameters and records live sniffer frames.

    Provides throughput calculation over sliding time windows, tracks RTT and packet
    loss percentage, maintains sniffer buffer, and supports CSV export.
    """

    telemetry_updated: ClassVar[Signal] = Signal(
        float, float, float, int, int
    )  # (speed_kbps, loss_pct, rtt_ms, chunks_count, frames_count)

    sniffer_frame_logged: ClassVar[Signal] = Signal(
        str, str, str, str, str, str, float
    )  # (time_str, frame_id, carrier, type_str, payload_preview, checksum_status, latency_ms)

    def __init__(
        self,
        storage_service: StorageService | None = None,
        sample_interval_ms: int = 1000,
        window_sec: float = 3.0,
    ) -> None:
        """Initializes telemetry service.

        Args:
            storage_service: Storage service repository for persistent storage.
            sample_interval_ms: Periodic calculation timer interval in ms.
            window_sec: Sliding window size in seconds for throughput estimation.
        """
        super().__init__()
        self._storage = storage_service or StorageService()
        self._window_sec = window_sec

        # Traffic sliding window: list of (timestamp_epoch, byte_count)
        self._tx_byte_events: deque[tuple[float, int]] = deque()
        self._rx_byte_events: deque[tuple[float, int]] = deque()
        # Running sums mirror the deques (refactor: avoids sum() scan each tick)
        self._tx_window_bytes: int = 0
        self._rx_window_bytes: int = 0

        # Cumulative counters
        self._total_bytes_sent: int = 0
        self._total_bytes_received: int = 0
        self._total_packets_sent: int = 0
        self._total_packets_received: int = 0
        self._total_packets_lost: int = 0
        self._total_chunks_transferred: int = 0

        # Physical link metrics (0.0 = no measurement yet; never seed fake RTT).
        self._current_throughput_kbps: float = 0.0
        self._current_tx_kbps: float = 0.0
        self._current_rx_kbps: float = 0.0
        self._last_rtt_ms: float = 0.0
        self._current_loss_rate: float = 0.0
        self._total_frames_captured: int = 0
        self._framer_crc_errors: int = 0
        self._framer_eof_errors: int = 0
        self._last_persist_time: float = 0.0

        # Sniffer frame trace ring buffer (live view; totals tracked separately)
        self._sniffer_records: deque[SnifferRecord] = deque(maxlen=SNIFFER_MAX_RECORDS)

        # Periodic ticker
        self._timer = QTimer(self)
        self._timer.setInterval(sample_interval_ms)
        self._timer.timeout.connect(self._on_tick)
        self._timer.start()

    @property
    def current_throughput_kbps(self) -> float:
        """Returns instantaneous throughput in KB/s."""
        return self._current_throughput_kbps

    @property
    def last_rtt_ms(self) -> float:
        """Returns most recent RTT measurement in ms."""
        return self._last_rtt_ms

    @property
    def packet_loss_rate(self) -> float:
        """Returns packet loss percentage (0.0 to 100.0)."""
        return self._current_loss_rate

    @property
    def total_packets_sent(self) -> int:
        """Returns total count of packets sent."""
        return self._total_packets_sent

    @property
    def total_packets_received(self) -> int:
        """Returns total count of packets received."""
        return self._total_packets_received

    @property
    def total_packets_lost(self) -> int:
        """Returns total count of packets lost or retried."""
        return self._total_packets_lost

    @property
    def total_chunks_transferred(self) -> int:
        """Returns total file chunks transferred."""
        return self._total_chunks_transferred

    @property
    def total_frames_captured(self) -> int:
        """Returns total frames captured (cumulative, not live-buffer length)."""
        return self._total_frames_captured

    def record_bytes_sent(self, count: int) -> None:
        """Registers transmitted byte count in sliding throughput window.

        Args:
            count: Number of bytes transmitted.
        """
        now = time.time()
        self._total_bytes_sent += count
        self._tx_byte_events.append((now, count))
        self._tx_window_bytes += count

    def record_bytes_received(self, count: int) -> None:
        """Registers received byte count in sliding throughput window.

        Args:
            count: Number of bytes received.
        """
        now = time.time()
        self._total_bytes_received += count
        self._rx_byte_events.append((now, count))
        self._rx_window_bytes += count

    def record_packet_sent(self, seq: int, opcode: int, length: int) -> None:
        """Registers outgoing packet transmission.

        Args:
            seq: Packet sequence number.
            opcode: Protocol operation code.
            length: Payload byte length.
        """
        self._total_packets_sent += 1
        self._total_frames_captured += 1
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(
                "%s TX tracked %s (%s) total_sent=%d frames=%d",
                LogTag.OPTICAL_PHY,
                format_frame_summary(seq, opcode, length),
                opcode_name(opcode),
                self._total_packets_sent,
                self._total_frames_captured,
            )
        time_str = time.strftime("%H:%M:%S")
        type_str = self._opcode_to_type_str(opcode)
        frame_id = f"#0x{seq:04X}"
        payload_preview = f"[0x55_TX len={length}B]"

        rec: SnifferRecord = {
            "time_str": time_str,
            "frame_id": frame_id,
            "carrier": CARRIER_LABEL,
            "type_str": type_str,
            "payload_preview": payload_preview,
            "checksum_status": "CRC32 Valid",
            "latency_ms": self._last_rtt_ms,
            "is_valid": True,
        }
        self._sniffer_records.append(rec)
        self.sniffer_frame_logged.emit(
            time_str,
            frame_id,
            CARRIER_LABEL,
            type_str,
            payload_preview,
            "CRC32 Valid",
            self._last_rtt_ms,
        )

    def record_packet_received(self, pkt: Packet, latency_ms: float = 0.0) -> None:
        """Registers incoming packet and records sniffer entry.

        Args:
            pkt: Verified Packet instance (CRC already checked by framer).
            latency_ms: Measured latency in milliseconds for this packet exchange.
        """
        self._total_packets_received += 1
        self._total_frames_captured += 1
        if latency_ms > 0.0:
            self._last_rtt_ms = latency_ms
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(
                "%s RX tracked %s crc=0x%08X (%s) rtt=%.1fms total_rx=%d hex=%s",
                LogTag.OPTICAL_PHY,
                format_frame_summary(pkt.seq, pkt.opcode, len(pkt.payload)),
                pkt.crc32 & 0xFFFFFFFF,
                opcode_name(pkt.opcode),
                latency_ms,
                self._total_packets_received,
                format_hex_preview(pkt.payload, max_bytes=7),
            )

        time_str = time.strftime("%H:%M:%S")
        type_str = self._opcode_to_type_str(pkt.opcode)
        frame_id = f"#0x{pkt.seq:04X}"
        preview_hex = pkt.payload[:7].hex(" ").upper() if pkt.payload else "EMPTY"
        payload_preview = f"0x55_PRE [{preview_hex}]"

        rec: SnifferRecord = {
            "time_str": time_str,
            "frame_id": frame_id,
            "carrier": CARRIER_LABEL,
            "type_str": type_str,
            "payload_preview": payload_preview,
            "checksum_status": "CRC32 Valid",
            "latency_ms": self._last_rtt_ms,
            "is_valid": True,
        }
        self._sniffer_records.append(rec)
        self.sniffer_frame_logged.emit(
            time_str,
            frame_id,
            CARRIER_LABEL,
            type_str,
            payload_preview,
            "CRC32 Valid",
            self._last_rtt_ms,
        )

    def record_framer_error(self, kind: str, count: int = 1) -> None:
        """Counts a framer-level corruption (CRC/EOF/LEN) as link loss.

        Args:
            kind: Error bucket ('crc_errors', 'eof_errors', 'len_errors').
            count: Delta to add.
        """
        if count <= 0:
            return
        if kind == "crc_errors":
            self._framer_crc_errors += count
        elif kind == "eof_errors":
            self._framer_eof_errors += count
        self._total_packets_lost += count
        logger.warning(
            "%s Framer %s x%d (crc_total=%d eof_total=%d lost_total=%d)",
            LogTag.OPTICAL_PHY,
            kind,
            count,
            self._framer_crc_errors,
            self._framer_eof_errors,
            self._total_packets_lost,
        )

    def record_packet_lost(self) -> None:
        """Registers a lost packet or timeout retry event."""
        self._total_packets_lost += 1
        total_attempts = self._total_packets_sent + self._total_packets_lost
        loss_rate = (self._total_packets_lost / total_attempts * 100.0) if total_attempts else 0.0
        logger.warning(
            "%s Optical packet drop/retry #%d (sent=%d loss=%.2f%%)",
            LogTag.OPTICAL_PHY,
            self._total_packets_lost,
            self._total_packets_sent,
            loss_rate,
        )

    def record_rtt(self, rtt_ms: float) -> None:
        """Updates measured round-trip time.

        Args:
            rtt_ms: Measured RTT in milliseconds.
        """
        old = self._last_rtt_ms
        self._last_rtt_ms = max(rtt_ms, 0.1)
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(
                "%s RTT update %.1fms -> %.1fms (raw=%.2fms)",
                LogTag.OPTICAL_PHY,
                old,
                self._last_rtt_ms,
                rtt_ms,
            )

    def record_chunk_transferred(self) -> None:
        """Registers successful transfer of one file chunk."""
        self._total_chunks_transferred += 1

    def reset_counters(self) -> None:
        """Resets all metrics and counters to initial state."""
        before = {
            "sent": self._total_packets_sent,
            "rx": self._total_packets_received,
            "lost": self._total_packets_lost,
            "chunks": self._total_chunks_transferred,
            "frames": self._total_frames_captured,
            "tx_bytes": self._total_bytes_sent,
            "rx_bytes": self._total_bytes_received,
        }
        self._tx_byte_events.clear()
        self._rx_byte_events.clear()
        self._tx_window_bytes = 0
        self._rx_window_bytes = 0
        self._total_bytes_sent = 0
        self._total_bytes_received = 0
        self._total_packets_sent = 0
        self._total_packets_received = 0
        self._total_packets_lost = 0
        self._total_chunks_transferred = 0
        self._total_frames_captured = 0
        self._framer_crc_errors = 0
        self._framer_eof_errors = 0
        self._current_throughput_kbps = 0.0
        self._current_tx_kbps = 0.0
        self._current_rx_kbps = 0.0
        self._current_loss_rate = 0.0
        self._last_rtt_ms = 0.0
        self._last_persist_time = 0.0
        self._sniffer_records.clear()

        logger.info(
            "%s Reset telemetry counters and sniffer buffer (was %s)",
            LogTag.OPTICAL_PHY,
            before,
        )
        self.telemetry_updated.emit(0.0, 0.0, self._last_rtt_ms, 0, 0)

    def get_summary(self) -> TelemetrySummary:
        """Returns snapshot dictionary of telemetry metrics.

        Returns:
            TelemetrySummary dictionary.
        """
        return TelemetrySummary(
            throughput_kbps=round(self._current_throughput_kbps, 2),
            rtt_ms=round(self._last_rtt_ms, 1),
            packet_loss_rate=round(self._current_loss_rate, 2),
            total_packets_sent=self._total_packets_sent,
            total_packets_received=self._total_packets_received,
            total_packets_lost=self._total_packets_lost,
            total_chunks_transferred=self._total_chunks_transferred,
            total_frames_captured=self._total_frames_captured,
        )

    def get_sniffer_records(self, limit: int = 100) -> list[SnifferRecord]:
        """Returns list of recent sniffer records up to limit.

        Args:
            limit: Maximum count of sniffer records to return.

        Returns:
            List of SnifferRecord items.
        """
        records = list(self._sniffer_records)
        return records[-limit:]

    def export_telemetry_csv(self, output_path: Path | None = None) -> str:
        """Exports channel telemetry data to CSV file and string.

        Args:
            output_path: Optional destination Path. Defaults to
                ``<app root>/data/logs/telemetry_<timestamp>.csv``.

        Returns:
            CSV formatted string.
        """
        if output_path is None:
            output_path = get_logs_dir() / f"telemetry_{int(time.time())}.csv"

        return self._storage.export_telemetry_csv(output_path)

    def export_sniffer_csv(self, output_path: Path | None = None) -> str:
        """Exports sniffer frame captures to CSV file and string.

        Args:
            output_path: Optional destination Path.

        Returns:
            CSV formatted string.
        """
        if output_path is None:
            output_path = get_logs_dir() / f"sniffer_{int(time.time())}.csv"

        records = list(self._sniffer_records)
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(
            [
                "Time_UTC",
                "FrameID",
                "Carrier",
                "Type",
                "PayloadPreview",
                "ChecksumStatus",
                "Latency_ms",
            ]
        )
        for r in records:
            writer.writerow(
                [
                    r["time_str"],
                    r["frame_id"],
                    r["carrier"],
                    r["type_str"],
                    r["payload_preview"],
                    r["checksum_status"],
                    f"{r['latency_ms']:.1f}",
                ]
            )

        csv_text = output.getvalue()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(csv_text, encoding="utf-8")
        logger.info(
            "%s Exported sniffer CSV: path=%s rows=%d bytes=%dB",
            LogTag.STORAGE,
            output_path,
            len(records),
            len(csv_text.encode("utf-8")),
        )
        return csv_text

    def _on_tick(self) -> None:
        """Periodic calculation of throughput and loss rate."""
        now = time.time()
        cutoff = now - self._window_sec

        # Prune old traffic events, keeping running sums in sync (same cutoff rule)
        while self._tx_byte_events and self._tx_byte_events[0][0] < cutoff:
            _, pruned = self._tx_byte_events.popleft()
            self._tx_window_bytes -= pruned
        while self._rx_byte_events and self._rx_byte_events[0][0] < cutoff:
            _, pruned = self._rx_byte_events.popleft()
            self._rx_window_bytes -= pruned

        # Actual span (not fixed window) so idle gaps don't dilute throughput.
        oldest: float | None = None
        if self._tx_byte_events:
            oldest = self._tx_byte_events[0][0]
        if self._rx_byte_events:
            oldest = min(oldest, self._rx_byte_events[0][0]) if oldest else self._rx_byte_events[0][0]
        span = max(now - oldest, 0.1) if oldest is not None else max(self._window_sec, 0.1)
        span = min(span, max(self._window_sec, 0.1))

        self._current_tx_kbps = (self._tx_window_bytes / 1024.0) / span
        self._current_rx_kbps = (self._rx_window_bytes / 1024.0) / span
        total_bytes = self._tx_window_bytes + self._rx_window_bytes

        self._current_throughput_kbps = (total_bytes / 1024.0) / span

        total_attempts = self._total_packets_sent + self._total_packets_lost
        if total_attempts > 0:
            self._current_loss_rate = (self._total_packets_lost / total_attempts) * 100.0
        else:
            self._current_loss_rate = 0.0

        # Batch JSON persist (at most every TELEMETRY_PERSIST_SEC) to avoid
        # rewriting the whole store on every 1s tick.
        if (total_bytes > 0 or self._total_packets_sent > 0) and (
            now - self._last_persist_time >= TELEMETRY_PERSIST_SEC
        ):
            self._last_persist_time = now
            self._storage.save_telemetry_sample(
                timestamp_epoch=now,
                throughput_kbps=self._current_throughput_kbps,
                rtt_ms=self._last_rtt_ms,
                packet_loss_rate=self._current_loss_rate,
                total_packets_sent=self._total_packets_sent,
                total_packets_lost=self._total_packets_lost,
            )

        self.telemetry_updated.emit(
            self._current_throughput_kbps,
            self._current_loss_rate,
            self._last_rtt_ms,
            self._total_chunks_transferred,
            self._total_frames_captured,
        )

    @staticmethod
    def _opcode_to_type_str(opcode: int) -> str:
        """Maps opcode integer to human-readable type string."""
        mapping = {
            Opcode.DATA_CHUNK: "BURST_DATA",
            Opcode.DATA_TEXT: "CHAT_TEXT",
            Opcode.ACK: "ACK_SYNC",
            Opcode.NACK: "NACK_ERR",
            Opcode.PING: "LINK_PING",
            Opcode.PONG: "LINK_PONG",
            Opcode.TRANSFER_START: "XFER_START",
            Opcode.TRANSFER_END: "XFER_END",
            Opcode.TRANSFER_PAUSE: "XFER_PAUSE",
            Opcode.TRANSFER_RESUME: "XFER_RESUME",
        }
        return mapping.get(opcode, f"OP_0x{opcode:02X}")
