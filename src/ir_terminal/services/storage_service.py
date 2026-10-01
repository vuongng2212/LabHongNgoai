"""Persistent SQLite storage repository for durable chat history, file logs, and settings."""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Any

from ir_terminal.utils.logger import LogTag
from ir_terminal.utils.path_helpers import get_db_path

logger = logging.getLogger(__name__)


class StorageService:
    """Manages persistent SQLite database on local hard drive."""

    def __init__(self, db_path: Path | None = None) -> None:
        self._db_path = db_path if db_path is not None else get_db_path()
        self._init_database()

    def _get_connection(self) -> sqlite3.Connection:
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

            # 3. Application Settings Table
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
        """Persists a chat message to disk."""
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

    def get_chat_history(self, limit: int = 100) -> list[dict[str, Any]]:
        """Retrieves recent chat messages from database."""
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
                {
                    "id": row["id"],
                    "sender": row["sender"],
                    "text": row["text"],
                    "timestamp": row["timestamp"],
                    "seq": row["seq"],
                    "is_outgoing": bool(row["is_outgoing"]),
                    "created_at": row["created_at"],
                }
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
        """Persists a file transfer record to database."""
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

    def get_file_transfers(self, limit: int = 50) -> list[dict[str, Any]]:
        """Retrieves file transfer history from database."""
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
                {
                    "id": row["id"],
                    "filename": row["filename"],
                    "file_path": row["file_path"],
                    "direction": row["direction"],
                    "size_bytes": row["size_bytes"],
                    "crc32_hex": row["crc32_hex"],
                    "speed_kbps": row["speed_kbps"],
                    "duration_sec": row["duration_sec"],
                    "status": row["status"],
                    "created_at": row["created_at"],
                }
                for row in rows
            ]

    def save_setting(self, key: str, value: str) -> None:
        """Persists a key-value setting."""
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
        """Reads a setting value."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM app_settings WHERE key = ?;", (key,))
            row = cursor.fetchone()
            return str(row["value"]) if row is not None else default
