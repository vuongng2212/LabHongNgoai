"""Dịch vụ lưu trữ dữ liệu bền vững trên tệp tin JSON và xuất báo cáo CSV (Storage Service).

Lưu trữ các tập dữ liệu người dùng dưới dạng các tệp JSON nguyên tử (atomic write thông qua tệp tạm):
- Lịch sử tin nhắn nhắn tin quang học (chat_history.json).
- Nhật ký các phiên truyền và nhận file (file_transfers.json).
- Mẫu lịch sử viễn trắc đo lường kênh truyền (telemetry_samples.json).
- Cấu hình cài đặt người dùng (settings.json).
Bảo vệ bằng khóa threading.Lock đảm bảo an toàn truy cập đồng thời từ các luồng Qt Worker.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypedDict

from ir_terminal.utils.logger import LogTag
from ir_terminal.utils.path_helpers import get_data_dir

logger = logging.getLogger(__name__)

_CHAT_FILE = "chat_history.json"
_TRANSFERS_FILE = "file_transfers.json"
_TELEMETRY_FILE = "telemetry_samples.json"
_SETTINGS_FILE = "settings.json"

_MAX_TELEMETRY_SAMPLES = 5000

_MAX_CHAT_MESSAGES = 2000
_MAX_TRANSFER_RECORDS = 5000


class ChatMessageRecord(TypedDict):
    """Bản ghi dữ liệu của một tin nhắn văn bản trong lịch sử trò chuyện."""

    id: int
    sender: str
    text: str
    timestamp: str
    seq: int
    is_outgoing: bool
    created_at: str


class FileTransferRecord(TypedDict):
    """Bản ghi dữ liệu của một phiên truyền hoặc nhận file."""

    id: int
    filename: str
    file_path: str
    direction: str
    size_bytes: int
    crc32_hex: str
    speed_kbps: float
    duration_sec: float
    status: str
    created_at: str


class TelemetryRecord(TypedDict):
    """Bản ghi dữ liệu của một mẫu đo lường viễn trắc kênh truyền quang học."""

    id: int
    timestamp_epoch: float
    throughput_kbps: float
    rtt_ms: float
    packet_loss_rate: float
    total_packets_sent: int
    total_packets_lost: int
    created_at: str


def _utc_now_iso() -> str:
    """Trả về thời gian UTC hiện tại theo định dạng chuỗi chuẩn ISO-8601."""
    return datetime.now(UTC).isoformat(timespec="seconds")


class StorageService:
    """Quản lý các kho lưu trữ tệp tin JSON cục bộ và xuất báo cáo dữ liệu định dạng CSV."""

    def __init__(self, data_dir: Path | None = None) -> None:
        """Khởi tạo dịch vụ lưu trữ với thư mục dữ liệu được chỉ định hoặc mặc định.

        Args:
            data_dir: Đường dẫn thư mục chứa các tệp JSON lưu trữ (hoặc None để dùng mặc định).
        """
        self._data_dir = data_dir if data_dir is not None else get_data_dir()
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        logger.info(
            "%s Storage init: data_dir=%s stores=[chat_history.json, file_transfers.json, "
            "telemetry_samples.json, settings.json]",
            LogTag.STORAGE,
            self._data_dir,
        )

    def _store_path(self, filename: str) -> Path:
        """Trả về đường dẫn tuyệt đối của tệp JSON lưu trữ."""
        return self._data_dir / filename

    def _backup_corrupt(self, path: Path) -> None:
        """Đổi tên tệp bị lỗi định dạng sang đuôi .corrupt-TIMESTAMP thay vì xóa đè âm thầm."""
        try:
            size = path.stat().st_size if path.exists() else -1
            backup = path.with_suffix(f"{path.suffix}.corrupt-{int(__import__('time').time())}")
            path.replace(backup)
            logger.warning(
                "%s Backed up corrupt store %s (%dB) -> %s",
                LogTag.STORAGE,
                path,
                size,
                backup,
            )
        except Exception as exc:
            logger.warning(
                "%s Could not back up corrupt store %s (%s: %s)",
                LogTag.STORAGE,
                path,
                type(exc).__name__,
                exc,
            )

    def _load_list(self, filename: str) -> list[dict[str, Any]]:
        """Đọc danh sách bản ghi từ tệp JSON, tự động phục hồi nếu thiếu tệp hoặc lỗi định dạng."""
        path = self._store_path(filename)
        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            logger.debug("%s Store miss (first run): %s", LogTag.STORAGE, path)
            return []
        except Exception as exc:
            logger.warning(
                "%s Store read FAILED %s (%s: %s)",
                LogTag.STORAGE,
                path,
                type(exc).__name__,
                exc,
            )
            return []
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            logger.warning(
                "%s Corrupt store (invalid JSON at line=%d col=%d): %s (%dB) — backing up",
                LogTag.STORAGE,
                exc.lineno,
                exc.colno,
                path,
                len(raw.encode("utf-8")),
            )
            self._backup_corrupt(path)
            return []
        if not isinstance(data, list):
            logger.warning(
                "%s Unexpected store shape (type=%s, expected list): %s — backing up",
                LogTag.STORAGE,
                type(data).__name__,
                path,
            )
            self._backup_corrupt(path)
            return []
        items = [dict(item) for item in data if isinstance(item, dict)]
        logger.debug(
            "%s Store load: %s records=%d skipped=%d bytes=%dB",
            LogTag.STORAGE,
            path,
            len(items),
            len(data) - len(items),
            len(raw.encode("utf-8")),
        )
        return items

    def _save_list(self, filename: str, records: list[dict[str, Any]]) -> None:
        """Ghi đè nguyên tử danh sách bản ghi ra tệp JSON thông qua tệp tạm thời và đổi tên."""
        import os

        path = self._store_path(filename)
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write(json.dumps(records, ensure_ascii=False, separators=(",", ":")))
            f.flush()
            os.fsync(f.fileno())
        tmp_path.replace(path)

    @staticmethod
    def _next_id(records: list[dict[str, Any]]) -> int:
        """Sinh mã định danh ID tăng dần đơn điệu (max + 1), an toàn ngay cả khi bị cắt tỉa dữ liệu cũ."""
        if not records:
            return 1
        try:
            return max(int(r.get("id", 0)) for r in records) + 1
        except (TypeError, ValueError):
            return len(records) + 1

    def _load_settings(self) -> dict[str, str]:
        """Đọc tệp cài đặt JSON, tự động phục hồi về từ điển rỗng nếu dữ liệu bị lỗi."""
        path = self._store_path(_SETTINGS_FILE)
        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("%s Corrupt settings file, backing up: %s", LogTag.STORAGE, path)
            self._backup_corrupt(path)
            return {}
        if not isinstance(data, dict):
            return {}
        return {str(key): str(value) for key, value in data.items()}

    def _append_record(
        self, filename: str, record: dict[str, Any], max_records: int | None = None
    ) -> int:
        """Thêm một bản ghi mới với ID tăng đơn điệu và giới hạn số lượng bản ghi tối đa."""
        with self._lock:
            records = self._load_list(filename)
            record_id = self._next_id(records)
            record["id"] = record_id
            record["created_at"] = _utc_now_iso()
            records.append(record)
            if max_records is not None and len(records) > max_records:
                del records[: len(records) - max_records]
            self._save_list(filename, records)
            return record_id

    def save_chat_message(
        self,
        sender: str,
        text: str,
        timestamp: str,
        seq: int,
        is_outgoing: bool,
    ) -> int:
        """Lưu trữ một tin nhắn trò chuyện xuống tệp JSON chat_history.json.

        Args:
            sender: Tên định danh của trạm gửi tin nhắn.
            text: Nội dung văn bản tin nhắn (mã hóa UTF-8).
            timestamp: Chuỗi thời gian hiển thị (HH:MM:SS).
            seq: Số thứ tự sequence của khung truyền.
            is_outgoing: True nếu tin nhắn do trạm này gửi đi; False nếu nhận từ trạm đối tác.

        Returns:
            Mã định danh ID số nguyên tăng dần của bản ghi tin nhắn.
        """
        msg_id = self._append_record(
            _CHAT_FILE,
            {
                "sender": sender,
                "text": text,
                "timestamp": timestamp,
                "seq": seq,
                "is_outgoing": is_outgoing,
            },
            max_records=_MAX_CHAT_MESSAGES,
        )
        logger.debug(
            "%s Saved chat #%d: sender=%s seq=%d outgoing=%s text_len=%dB preview=%r",
            LogTag.STORAGE,
            msg_id,
            sender,
            seq,
            is_outgoing,
            len(text.encode("utf-8")),
            text[:60],
        )
        return msg_id

    def get_chat_history(self, limit: int = 100) -> list[ChatMessageRecord]:
        """Truy xuất lịch sử tin nhắn trò chuyện gần nhất từ tệp JSON.

        Args:
            limit: Số lượng bản ghi tin nhắn tối đa cần lấy.

        Returns:
            Danh sách các bản ghi ChatMessageRecord sắp xếp theo thứ tự thời gian.
        """
        with self._lock:
            records = self._load_list(_CHAT_FILE)
        recent = records[-limit:] if limit > 0 else []
        return [
            ChatMessageRecord(
                id=int(item.get("id", 0)),
                sender=str(item.get("sender", "")),
                text=str(item.get("text", "")),
                timestamp=str(item.get("timestamp", "")),
                seq=int(item.get("seq", 0)),
                is_outgoing=bool(item.get("is_outgoing", False)),
                created_at=str(item.get("created_at", "")),
            )
            for item in recent
        ]

    def save_file_transfer(
        self,
        filename: str,
        file_path: str,
        direction: str,
        size_bytes: int,
        crc32_hex: str,
        speed_kbps: float,
        duration_sec: float,
        status: str,
    ) -> int:
        """Lưu trữ thông tin phiên truyền nhận file xuống tệp JSON file_transfers.json.

        Args:
            filename: Tên gốc của tệp tin.
            file_path: Đường dẫn tệp tin trên hệ thống tập tin cục bộ.
            direction: Hướng truyền dữ liệu ('TX' hoặc 'RX').
            size_bytes: Dung lượng file tính bằng byte.
            crc32_hex: Chuỗi mã kiểm tra CRC-32 định dạng hexa (ví dụ: '0x1A2B3C4D').
            speed_kbps: Tốc độ truyền trung bình tính bằng KB/s.
            duration_sec: Tổng thời gian thực thi phiên truyền tính bằng giây.
            status: Trạng thái kết quả phiên truyền ('SUCCESS', 'FAILED', 'PARTIAL', 'ABORTED').

        Returns:
            Mã định danh ID số nguyên của bản ghi truyền nhận file.
        """
        transfer_id = self._append_record(
            _TRANSFERS_FILE,
            {
                "filename": filename,
                "file_path": file_path,
                "direction": direction,
                "size_bytes": size_bytes,
                "crc32_hex": crc32_hex,
                "speed_kbps": speed_kbps,
                "duration_sec": duration_sec,
                "status": status,
            },
            max_records=_MAX_TRANSFER_RECORDS,
        )
        logger.info(
            "%s Saved transfer #%d: file='%s' dir=%s size=%dB crc=%s status=%s "
            "speed=%.2fKB/s duration=%.2fs path=%s",
            LogTag.STORAGE,
            transfer_id,
            filename,
            direction,
            size_bytes,
            crc32_hex,
            status,
            speed_kbps,
            duration_sec,
            file_path,
        )
        return transfer_id

    def get_file_transfers(self, limit: int = 50) -> list[FileTransferRecord]:
        """Truy xuất nhật ký lịch sử các phiên truyền file từ tệp JSON.

        Args:
            limit: Số lượng bản ghi truyền file tối đa cần lấy.

        Returns:
            Danh sách các bản ghi FileTransferRecord sắp xếp mới nhất lên đầu.
        """
        with self._lock:
            records = self._load_list(_TRANSFERS_FILE)
        recent = records[-limit:] if limit > 0 else []
        ordered = list(reversed(recent))
        return [
            FileTransferRecord(
                id=int(item.get("id", 0)),
                filename=str(item.get("filename", "")),
                file_path=str(item.get("file_path", "")),
                direction=str(item.get("direction", "")),
                size_bytes=int(item.get("size_bytes", 0)),
                crc32_hex=str(item.get("crc32_hex", "")),
                speed_kbps=float(item.get("speed_kbps", 0.0)),
                duration_sec=float(item.get("duration_sec", 0.0)),
                status=str(item.get("status", "")),
                created_at=str(item.get("created_at", "")),
            )
            for item in ordered
        ]

    def save_telemetry_sample(
        self,
        timestamp_epoch: float,
        throughput_kbps: float,
        rtt_ms: float,
        packet_loss_rate: float,
        total_packets_sent: int,
        total_packets_lost: int,
    ) -> int:
        """Lưu trữ mẫu đo lường viễn trắc kênh quang xuống tệp JSON telemetry_samples.json.

        Args:
            timestamp_epoch: Dấu thời gian Unix epoch tính bằng giây.
            throughput_kbps: Thông lượng đo được tính bằng KB/s.
            rtt_ms: Độ trễ khứ hồi RTT tính bằng mili-giây (ms).
            packet_loss_rate: Tỷ lệ mất gói tính theo phần trăm (0.0 đến 100.0%).
            total_packets_sent: Tổng số gói tin đã phát lũy kế.
            total_packets_lost: Tổng số gói tin bị rớt lũy kế.

        Returns:
            Mã định danh ID số nguyên của mẫu viễn trắc.
        """
        with self._lock:
            records = self._load_list(_TELEMETRY_FILE)
            record_id = self._next_id(records)
            records.append(
                {
                    "id": record_id,
                    "timestamp_epoch": timestamp_epoch,
                    "throughput_kbps": throughput_kbps,
                    "rtt_ms": rtt_ms,
                    "packet_loss_rate": packet_loss_rate,
                    "total_packets_sent": total_packets_sent,
                    "total_packets_lost": total_packets_lost,
                    "created_at": _utc_now_iso(),
                }
            )
            del records[: max(0, len(records) - _MAX_TELEMETRY_SAMPLES)]
            self._save_list(_TELEMETRY_FILE, records)
        logger.debug(
            "%s Saved telemetry sample #%d (Speed=%.2f KB/s, RTT=%.1f ms, Loss=%.1f%%)",
            LogTag.STORAGE,
            record_id,
            throughput_kbps,
            rtt_ms,
            packet_loss_rate,
        )
        return record_id

    def get_telemetry_history(self, limit: int = 100) -> list[TelemetryRecord]:
        """Truy xuất danh sách mẫu viễn trắc lịch sử theo thứ tự thời gian tăng dần.

        Args:
            limit: Số lượng mẫu viễn trắc tối đa cần lấy.

        Returns:
            Danh sách các bản ghi TelemetryRecord.
        """
        with self._lock:
            records = self._load_list(_TELEMETRY_FILE)
        recent = records[-limit:] if limit > 0 else []
        return [
            TelemetryRecord(
                id=int(item.get("id", 0)),
                timestamp_epoch=float(item.get("timestamp_epoch", 0.0)),
                throughput_kbps=float(item.get("throughput_kbps", 0.0)),
                rtt_ms=float(item.get("rtt_ms", 0.0)),
                packet_loss_rate=float(item.get("packet_loss_rate", 0.0)),
                total_packets_sent=int(item.get("total_packets_sent", 0)),
                total_packets_lost=int(item.get("total_packets_lost", 0)),
                created_at=str(item.get("created_at", "")),
            )
            for item in recent
        ]

    def export_telemetry_csv(self, output_path: Path | None = None) -> str:
        """Xuất toàn bộ lịch sử viễn trắc ra chuỗi định dạng CSV và ghi ra tệp nếu chỉ định.

        Args:
            output_path: Đường dẫn tệp tin trên đĩa để lưu nội dung CSV (tùy chọn).

        Returns:
            Chuỗi văn bản nội dung tệp CSV hoàn chỉnh.
        """
        samples = self.get_telemetry_history(limit=_MAX_TELEMETRY_SAMPLES)
        output = io.StringIO()
        writer = csv.writer(output, lineterminator="\n")
        writer.writerow(
            [
                "ID",
                "Timestamp_Epoch",
                "Throughput_KBps",
                "RTT_ms",
                "Packet_Loss_Rate_Pct",
                "Total_Packets_Sent",
                "Total_Packets_Lost",
                "Created_At",
            ]
        )
        for s in samples:
            writer.writerow(
                [
                    s["id"],
                    f"{s['timestamp_epoch']:.3f}",
                    f"{s['throughput_kbps']:.2f}",
                    f"{s['rtt_ms']:.1f}",
                    f"{s['packet_loss_rate']:.2f}",
                    s["total_packets_sent"],
                    s["total_packets_lost"],
                    s["created_at"],
                ]
            )

        csv_text = output.getvalue()
        if output_path is not None:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(csv_text, encoding="utf-8", newline="")
            logger.info(
                "%s Exported telemetry CSV: path=%s rows=%d bytes=%dB",
                LogTag.STORAGE,
                output_path,
                len(samples),
                len(csv_text.encode("utf-8")),
            )

        return csv_text

    def export_transfers_csv(self, output_path: Path | None = None) -> str:
        """Xuất nhật ký lịch sử truyền file ra chuỗi định dạng CSV và ghi ra tệp nếu chỉ định.

        Args:
            output_path: Đường dẫn tệp tin trên đĩa để lưu nội dung CSV (tùy chọn).

        Returns:
            Chuỗi văn bản nội dung tệp CSV hoàn chỉnh.
        """
        transfers = self.get_file_transfers(limit=5000)
        output = io.StringIO()
        writer = csv.writer(output, lineterminator="\n")
        writer.writerow(
            [
                "ID",
                "Filename",
                "FilePath",
                "Direction",
                "Size_Bytes",
                "CRC32_Hex",
                "Speed_KBps",
                "Duration_Sec",
                "Status",
                "Created_At",
            ]
        )
        for t in transfers:
            writer.writerow(
                [
                    t["id"],
                    t["filename"],
                    t["file_path"],
                    t["direction"],
                    t["size_bytes"],
                    t["crc32_hex"],
                    f"{t['speed_kbps']:.2f}",
                    f"{t['duration_sec']:.2f}",
                    t["status"],
                    t["created_at"],
                ]
            )

        csv_text = output.getvalue()
        if output_path is not None:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(csv_text, encoding="utf-8", newline="")
            logger.info(
                "%s Exported transfers CSV: path=%s rows=%d bytes=%dB",
                LogTag.STORAGE,
                output_path,
                len(transfers),
                len(csv_text.encode("utf-8")),
            )

        return csv_text

    def save_setting(self, key: str, value: str) -> None:
        """Lưu một cặp khóa-giá trị cấu hình cài đặt xuống tệp settings.json.

        Args:
            key: Tên khóa cài đặt.
            value: Giá trị chuỗi của cài đặt.
        """
        with self._lock:
            settings = self._load_settings()
            settings[key] = value
            path = self._store_path(_SETTINGS_FILE)
            tmp_path = path.with_suffix(path.suffix + ".tmp")
            tmp_path.write_text(
                json.dumps(settings, ensure_ascii=False, indent=1), encoding="utf-8"
            )
            tmp_path.replace(path)

    def get_setting(self, key: str, default: str = "") -> str:
        """Đọc giá trị cài đặt từ tệp settings.json.

        Args:
            key: Tên khóa cài đặt cần tra cứu.
            default: Giá trị chuỗi mặc định nếu không tìm thấy khóa.

        Returns:
            Giá trị cài đặt đã lưu hoặc giá trị mặc định.
        """
        with self._lock:
            settings = self._load_settings()
        return settings.get(key, default)
