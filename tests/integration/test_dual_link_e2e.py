"""Integration test for dual-mock mode: 2 SerialManagers via broker sockets."""

from __future__ import annotations

import socket
import tempfile
import threading
import time
import zlib
from pathlib import Path

from pytestqt.qtbot import QtBot

from ir_terminal.core import socket_transport as socket_transport_module
from ir_terminal.core.link_broker import LinkBroker
from ir_terminal.core.serial_manager import SerialManager
from ir_terminal.protocol.constants import Opcode
from ir_terminal.services.chat_service import ChatService
from ir_terminal.services.file_transfer_service import FileTransferService, TransferState
from ir_terminal.services.storage_service import StorageService
from scripts.virtual_optical_link import VirtualOpticalLink


def _free_ports(count: int) -> list[int]:
    """Reserves and returns ``count`` distinct loopback TCP ports."""
    ports: list[int] = []
    holders: list[socket.socket] = []
    try:
        while len(ports) < count:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.bind(("127.0.0.1", 0))
            port = int(sock.getsockname()[1])
            if port not in ports:
                ports.append(port)
                holders.append(sock)
    finally:
        for sock in holders:
            sock.close()
    return ports



def test_dual_link_file_transfer_via_broker(qtbot: QtBot, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """App A (LINK_A) transmits a file through the broker to App B (LINK_B)."""
    broker = VirtualOpticalLink(port_a=6011, port_b=6012, latency_ms=1.0)
    broker_thread = threading.Thread(target=broker.run, daemon=True)
    broker_thread.start()
    time.sleep(0.2)

    monkeypatch.setitem(socket_transport_module.LINK_PORT_MAP, "LINK_A", ("127.0.0.1", 6011))
    monkeypatch.setitem(socket_transport_module.LINK_PORT_MAP, "LINK_B", ("127.0.0.1", 6012))

    mgr_a = SerialManager()
    mgr_b = SerialManager()
    try:
        with qtbot.waitSignal(mgr_a.connected, timeout=5000):
            mgr_a.connect_port("LINK_A", 115200)
        with qtbot.waitSignal(mgr_b.connected, timeout=5000):
            mgr_b.connect_port("LINK_B", 115200)

        with tempfile.TemporaryDirectory() as tmp_dir:
            dir_path = Path(tmp_dir)
            source_file = dir_path / "dual_mock.bin"
            content = b"DUAL_MOCK_LINK_FRAME_" * 60  # 1260 bytes (~6 chunks @ 240B)
            source_file.write_bytes(content)
            expected_crc = zlib.crc32(content) & 0xFFFFFFFF

            storage_b = StorageService(data_dir=dir_path / "b_data")
            service_a = FileTransferService(serial_manager=mgr_a, chunk_size=240)
            service_b = FileTransferService(
                serial_manager=mgr_b,
                chunk_size=240,
                storage_service=storage_b,
                transfers_dir=dir_path / "b_downloads",
            )

            with (
                qtbot.waitSignal(service_a.transfer_completed, timeout=15000) as blocker_a,
                qtbot.waitSignal(service_b.transfer_completed, timeout=15000) as blocker_b,
            ):
                service_a.start_transfer(str(source_file))

            assert blocker_a.args[0] == source_file.name
            assert blocker_a.args[1] == len(content)
            assert blocker_a.args[3] == f"0x{expected_crc:08X}"
            assert service_a.state == TransferState.COMPLETED
            assert blocker_b.args[3] == f"0x{expected_crc:08X}"
            assert service_b.state == TransferState.COMPLETED
            received = dir_path / "b_downloads" / source_file.name
            assert received.exists()
            assert received.read_bytes() == content
    finally:
        mgr_a.shutdown()
        mgr_b.shutdown()


def test_configured_link_endpoints_relay_chat(qtbot: QtBot, tmp_path: Path) -> None:
    """SerialManager honors per-launch LINK endpoints and relays chat packets."""
    port_a, port_b = _free_ports(2)
    broker = LinkBroker(port_a=port_a, port_b=port_b, latency_ms=0.0)
    broker.start()
    endpoints = {
        "LINK_A": ("127.0.0.1", port_a),
        "LINK_B": ("127.0.0.1", port_b),
    }
    mgr_a = SerialManager(
        link_endpoints=endpoints, include_link=True, link_connect_retries=5
    )
    mgr_b = SerialManager(
        link_endpoints=endpoints, include_link=True, link_connect_retries=5
    )
    try:
        assert "LINK_A" in mgr_a.get_port_list()
        assert "LINK_B" in mgr_b.get_port_list()

        with qtbot.waitSignal(mgr_a.connected, timeout=5000):
            mgr_a.connect_port("LINK_A", 115200)
        with qtbot.waitSignal(mgr_b.connected, timeout=5000):
            mgr_b.connect_port("LINK_B", 115200)

        chat_b = ChatService(mgr_b, StorageService(data_dir=tmp_path / "b"))
        with qtbot.waitSignal(chat_b.message_received, timeout=5000) as blocker:
            mgr_a.send_packet(seq=7, opcode=Opcode.DATA_TEXT, payload=b"configured-link")

        text, _timestamp, is_outgoing = blocker.args
        assert text == "configured-link"
        assert is_outgoing is False
    finally:
        mgr_a.shutdown()
        mgr_b.shutdown()
        broker.stop()
