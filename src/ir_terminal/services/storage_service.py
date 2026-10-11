"""Durable file-based storage repository for chat history, file logs, and telemetry.

Replaces the former SQLite backend with plain JSON document files under the
application data directory. Client scope does not need a relational database:
each collection is a JSON array file written atomically (temporary file plus
rename) under a threading lock, so background Qt threads can persist safely.
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

# Upper bound for periodic telemetry samples kept on disk.
_MAX_TELEMETRY_SAMPLES = 5000


class ChatMessageRecord(TypedDict):
    """File record representation of a chat message."""

    id: int
    sender: str
    text: str
    timestamp: str
    seq: int
    is_outgoing: bool
    created_at: str


class FileTransferRecord(TypedDict):
    """File record representation of a file transfer event."""

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
    """File record representation of an optical link telemetry sample."""

    id: int
    timestamp_epoch: float
    throughput_kbps: float
    rtt_ms: float
    packet_loss_rate: float
    total_packets_sent: int
    total_packets_lost: int
    created_at: str


def _utc_now_iso() -> str:
    """Returns current UTC time as an ISO-8601 string."""
    return datetime.now(UTC).isoformat(timespec="seconds")


class StorageService:
    """Manages durable JSON file stores on local hard drive with CSV export."""

    def __init__(self, data_dir: Path | None = None) -> None:
        """Initializes storage service with specified or default data directory.

        Args:
            data_dir: Optional explicit directory holding the JSON store files.
        """
        self._data_dir = data_dir if data_dir is not None else get_data_dir()
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        logger.info("%s Initializing file storage at: %s", LogTag.STORAGE, self._data_dir)

    def _store_path(self, filename: str) -> Path:
        """Returns absolute path of a JSON store file."""
        return self._data_dir / filename

    def _load_list(self, filename: str) -> list[dict[str, Any]]:
        """Reads a JSON array store, recovering to empty on missing/corrupt data."""
        path = self._store_path(filename)
        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return []
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("%s Corrupt store file, resetting: %s", LogTag.STORAGE, path)
            return []
        if not isinstance(data, list):
            logger.warning("%s Unexpected store shape, resetting: %s", LogTag.STORAGE, path)
            return []
        return [dict(item) for item in data if isinstance(item, dict)]

    def _save_list(self, filename: str, records: list[dict[str, Any]]) -> None:
        """Atomically rewrites a JSON array store via temporary file plus rename."""
        path = self._store_path(filename)
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        # Compact separators: identical parsed data, ~20-30% fewer bytes written.
        tmp_path.write_text(
            json.dumps(records, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        tmp_path.replace(path)

    def _load_settings(self) -> dict[str, str]:
        """Reads the settings object store, recovering to empty on bad data."""
        path = self._store_path(_SETTINGS_FILE)
        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("%s Corrupt settings file, resetting: %s", LogTag.STORAGE, path)
            return {}
        if not isinstance(data, dict):
            return {}
        return {str(key): str(value) for key, value in data.items()}

    def _append_record(self, filename: str, record: dict[str, Any]) -> int:
        """Appends one record with the next sequential id, returns the id."""
        with self._lock:
            records = self._load_list(filename)
            record_id = len(records) + 1
            record["id"] = record_id
            record["created_at"] = _utc_now_iso()
            records.append(record)
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
        """Persists a chat message to hard drive.

        Args:
            sender: Sender node identifier string.
            text: UTF-8 message content string.
            timestamp: Formatted time string (HH:MM:SS).
            seq: Protocol packet sequence number.
            is_outgoing: True if transmitted locally; False if received from remote.

        Returns:
            Sequential ID of the stored chat message record.
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
        )
        logger.debug(
            "%s Saved chat message #%d (seq=%d, outgoing=%s)",
            LogTag.STORAGE,
            msg_id,
            seq,
            is_outgoing,
        )
        return msg_id

    def get_chat_history(self, limit: int = 100) -> list[ChatMessageRecord]:
        """Retrieves recent chat messages from hard drive.

        Args:
            limit: Maximum number of most recent records to fetch.

        Returns:
            List of ChatMessageRecord items ordered chronologically.
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
        """Persists a file transfer record to hard drive.

        Args:
            filename: Base name of the transferred file.
            file_path: Local filesystem path where file resides.
            direction: Transfer direction ('TX' or 'RX').
            size_bytes: Total file size in bytes.
            crc32_hex: Formatted CRC-32 checksum string (e.g. '0x1A2B3C4D').
            speed_kbps: Average transfer rate in KB/s.
            duration_sec: Total duration in seconds.
            status: Outcome status ('SUCCESS', 'FAILED', 'ABORTED').

        Returns:
            Sequential ID of the stored file transfer record.
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
        )
        logger.info(
            "%s Saved file transfer record #%d: '%s' (%dB, %s, %s)",
            LogTag.STORAGE,
            transfer_id,
            filename,
            size_bytes,
            crc32_hex,
            status,
        )
        return transfer_id

    def get_file_transfers(self, limit: int = 50) -> list[FileTransferRecord]:
        """Retrieves file transfer history from hard drive.

        Args:
            limit: Maximum number of recent transfer records to retrieve.

        Returns:
            List of FileTransferRecord items ordered newest first.
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
        """Persists a realtime optical channel telemetry sample to hard drive.

        Args:
            timestamp_epoch: Unix epoch floating timestamp.
            throughput_kbps: Measured optical link throughput in KB/s.
            rtt_ms: Round-trip flight time in milliseconds.
            packet_loss_rate: Measured packet loss ratio (0.0 to 100.0%).
            total_packets_sent: Cumulative count of packets transmitted.
            total_packets_lost: Cumulative count of lost/retried packets.

        Returns:
            Sequential ID of the stored telemetry sample record.
        """
        with self._lock:
            records = self._load_list(_TELEMETRY_FILE)
            record_id = len(records) + 1
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
        """Retrieves historical telemetry samples ordered by ascending time.

        Args:
            limit: Maximum number of recent samples to retrieve.

        Returns:
            List of TelemetryRecord items.
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
        """Exports all telemetry samples to a CSV formatted string and optional file.

        Args:
            output_path: Optional file path to write CSV content to disk.

        Returns:
            CSV content as formatted string.
        """
        samples = self.get_telemetry_history(limit=_MAX_TELEMETRY_SAMPLES)
        output = io.StringIO()
        writer = csv.writer(output)
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
            output_path.write_text(csv_text, encoding="utf-8")
            logger.info("%s Exported telemetry CSV to: %s", LogTag.STORAGE, output_path)

        return csv_text

    def export_transfers_csv(self, output_path: Path | None = None) -> str:
        """Exports file transfer logs to a CSV formatted string and optional file.

        Args:
            output_path: Optional file path to write CSV content to disk.

        Returns:
            CSV content as formatted string.
        """
        transfers = self.get_file_transfers(limit=5000)
        output = io.StringIO()
        writer = csv.writer(output)
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
            output_path.write_text(csv_text, encoding="utf-8")
            logger.info("%s Exported file transfers CSV to: %s", LogTag.STORAGE, output_path)

        return csv_text

    def save_setting(self, key: str, value: str) -> None:
        """Persists a key-value configuration setting.

        Args:
            key: Setting identifier key.
            value: Setting value string.
        """
        with self._lock:
            settings = self._load_settings()
            settings[key] = value
            path = self._store_path(_SETTINGS_FILE)
            tmp_path = path.with_suffix(path.suffix + ".tmp")
            tmp_path.write_text(json.dumps(settings, ensure_ascii=False, indent=1), encoding="utf-8")
            tmp_path.replace(path)

    def get_setting(self, key: str, default: str = "") -> str:
        """Reads a configuration setting value.

        Args:
            key: Setting identifier key.
            default: Fallback string value if key is not found.

        Returns:
            Persisted string value or default.
        """
        with self._lock:
            settings = self._load_settings()
        return settings.get(key, default)
