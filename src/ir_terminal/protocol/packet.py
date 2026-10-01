"""Immutable domain data classes for protocol packets and headers."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Header:
    """Fixed-size protocol header (6 bytes).

    Attributes:
        sof: Start of Frame magic delimiter (0xAA55).
        seq: Sequence number (0..255).
        opcode: Command operation code (see Opcode constants).
        length: Byte length of the accompanying payload.
    """

    sof: int
    seq: int
    opcode: int
    length: int


@dataclass(frozen=True)
class Packet:
    """Complete verified protocol packet including Header, Payload, and CRC-32.

    Attributes:
        header: Parsed 6-byte header metadata.
        payload: Raw binary payload data.
        crc32: 32-bit CRC checksum computed over Header + Payload.
        eof: End of Frame magic delimiter (0x55AA).
    """

    header: Header
    payload: bytes
    crc32: int
    eof: int

    @property
    def seq(self) -> int:
        """Convenience property for packet sequence number."""
        return self.header.seq

    @property
    def opcode(self) -> int:
        """Convenience property for packet opcode."""
        return self.header.opcode

    @property
    def payload_length(self) -> int:
        """Convenience property for packet payload length."""
        return self.header.length
