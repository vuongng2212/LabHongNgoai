"""Dịch vụ giám sát viễn trắc quang học và phân tích gói tin thời gian thực (Telemetry & Packet Sniffer).

Thu thập và tính toán các chỉ số chất lượng kênh truyền Open-Loop:
- Thông lượng tức thời (Throughput KB/s) theo cửa sổ trượt thời gian 3.0s.
- Tỷ lệ rớt gói (Packet Loss Rate %) và đếm lỗi khung truyền UART.
- Độ trễ khứ hồi ước lượng (RTT ms) và bộ đệm bắt gói tin (Live Packet Sniffer 200 bản ghi).
- Định kỳ lưu trữ mẫu đo lường viễn trắc xuống cơ sở dữ liệu và xuất báo cáo CSV.
"""

from __future__ import annotations

import csv
import io
import logging
import time
from collections import deque
from pathlib import Path
from typing import ClassVar, TypedDict

from PySide6.QtCore import QObject, QTimer, Signal

from ir_terminal.protocol.constants import SEQ_END, SEQ_HEADER
from ir_terminal.protocol.packet import WinFormPacket
from ir_terminal.services.storage_service import StorageService
from ir_terminal.utils.logger import LogTag, format_hex_preview
from ir_terminal.utils.path_helpers import get_logs_dir

logger = logging.getLogger(__name__)

CARRIER_LABEL = "880nm NIR"

SNIFFER_MAX_RECORDS = 200

TELEMETRY_PERSIST_SEC = 5.0

OPEN_LOOP_STATUS = "OPEN-LOOP"


class SnifferRecord(TypedDict):
    """Bản ghi siêu dữ liệu bắt gói tin (sniffer record) cho một khung truyền quang thô."""

    time_str: str
    frame_id: str
    carrier: str
    type_str: str
    payload_preview: str
    checksum_status: str
    latency_ms: float
    is_valid: bool


class TelemetrySummary(TypedDict):
    """Bản sao trạng thái tổng hợp các chỉ số viễn trắc kênh truyền hiện tại."""

    throughput_kbps: float
    rtt_ms: float
    packet_loss_rate: float
    total_packets_sent: int
    total_packets_received: int
    total_packets_lost: int
    total_chunks_transferred: int
    total_frames_captured: int


def frame_type_name(sequence: int) -> str:
    """Ánh xạ mã định danh sequence của khung truyền thành nhãn chuỗi trực quan.

    Args:
        sequence: Mã sequence nhị phân từ tiêu đề khung truyền.

    Returns:
        Chuỗi ký tự định danh: "FILE_HEADER", "FILE_END" hoặc "DATA_CHUNK".
    """
    if sequence == SEQ_HEADER:
        return "FILE_HEADER"
    if sequence == SEQ_END:
        return "FILE_END"
    return "DATA_CHUNK"


class TelemetryService(QObject):
    """Tính toán đo lường viễn trắc kênh truyền Open-Loop và quản lý bộ bắt gói tin Sniffer."""

    telemetry_updated: ClassVar[Signal] = Signal(float, float, float, int, int)

    sniffer_frame_logged: ClassVar[Signal] = Signal(
        str, str, str, str, str, str, float
    )

    def __init__(
        self,
        storage_service: StorageService | None = None,
        sample_interval_ms: int = 1000,
        window_sec: float = 3.0,
    ) -> None:
        """Khởi tạo dịch vụ viễn trắc và định thời tính toán chu kỳ.

        Args:
            storage_service: Dịch vụ lưu trữ bền vững để ghi nhận mẫu viễn trắc.
            sample_interval_ms: Chu kỳ định thời tính toán số liệu tính bằng mili-giây (mặc định 1000 ms).
            window_sec: Kích thước cửa sổ trượt tính thông lượng tính bằng giây (mặc định 3.0s).
        """
        super().__init__()
        self._storage = storage_service or StorageService()
        self._window_sec = window_sec

        self._tx_byte_events: deque[tuple[float, int]] = deque()
        self._rx_byte_events: deque[tuple[float, int]] = deque()
        self._tx_window_bytes: int = 0
        self._rx_window_bytes: int = 0

        self._total_bytes_sent: int = 0
        self._total_bytes_received: int = 0
        self._total_packets_sent: int = 0
        self._total_packets_received: int = 0
        self._total_packets_lost: int = 0
        self._total_chunks_transferred: int = 0

        self._current_throughput_kbps: float = 0.0
        self._current_tx_kbps: float = 0.0
        self._current_rx_kbps: float = 0.0
        self._last_rtt_ms: float = 0.0
        self._current_loss_rate: float = 0.0
        self._total_frames_captured: int = 0
        self._last_persist_time: float = 0.0

        self._sniffer_records: deque[SnifferRecord] = deque(maxlen=SNIFFER_MAX_RECORDS)

        self._timer = QTimer(self)
        self._timer.setInterval(sample_interval_ms)
        self._timer.timeout.connect(self._on_tick)
        self._timer.start()

    @property
    def current_throughput_kbps(self) -> float:
        """Thông lượng truyền nhận dữ liệu tức thời tính bằng KB/s."""
        return self._current_throughput_kbps

    @property
    def last_rtt_ms(self) -> float:
        """Độ trễ khứ hồi (RTT) đo lường gần nhất tính bằng mili-giây (ms)."""
        return self._last_rtt_ms

    @property
    def packet_loss_rate(self) -> float:
        """Tỷ lệ phần trăm gói tin bị mất (trong khoảng từ 0.0 đến 100.0%)."""
        return self._current_loss_rate

    @property
    def total_packets_sent(self) -> int:
        """Tổng số lượng gói tin đã phát qua đường truyền quang."""
        return self._total_packets_sent

    @property
    def total_packets_received(self) -> int:
        """Tổng số lượng gói tin hợp lệ đã thu nhận thành công."""
        return self._total_packets_received

    @property
    def total_packets_lost(self) -> int:
        """Tổng số lượng gói tin bị ghi nhận rớt trên đường truyền."""
        return self._total_packets_lost

    @property
    def total_chunks_transferred(self) -> int:
        """Tổng số lượng chunk dữ liệu file đã chuyển giao thành công."""
        return self._total_chunks_transferred

    @property
    def total_frames_captured(self) -> int:
        """Tổng số lượng khung truyền quang đã bắt được bởi bộ Sniffer."""
        return self._total_frames_captured

    def record_bytes_sent(self, count: int) -> None:
        """Ghi nhận số byte vừa phát vào cửa sổ trượt tính thông lượng.

        Args:
            count: Số lượng byte vừa truyền đi.
        """
        now = time.time()
        self._total_bytes_sent += count
        self._tx_byte_events.append((now, count))
        self._tx_window_bytes += count

    def record_bytes_received(self, count: int) -> None:
        """Ghi nhận số byte vừa thu nhận vào cửa sổ trượt tính thông lượng.

        Args:
            count: Số lượng byte vừa nhận được.
        """
        now = time.time()
        self._total_bytes_received += count
        self._rx_byte_events.append((now, count))
        self._rx_window_bytes += count

    def record_packet_sent(self, sequence: int, length: int) -> None:
        """Ghi nhận một khung truyền vừa phát đi và thêm vào hàng đợi Sniffer.

        Args:
            sequence: Mã định danh sequence của khung truyền.
            length: Độ dài byte phần payload.
        """
        self._total_packets_sent += 1
        self._total_frames_captured += 1
        type_str = frame_type_name(sequence)
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(
                "%s TX tracked seq=0x%08X type=%s payload=%dB total_sent=%d",
                LogTag.OPTICAL_PHY,
                sequence,
                type_str,
                length,
                self._total_packets_sent,
            )
        self._append_sniffer(sequence, type_str, f"[TX len={length}B]", self._last_rtt_ms)

    def record_packet_received(self, pkt: WinFormPacket, latency_ms: float = 0.0) -> None:
        """Ghi nhận một khung truyền vừa thu nhận thành công và cập nhật bộ Sniffer.

        Args:
            pkt: Đối tượng WinFormPacket đã giải mã.
            latency_ms: Độ trễ ước lượng đo được tính bằng mili-giây (ms).
        """
        self._total_packets_received += 1
        self._total_frames_captured += 1
        if latency_ms > 0.0:
            self._last_rtt_ms = latency_ms
        type_str = frame_type_name(pkt.sequence)
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(
                "%s RX tracked seq=0x%08X type=%s payload=%dB total_rx=%d hex=%s",
                LogTag.OPTICAL_PHY,
                pkt.sequence,
                type_str,
                len(pkt.data),
                self._total_packets_received,
                format_hex_preview(pkt.data, max_bytes=7),
            )
        preview = pkt.data[:7].hex(" ").upper() if pkt.data else "EMPTY"
        self._append_sniffer(
            pkt.sequence, type_str, f"PRE [{preview}]", self._last_rtt_ms
        )

    def _append_sniffer(
        self, sequence: int, type_str: str, payload_preview: str, latency_ms: float
    ) -> None:
        """Lưu trữ và phát tín hiệu cho một bản ghi bắt gói tin Sniffer."""
        time_str = time.strftime("%H:%M:%S")
        frame_id = f"#0x{sequence:08X}"
        rec: SnifferRecord = {
            "time_str": time_str,
            "frame_id": frame_id,
            "carrier": CARRIER_LABEL,
            "type_str": type_str,
            "payload_preview": payload_preview,
            "checksum_status": OPEN_LOOP_STATUS,
            "latency_ms": latency_ms,
            "is_valid": True,
        }
        self._sniffer_records.append(rec)
        self.sniffer_frame_logged.emit(
            time_str,
            frame_id,
            CARRIER_LABEL,
            type_str,
            payload_preview,
            OPEN_LOOP_STATUS,
            latency_ms,
        )

    def record_framer_error(self, kind: str, count: int = 1) -> None:
        """Tính toán các byte nhiễu kênh quang bị loại bỏ như hiện tượng rớt gói tiềm ẩn.

        Args:
            kind: Nhãn phân loại loại nhiễu phục vụ ghi log.
            count: Số lượng mẩu byte nhiễu bị hủy.
        """
        if count <= 0:
            return
        self._total_packets_lost += count
        logger.warning(
            "%s Stream noise %s x%d (lost_total=%d)",
            LogTag.OPTICAL_PHY,
            kind,
            count,
            self._total_packets_lost,
        )

    def record_packet_lost(self) -> None:
        """Ghi nhận sự kiện một gói tin bị mất hoặc không hoàn tất trên kênh truyền."""
        self._total_packets_lost += 1
        total_attempts = self._total_packets_sent + self._total_packets_lost
        loss_rate = (self._total_packets_lost / total_attempts * 100.0) if total_attempts else 0.0
        logger.warning(
            "%s Optical packet drop #%d (sent=%d loss=%.2f%%)",
            LogTag.OPTICAL_PHY,
            self._total_packets_lost,
            self._total_packets_sent,
            loss_rate,
        )

    def record_rtt(self, rtt_ms: float) -> None:
        """Cập nhật giá trị độ trễ khứ hồi đo lường được.

        Args:
            rtt_ms: Độ trễ RTT tính bằng mili-giây (ms).
        """
        self._last_rtt_ms = max(rtt_ms, 0.1)

    def record_chunk_transferred(self) -> None:
        """Ghi nhận việc hoàn tất chuyển giao thành công một chunk dữ liệu file."""
        self._total_chunks_transferred += 1

    def reset_counters(self) -> None:
        """Đặt lại toàn bộ các bộ đếm đo lường và xóa sạch bộ đệm bắt gói tin Sniffer."""
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
        self._current_throughput_kbps = 0.0
        self._current_tx_kbps = 0.0
        self._current_rx_kbps = 0.0
        self._current_loss_rate = 0.0
        self._last_rtt_ms = 0.0
        self._last_persist_time = 0.0
        self._sniffer_records.clear()
        logger.info("%s Reset telemetry counters and sniffer buffer", LogTag.OPTICAL_PHY)
        self.telemetry_updated.emit(0.0, 0.0, self._last_rtt_ms, 0, 0)

    def get_summary(self) -> TelemetrySummary:
        """Tổng hợp và trả về bản sao các chỉ số viễn trắc đo lường tức thời.

        Returns:
            Từ điển TelemetrySummary chứa các trường số liệu chi tiết.
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
        """Lấy danh sách các bản ghi Sniffer gần nhất lên tới số lượng giới hạn.

        Args:
            limit: Số lượng bản ghi tối đa cần lấy.

        Returns:
            Danh sách các đối tượng SnifferRecord.
        """
        records = list(self._sniffer_records)
        return records[-limit:]

    def export_telemetry_csv(self, output_path: Path | None = None) -> str:
        """Xuất lịch sử đo lường viễn trắc kênh truyền ra tệp định dạng CSV.

        Args:
            output_path: Đường dẫn tệp đích tùy chọn.

        Returns:
            Chuỗi văn bản chứa nội dung CSV hoàn chỉnh.
        """
        if output_path is None:
            output_path = get_logs_dir() / f"telemetry_{int(time.time())}.csv"
        return self._storage.export_telemetry_csv(output_path)

    def export_sniffer_csv(self, output_path: Path | None = None) -> str:
        """Xuất danh sách các khung truyền bắt được bởi Sniffer ra tệp CSV.

        Args:
            output_path: Đường dẫn tệp đích tùy chọn.

        Returns:
            Chuỗi văn bản chứa nội dung CSV hoàn chỉnh.
        """
        if output_path is None:
            output_path = get_logs_dir() / f"sniffer_{int(time.time())}.csv"
        records = list(self._sniffer_records)
        output = io.StringIO()
        writer = csv.writer(output, lineterminator="\n")
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
        output_path.write_text(csv_text, encoding="utf-8", newline="")
        logger.info(
            "%s Exported sniffer CSV: path=%s rows=%d bytes=%dB",
            LogTag.STORAGE,
            output_path,
            len(records),
            len(csv_text.encode("utf-8")),
        )
        return csv_text

    def _on_tick(self) -> None:
        """Định kỳ tính toán lại thông lượng, tỷ lệ mất gói và lưu mẫu viễn trắc."""
        now = time.time()
        cutoff = now - self._window_sec

        while self._tx_byte_events and self._tx_byte_events[0][0] < cutoff:
            _, pruned = self._tx_byte_events.popleft()
            self._tx_window_bytes -= pruned
        while self._rx_byte_events and self._rx_byte_events[0][0] < cutoff:
            _, pruned = self._rx_byte_events.popleft()
            self._rx_window_bytes -= pruned

        oldest: float | None = None
        if self._tx_byte_events:
            oldest = self._tx_byte_events[0][0]
        if self._rx_byte_events:
            oldest = (
                min(oldest, self._rx_byte_events[0][0]) if oldest else self._rx_byte_events[0][0]
            )
        span = max(now - oldest, 0.1) if oldest is not None else max(self._window_sec, 0.1)
        span = min(span, max(self._window_sec, 0.1))

        self._current_tx_kbps = (self._tx_window_bytes / 1024.0) / span
        self._current_rx_kbps = (self._rx_window_bytes / 1024.0) / span
        total_bytes = self._tx_window_bytes + self._rx_window_bytes
        self._current_throughput_kbps = (total_bytes / 1024.0) / span

        total_attempts = self._total_packets_sent + self._total_packets_lost
        self._current_loss_rate = (
            (self._total_packets_lost / total_attempts) * 100.0 if total_attempts > 0 else 0.0
        )

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
