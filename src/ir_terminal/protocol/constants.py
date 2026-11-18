"""Wire-format constants for the WinForm C# compatible IR-OWC protocol.

The firmware contract is defined by ``docs/WFF.txt``. A frame is an 8-byte
header followed by a variable payload:

    [0xAA][0x55][sequence uint32 LE][length uint16 LE][payload: length bytes]

Packet identity is carried by ``sequence``: ``0xFFFFFFFF`` marks a file
header, ``0xFFFFFFFE`` marks the end of a transfer, and any other value is a
zero-based data chunk index.
"""

from __future__ import annotations

SYNC_MAGIC: bytes = b"\xAA\x55"

SYNC_BYTE_1: int = 0xAA

SYNC_BYTE_2: int = 0x55

SEQ_HEADER: int = 0xFFFFFFFF

SEQ_END: int = 0xFFFFFFFE

PACKET_SIZE: int = 256

HEADER_OVERHEAD: int = 8

MAX_HEADER_PAYLOAD_SIZE: int = 65535

BAUD_COMMANDS: dict[int, str] = {
    19200: "00",
    9600: "01",
    4800: "10",
    2400: "11",
}

BAUD_CMD_MAP: dict[int, str] = BAUD_COMMANDS

OPTICAL_BAUDRATES: tuple[int, ...] = (2400, 4800, 9600, 19200)

SUPPORTED_BAUDRATES: tuple[int, ...] = OPTICAL_BAUDRATES

AVAILABLE_BAUDRATES: tuple[int, ...] = SUPPORTED_BAUDRATES

DEFAULT_OPTICAL_BAUDRATE: int = 19200

CONTROL_BAUDRATE: int = 115200

PHYSICAL_BAUDRATE: int = CONTROL_BAUDRATE

DEFAULT_BAUDRATE: int = DEFAULT_OPTICAL_BAUDRATE

MCU_BAUDRATES: tuple[int, ...] = (1200, 2400, 4800, 9600, 19200, 38400, 57600, 115200)

BAUD_COMMAND_TERMINATOR: bytes = b"\r"

BAUD_SWITCH_GUARD_MS: int = 50

PACED_DELAY_SAFETY_MS: int = 10

RX_INACTIVITY_TIMEOUT_SEC: float = 3.0

MAX_FILE_SIZE_BYTES: int = 0

MAX_CONFIGURABLE_FILE_SIZE_BYTES: int = 0xFFFFFFFF

RING_BUFFER_CAPACITY: int = 65536

RX_COMPACT_THRESHOLD: int = 4096

SOCKET_RECV_CHUNK: int = 4096

SOCKET_CONNECT_TIMEOUT_SEC: float = 3.0

SOCKET_READ_TIMEOUT_SEC: float = 0.02

MOCK_POLL_MS: int = 2

HW_POLL_MS: int = 10

SHUTDOWN_WAIT_MS: int = 3000

TELEMETRY_WINDOW_SEC: float = 3.0
