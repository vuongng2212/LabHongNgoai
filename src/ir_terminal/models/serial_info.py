"""Domain models for serial connection info, transfer states, messages, and telemetry."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum, auto


class TransferStatus(Enum):
    """File transfer lifecycle states."""

    IDLE = auto()
    TRANSMITTING = auto()
    PAUSED_BLOCKED = auto()  # Optical beam interrupted
    COMPLETED = auto()
    FAILED = auto()
    CANCELLED = auto()


@dataclass(frozen=True)
class SerialPortInfo:
    """Serial port metadata."""

    port: str
    description: str
    is_connected: bool = False
    baudrate: int = 115200


@dataclass(frozen=True)
class ChatMessage:
    """Chat message entity."""

    id: str
    text: str
    is_outgoing: bool
    timestamp: datetime
    is_ack_confirmed: bool = False


@dataclass(frozen=True)
class TelemetrySample:
    """Realtime physical optical channel metrics sample."""

    timestamp: float
    throughput_kbps: float
    rtt_ms: float
    packet_loss_rate: float
    total_packets_sent: int
    total_packets_lost: int
