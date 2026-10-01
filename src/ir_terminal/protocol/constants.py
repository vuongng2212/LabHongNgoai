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

# Default recommended chunk size for optical link stability at 115200 bps
DEFAULT_CHUNK_SIZE: int = 256

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
    TRANSFER_END: int = 0x31  # Finalizes file transfer session (SHA-256 verify)
    TRANSFER_PAUSE: int = 0x32  # Sender/Receiver paused due to optical beam blockage
    TRANSFER_RESUME: int = 0x33  # Resume transmission from last verified sequence


# ==============================================================================
# 3. TIMEOUTS & RETRY LIMITS
# ==============================================================================
# Default ARQ Stop-and-Wait timeout (seconds) before retrying chunk
DEFAULT_ACK_TIMEOUT_SEC: float = 0.250  # 250 ms

# Maximum retries per chunk before flagging LOS blockage / Link Failure
MAX_RETRY_COUNT: int = 5

# Serial Port Baudrate
DEFAULT_BAUDRATE: int = 115200
AVAILABLE_BAUDRATES: tuple[int, ...] = (9600, 19200, 38400, 57600, 115200, 230400)
