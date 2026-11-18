"""Immutable packet model for the WinForm C# compatible wire format."""

from __future__ import annotations

from dataclasses import dataclass

from ir_terminal.protocol.constants import SEQ_END, SEQ_HEADER


@dataclass(frozen=True)
class WinFormPacket:
    """One decoded frame from the optical link.

    Attributes:
        sequence: Frame identifier. ``SEQ_HEADER`` marks a file header,
            ``SEQ_END`` marks the end of a transfer, and any other value is a
            zero-based data chunk index.
        length: Payload byte count as carried by the wire header.
        data: Raw payload bytes.
    """

    sequence: int
    length: int
    data: bytes

    @property
    def is_header(self) -> bool:
        """Returns True when this frame announces a new incoming file."""
        return self.sequence == SEQ_HEADER

    @property
    def is_end(self) -> bool:
        """Returns True when this frame terminates an incoming file."""
        return self.sequence == SEQ_END

    @property
    def is_data(self) -> bool:
        """Returns True when this frame carries a zero-based data chunk."""
        return not self.is_header and not self.is_end
