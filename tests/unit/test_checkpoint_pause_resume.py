"""Bộ kiểm thử đơn vị và tích hợp cho cơ chế Chunked Checkpoint ACK, Pause khi mất tia quang và Resume.

Kiểm tra:
1. Thuộc tính nhận diện gói tin WinFormPacket cho các khung điều khiển mở rộng.
2. Quá trình truyền file có trao đổi Checkpoint ACK giữa bên phát và bên nhận.
3. Fallback tự động sang Open-Loop thuần khi bên nhận không hỗ trợ ACK.
4. Cơ chế cấu hình bật tắt Checkpoint ACK qua Settings.
5. Cơ chế phát hiện mất tia quang (Optical LOS Blocked): hết hạn ACK -> probe retry -> tự động PAUSED.
6. Cơ chế khôi phục phiên truyền (Resume): seek con trỏ file, gửi SEQ_RESUME_REQUEST, truyền tiếp từ contiguous seq hoặc 0.
7. Cơ chế tạm dừng và phục hồi phía nhận (RX): Watchdog chuyển sang PAUSED, giữ file .part, Resume Request hồi sinh phiên nhận.
8. Tính toán ACK theo chuỗi gói liên tục lớn nhất (Highest Contiguous Sequence).
9. Cơ chế hủy phiên truyền (Abort) khi đang ở trạng thái PAUSED.
10. Cầu nối BackendBridge phơi bày đầy đủ Signal và Slot phục vụ giao diện Web.
"""

from __future__ import annotations

import struct
from collections.abc import Callable
from pathlib import Path
from typing import ClassVar, cast

from PySide6.QtCore import QObject, QTimer, Signal
from pytestqt.qtbot import QtBot

from ir_terminal.core.serial_manager import SerialManager
from ir_terminal.protocol.constants import (
    CHECKPOINT_MAX_PROBE_RETRIES,
    CHECKPOINT_WINDOW_CHUNKS,
    PACKET_SIZE,
    SEQ_CHECKPOINT_ACK,
    SEQ_CHECKPOINT_PROBE,
    SEQ_END,
    SEQ_HEADER,
    SEQ_RESUME_REQUEST,
)
from ir_terminal.protocol.framer import pack_header_payload, pack_packet, parse_stream
from ir_terminal.protocol.packet import WinFormPacket
from ir_terminal.services.file_transfer_service import (
    FileTransferService,
    TransferState,
)
from ir_terminal.services.storage_service import StorageService
from ir_terminal.ui.backend_bridge import BackendBridge


class RecordingSerialManager(QObject):
    """Mô phỏng bộ điều phối cổng nối tiếp để ghi nhận và chuyển tiếp khung truyền."""

    packet_received: ClassVar[Signal] = Signal(object)

    def __init__(
        self,
        baud: int = 19200,
        on_send: Callable[[int, bytes], None] | None = None,
    ) -> None:
        super().__init__()
        self.current_baud = baud
        self.current_data_port = "COM1"
        self.current_ctrl_port = "COM2"
        self.sent: list[tuple[int, bytes]] = []
        self.on_send = on_send

    def send_packet(self, sequence: int, data: bytes = b"") -> None:
        """Ghi nhận một khung truyền gửi đi và gọi hàm chặn bắt nếu được cấu hình."""
        payload = bytes(data)
        self.sent.append((sequence, payload))
        if self.on_send is not None:
            self.on_send(sequence, payload)

    def deliver(self, sequence: int, data: bytes = b"") -> None:
        """Phát tín hiệu tiếp nhận một khung truyền đến."""
        self.packet_received.emit(
            WinFormPacket(sequence=sequence, length=len(data), data=data)
        )


class TwoWayLoopbackManager(QObject):
    """Mô phỏng kênh truyền quang hai chiều có hỗ trợ chặn tia quang theo sequence."""

    packet_received: ClassVar[Signal] = Signal(object)

    def __init__(
        self,
        baud: int = 19200,
        peer: TwoWayLoopbackManager | None = None,
        on_send: Callable[[int, bytes], None] | None = None,
    ) -> None:
        super().__init__()
        self.current_baud = baud
        self.current_data_port = "COM1"
        self.current_ctrl_port = "COM2"
        self.peer = peer
        self.on_send = on_send
        self.sent: list[tuple[int, bytes]] = []
        self.blocked: bool = False
        self.drop_sequences: set[int] = set()

    def send_packet(self, sequence: int, data: bytes = b"") -> None:
        """Gửi khung tin sang trạm đối diện trừ khi tia quang bị che chắn."""
        payload = bytes(data)
        self.sent.append((sequence, payload))
        if self.on_send is not None:
            self.on_send(sequence, payload)
        if self.peer is None or self.blocked or sequence in self.drop_sequences:
            return
        frames, _ = parse_stream(bytearray(pack_packet(sequence, payload)))
        for frame in frames:
            self.peer.packet_received.emit(frame)


def make_service(
    tmp_path: Path,
    baud: int = 19200,
    *,
    window: int = CHECKPOINT_WINDOW_CHUNKS,
    on_send: Callable[[int, bytes], None] | None = None,
) -> tuple[FileTransferService, RecordingSerialManager]:
    """Tạo dịch vụ truyền nhận file kèm bộ điều phối ghi nhận khung truyền."""
    storage = StorageService(data_dir=tmp_path / "data")
    manager = RecordingSerialManager(baud, on_send=on_send)
    service = FileTransferService(
        cast(SerialManager, manager),
        storage_service=storage,
        transfers_dir=tmp_path / "transfers",
    )
    service.checkpoint_window = window
    return service, manager


class TestPacketControlProperties:
    """Kiểm tra các thuộc tính phân loại gói tin điều khiển mở rộng trên WinFormPacket."""

    def test_checkpoint_ack_packet(self) -> None:
        packet = WinFormPacket(
            sequence=SEQ_CHECKPOINT_ACK,
            length=4,
            data=struct.pack("<i", 15),
        )
        assert packet.is_checkpoint_ack is True
        assert packet.is_checkpoint_probe is False
        assert packet.is_resume_request is False
        assert packet.is_header is False
        assert packet.is_end is False
        assert packet.is_data is False

    def test_checkpoint_probe_packet(self) -> None:
        packet = WinFormPacket(
            sequence=SEQ_CHECKPOINT_PROBE,
            length=4,
            data=struct.pack("<i", 15),
        )
        assert packet.is_checkpoint_ack is False
        assert packet.is_checkpoint_probe is True
        assert packet.is_resume_request is False
        assert packet.is_data is False

    def test_resume_request_packet(self) -> None:
        packet = WinFormPacket(
            sequence=SEQ_RESUME_REQUEST,
            length=4,
            data=struct.pack("<I", 16),
        )
        assert packet.is_checkpoint_ack is False
        assert packet.is_checkpoint_probe is False
        assert packet.is_resume_request is True
        assert packet.is_data is False

    def test_regular_data_packet(self) -> None:
        packet = WinFormPacket(sequence=0, length=256, data=b"X" * 256)
        assert packet.is_data is True
        assert packet.is_checkpoint_ack is False
        assert packet.is_checkpoint_probe is False
        assert packet.is_resume_request is False


class TestCheckpointPauseResumeLogic:
    """Kiểm tra logic State Machine, Checkpoint ACK, phát hiện mất tia và Resume."""

    def test_transfer_with_checkpoint_ack_completes(
        self, qtbot: QtBot, tmp_path: Path
    ) -> None:
        manager_ref: list[RecordingSerialManager] = []

        def on_packet_sent(sequence: int, data: bytes) -> None:
            if sequence == 3 and manager_ref:
                mgr = manager_ref[0]
                QTimer.singleShot(
                    5,
                    lambda: mgr.deliver(
                        SEQ_CHECKPOINT_ACK, struct.pack("<i", 3)
                    ),
                )

        service, manager = make_service(
            tmp_path, window=4, on_send=on_packet_sent
        )
        manager_ref.append(manager)
        service._quiescent_ms = 2
        service._tx_ack_timeout_ms = 80

        source = tmp_path / "sample.bin"
        source.write_bytes(b"A" * (PACKET_SIZE * 6))

        with qtbot.waitSignal(service.transfer_completed, timeout=4000):
            service.start_transfer(str(source))

        sent_sequences = [seq for seq, _ in manager.sent]
        assert sent_sequences == [SEQ_HEADER, 0, 1, 2, 3, 4, 5, SEQ_END]
        assert service.state == TransferState.COMPLETED

    def test_fallback_to_open_loop_when_peer_does_not_ack(
        self, qtbot: QtBot, tmp_path: Path
    ) -> None:
        """Kiểm tra Issue 1: Tự động Fallback sang Open-Loop thuần khi bên nhận không có ACK."""
        service, manager = make_service(tmp_path, window=4)
        service._quiescent_ms = 2
        service._tx_ack_timeout_ms = 20

        source = tmp_path / "standalone.bin"
        source.write_bytes(b"S" * (PACKET_SIZE * 6))

        with qtbot.waitSignal(service.transfer_completed, timeout=4000):
            service.start_transfer(str(source))

        assert service.state == TransferState.COMPLETED
        assert service._tx_checkpoint_enabled is False
        probes = [seq for seq, _ in manager.sent if seq == SEQ_CHECKPOINT_PROBE]
        assert len(probes) == CHECKPOINT_MAX_PROBE_RETRIES

    def test_checkpoint_ack_disabled_setting_runs_pure_open_loop(
        self, qtbot: QtBot, tmp_path: Path
    ) -> None:
        """Kiểm tra Issue 1: Tùy chọn cấu hình tắt Checkpoint ACK chạy thuần Open-Loop."""
        service, manager = make_service(tmp_path, window=2)
        service.checkpoint_ack_enabled = False
        service._quiescent_ms = 2

        source = tmp_path / "pure_open.bin"
        source.write_bytes(b"O" * (PACKET_SIZE * 4))

        with qtbot.waitSignal(service.transfer_completed, timeout=4000):
            service.start_transfer(str(source))

        assert service.state == TransferState.COMPLETED
        probes = [seq for seq, _ in manager.sent if seq == SEQ_CHECKPOINT_PROBE]
        assert len(probes) == 0

    def test_los_blocked_triggers_probe_and_pause(
        self, qtbot: QtBot, tmp_path: Path
    ) -> None:
        manager_ref: list[RecordingSerialManager] = []

        def ack_first_checkpoint_only(sequence: int, data: bytes) -> None:
            if sequence == 1 and manager_ref:
                mgr = manager_ref[0]
                QTimer.singleShot(
                    5,
                    lambda: mgr.deliver(
                        SEQ_CHECKPOINT_ACK, struct.pack("<i", 1)
                    ),
                )

        service, manager = make_service(
            tmp_path, window=2, on_send=ack_first_checkpoint_only
        )
        manager_ref.append(manager)
        service._quiescent_ms = 2
        service._tx_ack_timeout_ms = 20

        source = tmp_path / "blocked.bin"
        source.write_bytes(b"B" * (PACKET_SIZE * 6))

        paused_args: list[tuple[str, int, int, float, str]] = []
        service.transfer_paused.connect(
            lambda name, acked, total, pct, reason: paused_args.append(
                (name, acked, total, pct, reason)
            )
        )

        with qtbot.waitSignal(service.transfer_paused, timeout=3000):
            service.start_transfer(str(source))

        assert service.state == TransferState.PAUSED
        assert service.is_active is True
        assert len(paused_args) == 1
        name, acked, total, pct, reason = paused_args[0]
        assert name == "blocked.bin"
        assert acked == 1
        assert total == 6
        assert "Optical LOS Blocked" in reason

        probes = [seq for seq, _ in manager.sent if seq == SEQ_CHECKPOINT_PROBE]
        assert len(probes) == CHECKPOINT_MAX_PROBE_RETRIES

    def test_resume_after_pause_succeeds(
        self, qtbot: QtBot, tmp_path: Path
    ) -> None:
        manager_ref: list[RecordingSerialManager] = []
        beam_restored = False

        def ack_simulation(seq: int, payload: bytes) -> None:
            if not manager_ref:
                return
            mgr = manager_ref[0]
            if seq == 1:
                QTimer.singleShot(
                    5,
                    lambda: mgr.deliver(
                        SEQ_CHECKPOINT_ACK, struct.pack("<i", 1)
                    ),
                )
            elif seq == 3 and beam_restored:
                QTimer.singleShot(
                    5,
                    lambda: mgr.deliver(
                        SEQ_CHECKPOINT_ACK, struct.pack("<i", 3)
                    ),
                )

        service, manager = make_service(
            tmp_path, window=2, on_send=ack_simulation
        )
        manager_ref.append(manager)
        service._quiescent_ms = 2
        service._tx_ack_timeout_ms = 20

        source = tmp_path / "resume_test.bin"
        source.write_bytes(b"C" * (PACKET_SIZE * 6))

        with qtbot.waitSignal(service.transfer_paused, timeout=3000):
            service.start_transfer(str(source))

        paused_state = service.state
        assert paused_state == TransferState.PAUSED
        beam_restored = True

        resumed_args: list[tuple[str, int]] = []
        service.transfer_resumed.connect(
            lambda name, seq: resumed_args.append((name, seq))
        )

        with qtbot.waitSignal(service.transfer_completed, timeout=4000):
            ok = service.resume_transfer()
            assert ok is True

        assert len(resumed_args) == 1
        assert resumed_args[0] == ("resume_test.bin", 2)
        final_state = service.state
        assert final_state == TransferState.COMPLETED
        sent_sequences = [seq for seq, _ in manager.sent]
        assert SEQ_RESUME_REQUEST in sent_sequences
        assert SEQ_END in sent_sequences

    def test_resume_from_zero_when_no_ack_received(
        self, qtbot: QtBot, tmp_path: Path
    ) -> None:
        """Kiểm tra Issue 3: Sentinel _tx_last_acked = -1 khôi phục từ gói 0 khi chưa có ACK."""
        service, manager = make_service(tmp_path, window=4)
        service._quiescent_ms = 2

        source = tmp_path / "no_ack.bin"
        source.write_bytes(b"Z" * (PACKET_SIZE * 4))

        service.start_transfer(str(source))
        service.pause_transfer("Manual pause before ACK")
        assert service.state == TransferState.PAUSED
        assert service._tx_last_acked == -1

        resumed_args: list[tuple[str, int]] = []
        service.transfer_resumed.connect(
            lambda name, seq: resumed_args.append((name, seq))
        )

        service.resume_transfer()
        assert len(resumed_args) == 1
        assert resumed_args[0] == ("no_ack.bin", 0)
        assert service._tx_next_index == 0
        service.cancel_transfer()

    def test_abort_while_paused(self, qtbot: QtBot, tmp_path: Path) -> None:
        service, manager = make_service(tmp_path, window=4)
        service._quiescent_ms = 2

        source = tmp_path / "abort_test.bin"
        source.write_bytes(b"D" * (PACKET_SIZE * 6))

        service.start_transfer(str(source))
        service.pause_transfer("Manual pause for abort")
        assert service.state.name == "PAUSED"

        with qtbot.waitSignal(service.state_changed, timeout=1000):
            service.abort_transfer()

        assert service.state.name == "ABORTED"
        assert service.is_active is False
        assert service.resume_transfer() is False

    def test_rx_responds_to_checkpoint_and_probe(
        self, qtbot: QtBot, tmp_path: Path
    ) -> None:
        service, manager = make_service(tmp_path, window=2)
        service._quiescent_ms = 2

        manager.deliver(SEQ_HEADER, pack_header_payload("rx_check.bin", 4))
        manager.deliver(0, b"0" * PACKET_SIZE)
        manager.deliver(1, b"1" * PACKET_SIZE)

        qtbot.waitUntil(
            lambda: any(seq == SEQ_CHECKPOINT_ACK for seq, _ in manager.sent),
            timeout=1000,
        )

        ack_frames = [
            (seq, data) for seq, data in manager.sent if seq == SEQ_CHECKPOINT_ACK
        ]
        assert len(ack_frames) >= 1
        last_acked_seq = struct.unpack("<i", ack_frames[0][1])[0]
        assert last_acked_seq == 1

        manager.deliver(SEQ_CHECKPOINT_PROBE, struct.pack("<i", 0))
        qtbot.waitUntil(
            lambda: len(
                [seq for seq, _ in manager.sent if seq == SEQ_CHECKPOINT_ACK]
            )
            >= 2,
            timeout=1000,
        )

    def test_rx_watchdog_timeout_pauses_transfer(
        self, qtbot: QtBot, tmp_path: Path
    ) -> None:
        """Kiểm tra Issue 2: Watchdog RX hết hạn chuyển sang PAUSED, giữ nguyên file tạm .part."""
        service, manager = make_service(tmp_path, window=2)
        paused_signals: list[tuple[str, int, int, float, str]] = []
        service.transfer_paused.connect(
            lambda name, acked, total, pct, reason: paused_signals.append(
                (name, acked, total, pct, reason)
            )
        )

        manager.deliver(SEQ_HEADER, pack_header_payload("rx_watchdog.bin", 4))
        manager.deliver(0, b"0" * PACKET_SIZE)

        assert service.state.name == "RECEIVING"
        service._on_rx_timeout()

        assert service.state.name == "PAUSED"
        assert len(paused_signals) == 1
        assert paused_signals[0][0] == "rx_watchdog.bin"
        assert service._rx_part_path is not None
        assert service._rx_part_path.exists()

    def test_rx_resume_request_revives_and_sends_contiguous_ack(
        self, qtbot: QtBot, tmp_path: Path
    ) -> None:
        """Kiểm tra Issue 2 & 3: SEQ_RESUME_REQUEST hồi sinh phiên nhận RX và gửi lại ACK liên tục."""
        service, manager = make_service(tmp_path, window=2)
        service._quiescent_ms = 2

        manager.deliver(SEQ_HEADER, pack_header_payload("rx_resume.bin", 4))
        manager.deliver(0, b"0" * PACKET_SIZE)
        service._pause_rx("Test pause")
        assert service.state.name == "PAUSED"

        resumed_signals: list[tuple[str, int]] = []
        service.transfer_resumed.connect(
            lambda name, seq: resumed_signals.append((name, seq))
        )

        resume_pkt = WinFormPacket(
            sequence=SEQ_RESUME_REQUEST,
            length=4,
            data=struct.pack("<I", 1),
        )
        service._handle_resume_request(resume_pkt)

        assert service.state.name == "RECEIVING"
        assert len(resumed_signals) == 1
        assert resumed_signals[0] == ("rx_resume.bin", 0)

        qtbot.waitUntil(
            lambda: any(seq == SEQ_CHECKPOINT_ACK for seq, _ in manager.sent),
            timeout=1000,
        )
        ack_data = [data for seq, data in manager.sent if seq == SEQ_CHECKPOINT_ACK][-1]
        acked_seq = struct.unpack("<i", ack_data)[0]
        assert acked_seq == 0

    def test_rx_recovery_timeout_fails_transfer(
        self, qtbot: QtBot, tmp_path: Path
    ) -> None:
        """Kiểm tra Issue 2: Timer phục hồi dài hạn hết hạn sẽ thực sự hủy phiên nhận."""
        service, manager = make_service(tmp_path, window=2)
        failed_signals: list[str] = []
        service.transfer_failed.connect(lambda r: failed_signals.append(r))

        manager.deliver(SEQ_HEADER, pack_header_payload("rx_timeout.bin", 4))
        manager.deliver(0, b"0" * PACKET_SIZE)
        service._pause_rx("Test pause")

        service._on_rx_recovery_timeout()
        assert service.state == TransferState.FAILED
        assert len(failed_signals) == 1
        assert "recovery timeout" in failed_signals[0]

    def test_highest_contiguous_rx_seq_calculation(
        self, tmp_path: Path
    ) -> None:
        """Kiểm tra Issue 3: Tính chuỗi gói tin liên tục lớn nhất (Contiguous Sequence)."""
        service, _ = make_service(tmp_path, window=2)
        service._rx_received = set()
        assert service._highest_contiguous_rx_seq() == -1

        service._rx_received = {1, 2, 3}
        assert service._highest_contiguous_rx_seq() == -1

        service._rx_received = {0}
        assert service._highest_contiguous_rx_seq() == 0

        service._rx_received = {0, 1, 2, 3}
        assert service._highest_contiguous_rx_seq() == 3

        service._rx_received = {0, 1, 2, 3, 5}
        assert service._highest_contiguous_rx_seq() == 3


class TestTwoNodeEndToEndOpticalLink:
    """Kiểm tra liên kết hai node quang học thu phát qua lại có checkpointing và mất tia."""

    def test_e2e_checkpoint_transmission_success(
        self, qtbot: QtBot, tmp_path: Path
    ) -> None:
        tx_storage = StorageService(data_dir=tmp_path / "tx_data")
        rx_storage = StorageService(data_dir=tmp_path / "rx_data")

        tx_manager = TwoWayLoopbackManager()
        rx_manager = TwoWayLoopbackManager(peer=tx_manager)
        tx_manager.peer = rx_manager

        tx_service = FileTransferService(
            cast(SerialManager, tx_manager),
            storage_service=tx_storage,
            transfers_dir=tmp_path / "tx_transfers",
        )
        rx_service = FileTransferService(
            cast(SerialManager, rx_manager),
            storage_service=rx_storage,
            transfers_dir=tmp_path / "rx_transfers",
        )

        tx_service.checkpoint_window = 4
        rx_service.checkpoint_window = 4
        tx_service._quiescent_ms = 2
        rx_service._quiescent_ms = 2
        tx_service._tx_ack_timeout_ms = 200

        source = tmp_path / "e2e_file.bin"
        content = b"PHOTON_DATA_" * 120
        source.write_bytes(content)

        with qtbot.waitSignal(rx_service.transfer_completed, timeout=6000):
            tx_service.start_transfer(str(source))

        assert tx_service.state == TransferState.COMPLETED
        assert rx_service.state == TransferState.COMPLETED
        received_path = tmp_path / "rx_transfers" / "e2e_file.bin"
        assert received_path.read_bytes() == content

    def test_e2e_los_block_pause_and_resume(
        self, qtbot: QtBot, tmp_path: Path
    ) -> None:
        tx_storage = StorageService(data_dir=tmp_path / "tx_data")
        rx_storage = StorageService(data_dir=tmp_path / "rx_data")

        tx_manager = TwoWayLoopbackManager()
        rx_manager = TwoWayLoopbackManager(peer=tx_manager)
        tx_manager.peer = rx_manager

        tx_service = FileTransferService(
            cast(SerialManager, tx_manager),
            storage_service=tx_storage,
            transfers_dir=tmp_path / "tx_transfers",
        )
        rx_service = FileTransferService(
            cast(SerialManager, rx_manager),
            storage_service=rx_storage,
            transfers_dir=tmp_path / "rx_transfers",
        )

        tx_service.checkpoint_window = 2
        rx_service.checkpoint_window = 2
        tx_service._quiescent_ms = 2
        rx_service._quiescent_ms = 2
        tx_service._tx_ack_timeout_ms = 40

        source = tmp_path / "los_file.bin"
        content = b"BEAM_DATA_CHUNK_" * 100
        source.write_bytes(content)

        def drop_after_first_ack(seq: int, payload: bytes) -> None:
            if seq == 1:
                QTimer.singleShot(
                    50,
                    lambda: setattr(
                        rx_manager,
                        "drop_sequences",
                        {SEQ_CHECKPOINT_ACK, SEQ_CHECKPOINT_PROBE},
                    ),
                )

        tx_manager.on_send = drop_after_first_ack

        with qtbot.waitSignal(tx_service.transfer_paused, timeout=4000):
            tx_service.start_transfer(str(source))

        paused_state = tx_service.state
        assert paused_state == TransferState.PAUSED
        assert tx_service._tx_last_acked == 1

        rx_manager.drop_sequences.clear()

        with qtbot.waitSignal(rx_service.transfer_completed, timeout=6000):
            tx_service.resume_transfer()

        resumed_tx_state = tx_service.state
        assert resumed_tx_state == TransferState.COMPLETED
        assert rx_service.state == TransferState.COMPLETED
        received_path = tmp_path / "rx_transfers" / "los_file.bin"
        assert received_path.read_bytes() == content


class TestBackendBridgeCheckpointIntegration:
    """Kiểm tra cầu nối BackendBridge kết nối các tín hiệu transferPaused và transferResumed."""

    def test_bridge_wires_pause_and_resume(
        self, qtbot: QtBot, tmp_path: Path
    ) -> None:
        manager = SerialManager()
        storage = StorageService(data_dir=tmp_path / "data")
        service = FileTransferService(
            manager,
            storage_service=storage,
            transfers_dir=tmp_path / "transfers",
        )
        service.checkpoint_window = 4
        bridge = BackendBridge(
            serial_manager=manager,
            file_service=service,
            storage_service=storage,
        )

        paused_signals: list[tuple[str, int, int, float, str]] = []
        resumed_signals: list[tuple[str, int]] = []

        bridge.transferPaused.connect(
            lambda name, acked, total, pct, reason: paused_signals.append(
                (name, acked, total, pct, reason)
            )
        )
        bridge.transferResumed.connect(
            lambda name, seq: resumed_signals.append((name, seq))
        )

        service.transfer_paused.emit("sample.bin", 4, 16, 25.0, "LOS Blocked")
        assert len(paused_signals) == 1
        assert paused_signals[0] == ("sample.bin", 4, 16, 25.0, "LOS Blocked")

        service.transfer_resumed.emit("sample.bin", 5)
        assert len(resumed_signals) == 1
        assert resumed_signals[0] == ("sample.bin", 5)

        service._state = TransferState.PAUSED
        source = tmp_path / "resume.bin"
        source.write_bytes(b"DATA" * 100)
        service._tx_file = source.open("rb")
        service._tx_name = "resume.bin"
        service._tx_total_packets = 1

        bridge.resumeFileTransfer()
        assert service.state == TransferState.SENDING
        service.cancel_transfer()
        manager.shutdown()

    def test_bridge_checkpoint_ack_option_slot(
        self, tmp_path: Path
    ) -> None:
        """Kiểm tra slot cấu hình setCheckpointAckOption trên BackendBridge."""
        manager = SerialManager()
        storage = StorageService(data_dir=tmp_path / "data")
        service = FileTransferService(
            manager,
            storage_service=storage,
            transfers_dir=tmp_path / "transfers",
        )
        bridge = BackendBridge(
            serial_manager=manager,
            file_service=service,
            storage_service=storage,
        )

        res = bridge.setCheckpointAckOption(False)
        assert bool(service.checkpoint_ack_enabled) is False
        assert '"checkpoint_ack_enabled": false' in res.lower()

        res2 = bridge.setCheckpointAckOption(True)
        assert bool(service.checkpoint_ack_enabled) is True
        assert '"checkpoint_ack_enabled": true' in res2.lower()
        manager.shutdown()
