"""Protocol constants, frame magic bytes, opcodes, and timing parameters.

Hardware Interface: STM32F407 + MCP2122 + TFDU4101 (Infrared Transceiver).
Frame Alignment: Little-Endian for multi-byte numeric fields.
"""

from __future__ import annotations

SOF_MAGIC: int = 0xAA55

EOF_MAGIC: int = 0x55AA

HEADER_SIZE: int = 6

FOOTER_SIZE: int = 6

TOTAL_OVERHEAD: int = HEADER_SIZE + FOOTER_SIZE

MAX_PAYLOAD_SIZE: int = 512

MAX_CHUNK_DATA_SIZE: int = MAX_PAYLOAD_SIZE - 2  # 510

MAX_EXTENDED_CHUNK_DATA_SIZE: int = MAX_PAYLOAD_SIZE - 4  # 508

MAX_V1_CHUNK_COUNT: int = 0xFFFF

MAX_EXTENDED_CHUNK_COUNT: int = 0xFFFFFFFF

MAX_CHUNK_COUNT: int = MAX_EXTENDED_CHUNK_COUNT

DEFAULT_CHUNK_SIZE: int = 240

MAX_FILE_SIZE_BYTES: int = 0

PROTOCOL_MAX_FILE_SIZE_BYTES: int = MAX_EXTENDED_CHUNK_COUNT * MAX_EXTENDED_CHUNK_DATA_SIZE

DEFAULT_HEALTH_CHECK_SEC: float = 5.0

MAX_FRAME_SIZE: int = HEADER_SIZE + MAX_PAYLOAD_SIZE + FOOTER_SIZE


class Opcode:
    """Protocol operation codes (1 byte uint8)."""

    DATA_CHUNK: int = 0x01  # Binary data chunk for file transfer
    DATA_TEXT: int = 0x02  # UTF-8 text message for realtime chat

    ACK: int = 0x10  # Acknowledge successful packet reception
    NACK: int = 0x11  # Negative Acknowledge (CRC error or out-of-order)

    PING: int = 0x20  # Optical link ping echo request
    PONG: int = 0x21  # Optical link pong echo response

    TRANSFER_START: int = 0x30  # Initiates file transfer session (metadata payload)
    TRANSFER_END: int = 0x31  # Finalizes file transfer session (CRC-32 verify; SHA-256 is Post-MVP)
    TRANSFER_PAUSE: int = 0x32  # Pause/abort session (MVP: ABORT payload on user cancel)
    TRANSFER_RESUME: int = 0x33  # Reserved for Post-MVP auto-resume, not used by MVP ARQ

    TRANSFER_START_LARGE: int = 0x34
    DATA_CHUNK_LARGE: int = 0x35
    TRANSFER_END_LARGE: int = 0x36


class AckStatus:
    """Acknowledgment status codes for Stop-and-Wait ARQ (1 byte uint8)."""

    OK: int = 0x00  # Packet successfully received and verified
    CRC_ERROR: int = 0x01  # CRC-32 checksum mismatch
    OUT_OF_ORDER: int = 0x02  # Sequence number out of expected order
    TIMEOUT: int = 0x03  # Transmission timeout / retry limit exceeded


DEFAULT_ACK_TIMEOUT_SEC: float = 0.250  # 250 ms

MAX_RETRY_COUNT: int = 5

DEFAULT_BAUDRATE: int = 115200
SUPPORTED_BAUDRATES: tuple[int, ...] = (
    9600,
    38400,
    57600,
    115200,
)
AVAILABLE_BAUDRATES: tuple[int, ...] = SUPPORTED_BAUDRATES

RING_BUFFER_CAPACITY: int = 65536

RX_COMPACT_THRESHOLD: int = 4096

SOCKET_RECV_CHUNK: int = 4096
SOCKET_CONNECT_TIMEOUT_SEC: float = 3.0
SOCKET_READ_TIMEOUT_SEC: float = 0.02

MOCK_POLL_MS: int = 2
HW_POLL_MS: int = 10

SHUTDOWN_WAIT_MS: int = 3000

TELEMETRY_WINDOW_SEC: float = 3.0
