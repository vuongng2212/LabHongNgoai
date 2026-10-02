"""Persistent SQLite storage repository for durable chat history, file logs, and telemetry."""

from __future__ import annotations

import csv
import io
import logging
import sqlite3
from pathlib import Path
from typing import TypedDict

from ir_terminal.utils.logger import LogTag
from ir_terminal.utils.path_helpers import get_db_path

logger = logging.getLogger(__name__)


class ChatMessageRecord(TypedDict):
    """Database record representation of a chat message."""

    id: int
    sender: str
    text: str
    timestamp: str
    seq: int
    is_outgoing: bool
    created_at: str


class FileTransferRecord(TypedDict):
    """Database record representation of a file transfer event."""

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
    """Database record representation of an optical link telemetry sample."""

    id: int
    timestamp_epoch: float
    throughput_kbps: float
    rtt_ms: float
    packet_loss_rate: float
    total_packets_sent: int
    total_packets_lost: int
    created_at: str


class StorageService:
    """Manages persistent SQLite database on local storage with CSV export capabilities."""

    def __init__(self, db_path: Path | None = None) -> None:
        """Initializes storage service with specified or default database file path.

        Args:
            db_path: Optional explicit Path to the SQLite database file.
        """
        self._db_path = db_path if db_path is not None else get_db_path()
        self._init_database()

    def _get_connection(self) -> sqlite3.Connection:
        """Creates and returns a connection to the SQLite database.

        Returns:
            Configured sqlite3.Connection instance with Row factory.
        """
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init_database(self) -> None:
        """Initializes database schema if not already present."""
        logger.info(
            "%s Initializing SQLite persistent database at: %s", LogTag.STORAGE, self._db_path
        )
        with self._get_connection() as conn:
            cursor = conn.cursor()

            # 1. Chat Messages Table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS chat_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    sender TEXT NOT NULL,
                    text TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    seq INTEGER NOT NULL,
                    is_outgoing INTEGER NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)

            # 2. File Transfers Table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS file_transfers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    filename TEXT NOT NULL,
                    file_path TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    crc32_hex TEXT NOT NULL,
                    speed_kbps REAL NOT NULL,
                    duration_sec REAL NOT NULL,
                    status TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)

            # 3. Telemetry Samples Table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS telemetry_samples (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp_epoch REAL NOT NULL,
                    throughput_kbps REAL NOT NULL,
                    rtt_ms REAL NOT NULL,
                    packet_loss_rate REAL NOT NULL,
                    total_packets_sent INTEGER NOT NULL,
                    total_packets_lost INTEGER NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)

            # 4. Application Settings Table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS app_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)
            conn.commit()

    def save_chat_message(
        self,
        sender: str,
        text: str,
        timestamp: str,
        seq: int,
        is_outgoing: bool,
    ) -> int:
        """Persists a chat message to disk.

        Args:
            sender: Sender node identifier string.
            text: UTF-8 message content string.
            timestamp: Formatted time string (HH:MM:SS).
            seq: Protocol packet sequence number.
            is_outgoing: True if transmitted locally; False if received from remote.

        Returns:
            Row ID of the inserted chat message record.
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO chat_messages (sender, text, timestamp, seq, is_outgoing)
                VALUES (?, ?, ?, ?, ?);
                """,
                (sender, text, timestamp, seq, 1 if is_outgoing else 0),
            )
            conn.commit()
            msg_id = int(cursor.lastrowid or 0)
            logger.debug(
                "%s Saved chat message #%d (seq=%d, outgoing=%s)",
                LogTag.STORAGE,
                msg_id,
                seq,
                is_outgoing,
            )
            return msg_id

    def get_chat_history(self, limit: int = 100) -> list[ChatMessageRecord]:
        """Retrieves recent chat messages from database.

        Args:
            limit: Maximum number of recent records to fetch.

        Returns:
            List of ChatMessageRecord dictionary items ordered chronologically.
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT id, sender, text, timestamp, seq, is_outgoing, created_at
                FROM chat_messages
                ORDER BY id ASC
                LIMIT ?;
                """,
                (limit,),
            )
            rows = cursor.fetchall()
            return [
                ChatMessageRecord(
                    id=int(row["id"]),
                    sender=str(row["sender"]),
                    text=str(row["text"]),
                    timestamp=str(row["timestamp"]),
                    seq=int(row["seq"]),
                    is_outgoing=bool(row["is_outgoing"]),
                    created_at=str(row["created_at"]),
                )
                for row in rows
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
        """Persists a file transfer record to database.

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
            Row ID of the inserted file transfer record.
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO file_transfers (
                    filename, file_path, direction, size_bytes,
                    crc32_hex, speed_kbps, duration_sec, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    filename,
                    file_path,
                    direction,
                    size_bytes,
                    crc32_hex,
                    speed_kbps,
                    duration_sec,
                    status,
                ),
            )
            conn.commit()
            transfer_id = int(cursor.lastrowid or 0)
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
        """Retrieves file transfer history from database.

        Args:
            limit: Maximum number of recent transfer records to retrieve.

        Returns:
            List of FileTransferRecord dictionary items ordered newest first.
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT id, filename, file_path, direction, size_bytes,
                       crc32_hex, speed_kbps, duration_sec, status, created_at
                FROM file_transfers
                ORDER BY id DESC
                LIMIT ?;
                """,
                (limit,),
            )
            rows = cursor.fetchall()
            return [
                FileTransferRecord(
                    id=int(row["id"]),
                    filename=str(row["filename"]),
                    file_path=str(row["file_path"]),
                    direction=str(row["direction"]),
                    size_bytes=int(row["size_bytes"]),
                    crc32_hex=str(row["crc32_hex"]),
                    speed_kbps=float(row["speed_kbps"]),
                    duration_sec=float(row["duration_sec"]),
                    status=str(row["status"]),
                    created_at=str(row["created_at"]),
                )
                for row in rows
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
        """Persists a realtime optical channel telemetry sample to SQLite.

        Args:
            timestamp_epoch: Unix epoch floating timestamp.
            throughput_kbps: Measured optical link throughput in KB/s.
            rtt_ms: Round-trip flight time in milliseconds.
            packet_loss_rate: Measured packet loss ratio (0.0 to 100.0%).
            total_packets_sent: Cumulative count of packets transmitted.
            total_packets_lost: Cumulative count of lost/retried packets.

        Returns:
            Row ID of the inserted telemetry sample record.
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO telemetry_samples (
                    timestamp_epoch, throughput_kbps, rtt_ms,
                    packet_loss_rate, total_packets_sent, total_packets_lost
                ) VALUES (?, ?, ?, ?, ?, ?);
                """,
                (
                    timestamp_epoch,
                    throughput_kbps,
                    rtt_ms,
                    packet_loss_rate,
                    total_packets_sent,
                    total_packets_lost,
                ),
            )
            conn.commit()
            sample_id = int(cursor.lastrowid or 0)
            logger.debug(
                "%s Saved telemetry sample #%d (Speed=%.2f KB/s, RTT=%.1f ms, Loss=%.1f%%)",
                LogTag.STORAGE,
                sample_id,
                throughput_kbps,
                rtt_ms,
                packet_loss_rate,
            )
            return sample_id

    def get_telemetry_history(self, limit: int = 100) -> list[TelemetryRecord]:
        """Retrieves historical telemetry samples ordered by ascending time.

        Args:
            limit: Maximum number of recent samples to retrieve.

        Returns:
            List of TelemetryRecord dictionary items.
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT id, timestamp_epoch, throughput_kbps, rtt_ms,
                       packet_loss_rate, total_packets_sent, total_packets_lost, created_at
                FROM telemetry_samples
                ORDER BY id ASC
                LIMIT ?;
                """,
                (limit,),
            )
            rows = cursor.fetchall()
            return [
                TelemetryRecord(
                    id=int(row["id"]),
                    timestamp_epoch=float(row["timestamp_epoch"]),
                    throughput_kbps=float(row["throughput_kbps"]),
                    rtt_ms=float(row["rtt_ms"]),
                    packet_loss_rate=float(row["packet_loss_rate"]),
                    total_packets_sent=int(row["total_packets_sent"]),
                    total_packets_lost=int(row["total_packets_lost"]),
                    created_at=str(row["created_at"]),
                )
                for row in rows
            ]

    def export_telemetry_csv(self, output_path: Path | None = None) -> str:
        """Exports all telemetry samples to a CSV formatted string and optional file.

        Args:
            output_path: Optional file path to write CSV content to disk.

        Returns:
            CSV content as formatted string.
        """
        samples = self.get_telemetry_history(limit=5000)
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
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO app_settings (key, value, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = CURRENT_TIMESTAMP;
                """,
                (key, value),
            )
            conn.commit()

    def get_setting(self, key: str, default: str = "") -> str:
        """Reads a configuration setting value.

        Args:
            key: Setting identifier key.
            default: Fallback string value if key is not found.

        Returns:
            Persisted string value or default.
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM app_settings WHERE key = ?;", (key,))
            row = cursor.fetchone()
            return str(row["value"]) if row is not None else default
