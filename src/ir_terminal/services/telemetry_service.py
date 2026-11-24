"""Bộ đo thông số đường truyền: tính tốc độ thực tế (KB/giây), tỷ lệ rớt gói (packet loss %) và bảng bắt gói tin quang học (packet sniffer).

Chuyên theo dõi chất lượng đường truyền quang giữa hai máy tính qua tia hồng ngoại:
- Đo tốc độ truyền thực tế (KB/s): đếm lượng byte bay qua không khí trong 3 giây gần nhất để hiển thị biểu đồ thời gian thực.
- Theo dõi tỷ lệ rớt gói (packet loss %): đếm số gói bị mất tín hiệu hoặc lỗi checksum CRC để kỹ sư biết tia quang có bị lệch hướng hay vật cản không.
- Bảng bắt gói tin (Packet Sniffer): hiển thị trực tiếp 200 khung tin quang gần nhất (ID gói, loại gói HEADER/DATA/END, độ trễ ms) giống như một máy phân tích logic thu nhỏ.
- Định kỳ tự động lưu số liệu đo đạc vào ổ cứng và cho phép bấm nút xuất báo cáo ra file Excel (.csv).
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

from ir_terminal.protocol.constants import (
    SEQ_CHECKPOINT_ACK,
    SEQ_CHECKPOINT_PROBE,
    SEQ_END,
    SEQ_HEADER,
    SEQ_RESUME_REQUEST,
)
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
    """Bản ghi tóm tắt của một khung tin quang học bắt được trên đường truyền (thời gian, mã khung, loại gói tin, xem trước dữ liệu, độ trễ)."""

    time_str: str
    frame_id: str
    carrier: str
    type_str: str
    payload_preview: str
    checksum_status: str
    latency_ms: float
    is_valid: bool


class TelemetrySummary(TypedDict):
    """Bảng số liệu tổng hợp về chất lượng kênh quang (tốc độ KB/s, tỷ lệ rớt gói %, tổng số gói đã gửi/nhận/rớt)."""

    throughput_kbps: float
    rtt_ms: float
    packet_loss_rate: float
    total_packets_sent: int
    total_packets_received: int
    total_packets_lost: int
    total_chunks_transferred: int
    total_frames_captured: int


def frame_type_name(sequence: int) -> str:
    """Đổi mã số sequence thành tên loại gói tin dễ đọc: FILE_HEADER (gói mở đầu), FILE_END (gói kết thúc), DATA_CHUNK (gói dữ liệu 256 byte).

    Args:
        sequence: Mã sequence nhị phân từ tiêu đề khung truyền.

    Returns:
        Chuỗi ký tự định danh: "FILE_HEADER", "FILE_END" hoặc "DATA_CHUNK".
    """
    if sequence == SEQ_HEADER:
        return "FILE_HEADER"
    if sequence == SEQ_END:
        return "FILE_END"
    if sequence == SEQ_CHECKPOINT_ACK:
        return "CHECKPOINT_ACK"
    if sequence == SEQ_CHECKPOINT_PROBE:
        return "CHECKPOINT_PROBE"
    if sequence == SEQ_RESUME_REQUEST:
        return "RESUME_REQUEST"
    return "DATA_CHUNK"


class TelemetryService(QObject):
    """Bộ đo thông số đường truyền: tính tốc độ thực tế (KB/giây), tỷ lệ rớt gói (packet loss %) và bảng bắt gói tin quang học (packet sniffer)."""

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
        """Khởi tạo bộ đo thông số đường truyền quang và bắt đầu chu kỳ tính toán 1 giây/lần.

        Args:
            storage_service: Dịch vụ lưu trữ bền vững để ghi nhận mẫu viễn trắc.
            sample_interval_ms: Chu kỳ định thời tính toán số liệu tính bằng mili-giây (mặc định 1000 ms).
            window_sec: Khoảng thời gian cửa sổ trượt tính tốc độ tức thời (mặc định 3 giây gần nhất).
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
        """Tốc độ truyền nhận dữ liệu thực tế đo được qua tia hồng ngoại tính bằng KB/giây."""
        return self._current_throughput_kbps

    @property
    def last_rtt_ms(self) -> float:
        """Độ trễ truyền nhận ước lượng tính bằng mili-giây (ms)."""
        return self._last_rtt_ms

    @property
    def packet_loss_rate(self) -> float:
        """Tỷ lệ phần trăm gói tin bị mất trên tia quang (trong khoảng từ 0.0% đến 100.0%)."""
        return self._current_loss_rate

    @property
    def total_packets_sent(self) -> int:
        """Tổng số gói tin đã phát ra mắt phát quang."""
        return self._total_packets_sent

    @property
    def total_packets_received(self) -> int:
        """Tổng số gói tin hợp lệ mắt thu quang đã nhặt được thành công."""
        return self._total_packets_received

    @property
    def total_packets_lost(self) -> int:
        """Tổng số gói tin bị rớt do nhiễu môi trường, lệch góc tia sáng hoặc lỗi khung truyền."""
        return self._total_packets_lost

    @property
    def total_chunks_transferred(self) -> int:
        """Tổng số lượng chunk dữ liệu 256 byte đã chuyển giao thành công."""
        return self._total_chunks_transferred

    @property
    def total_frames_captured(self) -> int:
        """Tổng số khung tin quang học đã bắt được và hiển thị trên bảng Sniffer."""
        return self._total_frames_captured

    def record_bytes_sent(self, count: int) -> None:
        """Ghi nhận số byte vừa đẩy ra mắt phát quang vào bộ tính tốc độ tức thời.

        Args:
            count: Số lượng byte vừa truyền đi.
        """
        now = time.time()
        self._total_bytes_sent += count
        self._tx_byte_events.append((now, count))
        self._tx_window_bytes += count

    def record_bytes_received(self, count: int) -> None:
        """Ghi nhận số byte vừa nhận từ mắt thu quang vào bộ tính tốc độ tức thời.

        Args:
            count: Số lượng byte vừa nhận được.
        """
        now = time.time()
        self._total_bytes_received += count
        self._rx_byte_events.append((now, count))
        self._rx_window_bytes += count

    def record_packet_sent(self, sequence: int, length: int) -> None:
        """Ghi nhận một khung tin vừa phát ra đường quang và đưa vào bảng bắt gói tin Sniffer.

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
        """Ghi nhận một khung tin quang thu được và đưa vào bảng bắt gói tin Sniffer để kỹ sư xem trước vài byte đầu.

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
        """Thêm một dòng nhật ký vào bảng bắt gói tin quang (sniffer) và phát tín hiệu cho giao diện cập nhật bảng."""
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
        """Ghi nhận các byte nhiễu quang học hoặc byte rác không đúng định dạng bị loại bỏ khỏi luồng.

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
        """Ghi nhận sự kiện một gói tin bị rớt trên đường truyền quang và tính lại tỷ lệ rớt gói (loss %)."""
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
        """Cập nhật giá trị độ trễ ước lượng (tính bằng ms).

        Args:
            rtt_ms: Độ trễ RTT tính bằng mili-giây (ms).
        """
        self._last_rtt_ms = max(rtt_ms, 0.1)

    def record_chunk_transferred(self) -> None:
        """Tăng số lượng chunk dữ liệu file 256 byte đã chuyển giao thành công thêm 1."""
        self._total_chunks_transferred += 1

    def reset_counters(self) -> None:
        """Đặt lại toàn bộ các bộ đếm đo lường tốc độ, xóa sạch bảng bắt gói tin quang (sniffer) về số 0 khi người dùng bấm nút Reset."""
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
        """Tổng hợp nhanh toàn bộ số liệu đo đạc (tốc độ, tỷ lệ rớt gói, tổng gói gửi/nhận) thành một từ điển.

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
        """Lấy danh sách các khung tin quang học bắt được gần nhất (tối đa theo số lượng limit) để đưa lên giao diện.

        Args:
            limit: Số lượng bản ghi tối đa cần lấy.

        Returns:
            Danh sách các đối tượng SnifferRecord.
        """
        records = list(self._sniffer_records)
        return records[-limit:]

    def export_telemetry_csv(self, output_path: Path | None = None) -> str:
        """Xuất lịch sử đo lường tốc độ truyền và rớt gói ra file bảng tính CSV.

        Args:
            output_path: Đường dẫn tệp đích tùy chọn.

        Returns:
            Chuỗi văn bản chứa nội dung CSV hoàn chỉnh.
        """
        if output_path is None:
            output_path = get_logs_dir() / f"telemetry_{int(time.time())}.csv"
        return self._storage.export_telemetry_csv(output_path)

    def export_sniffer_csv(self, output_path: Path | None = None) -> str:
        """Xuất danh sách các khung tin quang bắt được từ bộ sniffer ra tệp CSV để kỹ sư phân tích tín hiệu trên Excel.

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
        """Định kỳ 1 giây/lần tự động tính lại tốc độ truyền thực tế (KB/s), tỷ lệ rớt gói (%) trong 3 giây qua và lưu vào ổ cứng."""
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
