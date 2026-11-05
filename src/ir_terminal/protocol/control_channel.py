"""ASCII control-channel grammar for optical baudrate switching.

Control lines are plain text terminated by ``\\n`` and are never wrapped in a
binary frame. They are parsed only outside validated binary frames.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from ir_terminal.protocol.constants import OPTICAL_BAUDRATES

CONTROL_LINE_MAX_BYTES: int = 64
CONTROL_REASON_MAX_BYTES: int = 48
CONTROL_BAUD_MAX_DIGITS: int = 6

CONTROL_LINE_TERMINATOR: bytes = b"\n"


class ControlKind(StrEnum):
    """Recognised control-message prefixes."""

    SET_BAUD = "SET_BAUD"
    BAUD_OK = "BAUD_OK"
    NOTIFY_BAUD = "NOTIFY_BAUD"
    BAUD_ERR = "BAUD_ERR"


_BAUD_COMMAND_KINDS: frozenset[ControlKind] = frozenset(
    (ControlKind.SET_BAUD, ControlKind.BAUD_OK, ControlKind.NOTIFY_BAUD)
)


@dataclass(frozen=True, slots=True)
class ControlMessage:
    """One validated ASCII control line."""

    kind: ControlKind
    raw: str
    baudrate: int | None = None
    reason: str = ""

    def to_line(self) -> str:
        """Returns the canonical wire form without the trailing newline."""
        return self.raw


def _normalize_text(line: object) -> str | None:
    if isinstance(line, (bytes, bytearray, memoryview)):
        try:
            text = bytes(line).decode("ascii")
        except UnicodeDecodeError:
            return None
    elif isinstance(line, str):
        text = line
    else:
        return None
    text = text.rstrip("\r\n")
    if not text:
        return None
    if len(text) > CONTROL_LINE_MAX_BYTES:
        return None
    if "\r" in text or "\n" in text:
        return None
    try:
        text.encode("ascii")
    except UnicodeEncodeError:
        return None
    return text


def parse_control_line(line: str | bytes) -> ControlMessage | None:
    """Parses one control line against the baud-control grammar.

    Args:
        line: Control line with or without the trailing ``\\n`` / ``\\r\\n``.

    Returns:
        A validated :class:`ControlMessage`, or ``None`` when the line is not a
        recognised control message.
    """
    text = _normalize_text(line)
    if text is None:
        return None

    prefix, separator, argument = text.partition(":")
    if not separator:
        return None
    try:
        kind = ControlKind(prefix)
    except ValueError:
        return None

    if kind in _BAUD_COMMAND_KINDS:
        if not argument.isdigit():
            return None
        if not 1 <= len(argument) <= CONTROL_BAUD_MAX_DIGITS:
            return None
        baudrate = int(argument)
        if baudrate not in OPTICAL_BAUDRATES:
            return None
        return ControlMessage(kind=kind, raw=text, baudrate=baudrate)

    if not 1 <= len(argument) <= CONTROL_REASON_MAX_BYTES:
        return None
    if not all(0x20 <= ord(character) <= 0x7E for character in argument):
        return None
    return ControlMessage(kind=kind, raw=text, reason=argument)


def is_control_line(line: str | bytes) -> bool:
    """Returns True when ``line`` is a recognised control message."""
    return parse_control_line(line) is not None


def normalize_outgoing_control_line(message: str) -> str:
    """Validates an outgoing control line and strips its terminator.

    Args:
        message: Control line to transmit, with or without ``\\r\\n``.

    Returns:
        The normalised line without a terminator.

    Raises:
        ValueError: If the line is empty, non-ASCII, overlong, or contains an
            embedded newline.
    """
    if not isinstance(message, str):
        raise ValueError("control line must be a string")
    line = message.rstrip("\r\n")
    if not line:
        raise ValueError("control line is empty")
    if "\r" in line or "\n" in line:
        raise ValueError("control line contains an embedded newline")
    if len(line) > CONTROL_LINE_MAX_BYTES:
        raise ValueError(f"control line exceeds {CONTROL_LINE_MAX_BYTES} bytes")
    try:
        line.encode("ascii")
    except UnicodeEncodeError as exc:
        raise ValueError("control line must be ASCII") from exc
    return line


def encode_control_line(message: str) -> bytes:
    """Encodes a validated control line with exactly one trailing newline."""
    return (normalize_outgoing_control_line(message) + "\n").encode("ascii")


def format_set_baud(baudrate: int) -> str:
    """Builds a canonical ``SET_BAUD:<baud>`` command line."""
    if baudrate not in OPTICAL_BAUDRATES:
        raise ValueError(f"unsupported optical baudrate {baudrate}")
    return f"{ControlKind.SET_BAUD.value}:{int(baudrate)}"


def format_baud_ok(baudrate: int) -> str:
    """Builds a canonical ``BAUD_OK:<baud>`` acknowledgement line."""
    return f"{ControlKind.BAUD_OK.value}:{int(baudrate)}"


def format_notify_baud(baudrate: int) -> str:
    """Builds a canonical ``NOTIFY_BAUD:<baud>`` notification line."""
    return f"{ControlKind.NOTIFY_BAUD.value}:{int(baudrate)}"


def format_baud_err(reason: str) -> str:
    """Builds a canonical ``BAUD_ERR:<reason>`` error line."""
    return f"{ControlKind.BAUD_ERR.value}:{reason}"
