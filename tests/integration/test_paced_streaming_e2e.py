"""End-to-end paced streaming over an in-process optical loopback."""

from __future__ import annotations

import time
from pathlib import Path
from typing import ClassVar, cast

from PySide6.QtCore import QObject, Signal
from pytestqt.qtbot import QtBot

from ir_terminal.core.serial_manager import SerialManager
from ir_terminal.protocol.constants import SEQ_END, SEQ_HEADER
from ir_terminal.protocol.framer import pack_packet, parse_stream
from ir_terminal.services.file_transfer_service import FileTransferService, TransferState
from ir_terminal.services.storage_service import StorageService


class LoopbackManager(QObject):
    """Forwards transmitted frames to a peer receiver with optional drops."""

    packet_received: ClassVar[Signal] = Signal(object)

    def __init__(self, baud: int = 19200, peer: LoopbackManager | None = None) -> None:
        super().__init__()
        self.current_baud = baud
        self.peer = peer
        self.sent: list[tuple[int, bytes]] = []
        self.drop_sequences: set[int] = set()

    def send_packet(self, sequence: int, data: bytes = b"") -> None:
        """Serializes one frame and delivers it to the peer immediately."""
        payload = bytes(data)
        self.sent.append((sequence, payload))
        if self.peer is None or sequence in self.drop_sequences:
            return
        packets, _ = parse_stream(bytearray(pack_packet(sequence, payload)))
        for packet in packets:
            self.peer.packet_received.emit(packet)


def make_node(
    tmp_path: Path, name: str, peer: LoopbackManager | None
) -> tuple[FileTransferService, LoopbackManager]:
    """Builds one streaming endpoint with isolated storage and receive folder."""
    storage = StorageService(data_dir=tmp_path / name / "data")
    manager = LoopbackManager(peer=peer)
    service = FileTransferService(
        cast(SerialManager, manager),
        storage_service=storage,
        transfers_dir=tmp_path / name / "transfers",
    )
    return service, manager


class TestPacedStreamingE2E:
    """Streams complete files and validates the on-disk result."""

    def test_file_streams_from_tx_to_rx(self, qtbot: QtBot, tmp_path: Path) -> None:
        tx_service, tx_manager = make_node(tmp_path, "tx", None)
        rx_service, rx_manager = make_node(tmp_path, "rx", tx_manager)
        tx_manager.peer = rx_manager

        source = tmp_path / "source.bin"
        source.write_bytes(bytes(range(256)) * 3 + b"tail")

        started = time.monotonic()
        with qtbot.waitSignal(rx_service.transfer_completed, timeout=8000):
            tx_service.start_transfer(str(source))
        elapsed = time.monotonic() - started

        sequences = [sequence for sequence, _ in tx_manager.sent]
        assert sequences == [SEQ_HEADER, 0, 1, 2, 3, SEQ_END]
        assert tx_service.state is TransferState.COMPLETED
        assert rx_service.state is TransferState.COMPLETED
        assert (tmp_path / "rx" / "transfers" / "source.bin").read_bytes() == source.read_bytes()
        assert elapsed >= 0.3

    def test_lost_frame_leaves_gap_and_partial_marker(
        self, qtbot: QtBot, tmp_path: Path
    ) -> None:
        tx_service, tx_manager = make_node(tmp_path, "tx", None)
        rx_service, rx_manager = make_node(tmp_path, "rx", tx_manager)
        tx_manager.drop_sequences = {1}
        tx_manager.peer = rx_manager

        source = tmp_path / "gap.bin"
        source.write_bytes(b"Z" * 600)

        with qtbot.waitSignal(rx_service.transfer_failed, timeout=8000) as blocker:
            tx_service.start_transfer(str(source))

        assert "1 lost" in blocker.args[0]
        assert rx_service.state is TransferState.FAILED
        assert tx_service.state is TransferState.COMPLETED
        received = (tmp_path / "rx" / "transfers" / "gap.bin").read_bytes()
        assert len(received) == 600
        assert received[:256] == b"Z" * 256
        assert received[256:512] == b"\x00" * 256
        assert rx_manager.sent == []
