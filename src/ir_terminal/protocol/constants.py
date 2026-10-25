"""Protocol constants, frame magic bytes, opcodes, and timing parameters.

Hardware Interface: STM32F407 + MCP2122 + TFDU4101 (Infrared Transceiver).
Frame Alignment: Little-Endian for multi-byte numeric fields.
"""

from __future__ import annotations

# ==============================================================================
# 1. FRAME DELIMITERS & HEADER GEOMETRY
# ==============================================================================
# Start of Frame (SOF): 0xAA55 -> 2 bytes (0x55, 0xAA in Little-Endian)
SOF_MAGIC: int = 0xAA55

# End of Frame (EOF): 0x55AA -> 2 bytes (0xAA, 0x55 in Little-Endian)
EOF_MAGIC: int = 0x55AA

# Fixed header size: SOF(2) + SEQ(1) + CMD(1) + LEN(2) = 6 bytes
HEADER_SIZE: int = 6

# Footer size: CRC32(4) + EOF(2) = 6 bytes
FOOTER_SIZE: int = 6

# Total non-payload framing overhead: 6 + 6 = 12 bytes
TOTAL_OVERHEAD: int = HEADER_SIZE + FOOTER_SIZE

# Maximum payload size per chunk (bytes) to prevent MCU internal ring buffer overflow
MAX_PAYLOAD_SIZE: int = 512

# Legacy DATA_CHUNK carries a 2-byte chunk_id prefix inside the frame payload.
MAX_CHUNK_DATA_SIZE: int = MAX_PAYLOAD_SIZE - 2  # 510

# Large-transfer extension DATA_CHUNK_LARGE carries a 4-byte chunk_id prefix.
MAX_EXTENDED_CHUNK_DATA_SIZE: int = MAX_PAYLOAD_SIZE - 4  # 508

# Legacy TRANSFER_START encodes total_chunks as uint16, capping one session at 65535 chunks.
MAX_V1_CHUNK_COUNT: int = 0xFFFF

# Large-transfer extension encodes total_chunks as uint32 (~4.29 billion chunks).
MAX_EXTENDED_CHUNK_COUNT: int = 0xFFFFFFFF

# Backwards-compatible alias used by the transfer service for the effective cap.
MAX_CHUNK_COUNT: int = MAX_EXTENDED_CHUNK_COUNT

# Default chunk size for optical link stability at 115200 bps (MVP binding per PROTOCOL.md §7)
DEFAULT_CHUNK_SIZE: int = 240

# Application-level file size ceiling. 0 means UNLIMITED: the service streams any
# file the filesystem can hold (subject only to the wire-format chunk-count cap).
MAX_FILE_SIZE_BYTES: int = 0

# Structural wire-format ceiling of the large-transfer extension (~2.18 TB with
# 508-byte chunks). This bounds chunk-count encoding, not the product policy.
PROTOCOL_MAX_FILE_SIZE_BYTES: int = (
    MAX_EXTENDED_CHUNK_COUNT * MAX_EXTENDED_CHUNK_DATA_SIZE
)

# Default interval (seconds) between automatic link health-check pings
DEFAULT_HEALTH_CHECK_SEC: float = 5.0

# Maximum total frame length: Header(6) + MaxPayload(512) + Footer(6) = 524 bytes
MAX_FRAME_SIZE: int = HEADER_SIZE + MAX_PAYLOAD_SIZE + FOOTER_SIZE


# ==============================================================================
# 2. OPCODES & COMMAND IDENTIFIERS
# ==============================================================================
class Opcode:
    """Protocol operation codes (1 byte uint8)."""

    # Data & Payload commands
    DATA_CHUNK: int = 0x01  # Binary data chunk for file transfer
    DATA_TEXT: int = 0x02  # UTF-8 text message for realtime chat

    # Acknowledgement & Flow Control
    ACK: int = 0x10  # Acknowledge successful packet reception
    NACK: int = 0x11  # Negative Acknowledge (CRC error or out-of-order)

    # Link Diagnostics & Heartbeat
    PING: int = 0x20  # Optical link ping echo request
    PONG: int = 0x21  # Optical link pong echo response

    # Session & State Control
    TRANSFER_START: int = 0x30  # Initiates file transfer session (metadata payload)
    TRANSFER_END: int = 0x31  # Finalizes file transfer session (CRC-32 verify; SHA-256 is Post-MVP)
    TRANSFER_PAUSE: int = 0x32  # Pause/abort session (MVP: ABORT payload on user cancel)
    TRANSFER_RESUME: int = 0x33  # Reserved for Post-MVP auto-resume, not used by MVP ARQ

    # Large-file extension (same framing; widened integer fields). Used
    # automatically when a file needs more than 65535 chunks or exceeds u32 size.
    TRANSFER_START_LARGE: int = 0x34
    DATA_CHUNK_LARGE: int = 0x35
    TRANSFER_END_LARGE: int = 0x36


class AckStatus:
    """Acknowledgment status codes for Stop-and-Wait ARQ (1 byte uint8)."""

    OK: int = 0x00  # Packet successfully received and verified
    CRC_ERROR: int = 0x01  # CRC-32 checksum mismatch
    OUT_OF_ORDER: int = 0x02  # Sequence number out of expected order
    TIMEOUT: int = 0x03  # Transmission timeout / retry limit exceeded


# ==============================================================================
# 3. TIMEOUTS & RETRY LIMITS
# ==============================================================================
# Default ARQ Stop-and-Wait timeout (seconds) before retrying chunk
DEFAULT_ACK_TIMEOUT_SEC: float = 0.250  # 250 ms

# Maximum retries per chunk before flagging LOS blockage / Link Failure
MAX_RETRY_COUNT: int = 5

# Serial Port Baudrate
DEFAULT_BAUDRATE: int = 115200
SUPPORTED_BAUDRATES: tuple[int, ...] = (
    9600,
    38400,
    57600,
    115200,
)
AVAILABLE_BAUDRATES: tuple[int, ...] = SUPPORTED_BAUDRATES

# ==============================================================================
# 4. TRANSPORT / BUFFER / POLL TUNING (single source for magic numbers)
# ==============================================================================
# Fixed-capacity RX ring buffer (bytes). Shared by MockSerialPort RX path.
RING_BUFFER_CAPACITY: int = 65536

# Worker RX accumulation compaction threshold (bytes of consumed prefix).
RX_COMPACT_THRESHOLD: int = 4096

# TCP socket chunk / timeouts for dual-mock LINK_A/B transport.
SOCKET_RECV_CHUNK: int = 4096
SOCKET_CONNECT_TIMEOUT_SEC: float = 3.0
SOCKET_READ_TIMEOUT_SEC: float = 0.02

# Worker poll intervals (ms): mock stays snappy, hardware conservative.
MOCK_POLL_MS: int = 2
HW_POLL_MS: int = 10

# Background thread shutdown grace (ms).
SHUTDOWN_WAIT_MS: int = 3000

# Telemetry sliding window (seconds) for throughput averaging.
TELEMETRY_WINDOW_SEC: float = 3.0
