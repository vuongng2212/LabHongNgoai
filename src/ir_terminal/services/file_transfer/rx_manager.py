"""Transfer protocol handlers split from the public Qt service façade."""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

from ir_terminal.protocol.constants import (
    MAX_CHUNK_DATA_SIZE,
    MAX_EXTENDED_CHUNK_COUNT,
    MAX_EXTENDED_CHUNK_DATA_SIZE,
    MAX_PAYLOAD_SIZE,
    MAX_V1_CHUNK_COUNT,
    AckStatus,
    Opcode,
)
from ir_terminal.protocol.file_payloads import (
    AckPayload,
    FileStartPayload,
    FileStartPayloadLarge,
)
from ir_terminal.protocol.packet import Packet
from ir_terminal.utils.logger import (
    LogTag,
    ack_status_name,
    format_frame_summary,
    format_hex_preview,
)

from .chunk_buffer import ChunkBuffer
from .file_transfer_types import TransferState
from .integrity_verifier import IntegrityVerifier
from .protocol_adapter import ProtocolAdapter

if TYPE_CHECKING:
    from ir_terminal.services.file_transfer_service import FileTransferService

logger = logging.getLogger(__name__)
CANCEL_REASON: bytes = b"ABORT"
CRC_STREAM_BUFFER_SIZE: int = 1024 * 1024


class RxManager:
    def __init__(self, service: FileTransferService) -> None:
        self._service = service

    def _close_rx_file(self, remove_temp: bool) -> None:
        """Closes the RX .part handle and optionally deletes the partial file."""
        service: FileTransferService = self._service
        handle = service._rx_file_handle
        service._rx_file_handle = None
        if handle is not None:
            try:
                handle.flush()
                handle.close()
            except OSError as exc:
                logger.warning(
                    "%s Could not close RX part file (%s: %s)",
                    LogTag.FILE_SVC,
                    type(exc).__name__,
                    exc,
                )
        if remove_temp and service._rx_temp_path is not None:
            try:
                service._rx_temp_path.unlink(missing_ok=True)
            except OSError as exc:
                logger.warning(
                    "%s Could not remove RX part file %s (%s: %s)",
                    LogTag.FILE_SVC,
                    service._rx_temp_path,
                    type(exc).__name__,
                    exc,
                )
        if remove_temp:
            service._rx_temp_path = None

    def _reset_rx_session(self, remove_temp: bool = True, keep_end_ack: bool = False) -> None:
        """Releases RX resources and resets per-session counters.

        Args:
            remove_temp: Delete the in-progress ``.part`` file when True.
            keep_end_ack: Preserve the last END seq/status so a retransmitted
                END can be re-ACKed without writing the file twice.
        """
        service: FileTransferService = self._service
        service._close_rx_file(remove_temp=remove_temp)
        service._rx_filename = ""
        service._rx_file_size = 0
        service._rx_chunk_size = 0
        service._rx_total_chunks = 0
        service._rx_file_crc32 = 0
        service._rx_expected_chunk = 0
        service._rx_crc32 = 0
        service._rx_bytes_received = 0
        service._rx_received_count = 0
        service._rx_extended = False
        service._rx_dest_dir = None
        if not keep_end_ack:
            service._rx_start_seq = -1
            service._rx_end_seq = -1
            service._rx_end_status = AckStatus.OK

    def _fail_rx(
        self,
        reason: str,
        status: int = AckStatus.OUT_OF_ORDER,
        end_seq: int | None = None,
    ) -> None:
        """Rejects/fails an RX session, sends NACK, removes temp data and emits."""
        service: FileTransferService = self._service
        if end_seq is not None:
            service._rx_end_seq = end_seq
            service._rx_end_status = status
        service._set_state(TransferState.FAILED)
        service._persist_failure(reason)
        service._reset_rx_session(remove_temp=True, keep_end_ack=end_seq is not None)
        service.transfer_failed.emit(reason)

    def _on_transfer_pause_received(self, pkt: Packet) -> None:
        """Handles a remote ABORT/PAUSE cancel for either half of the session."""
        service: FileTransferService = self._service
        reason = pkt.payload.decode("utf-8", errors="replace") if pkt.payload else "PAUSED"

        if service._state == TransferState.RECEIVING:
            rx_file = service._rx_filename
            rx_had = (
                service._rx_received_count,
                service._rx_total_chunks,
                service._rx_bytes_received,
            )
            ack = AckPayload(acked_seq=pkt.seq, status=AckStatus.OK)
            service._serial_manager.send_packet(seq=pkt.seq, opcode=Opcode.ACK, payload=ack.pack())
            service._set_state(TransferState.ABORTED)
            service._persist_failure(f"Remote aborted transfer: {reason}")
            service._reset_rx_session(remove_temp=True)
            service.transfer_failed.emit(f"Remote aborted transfer: {reason}")
            logger.info(
                "%s RX remote-abort %s reason=%r file='%s' had=%d/%d chunks (%dB)",
                LogTag.FILE_SVC,
                format_frame_summary(pkt.seq, pkt.opcode, len(pkt.payload)),
                reason,
                rx_file,
                rx_had[0],
                rx_had[1],
                rx_had[2],
            )
            return

        if service._state in (
            TransferState.HANDSHAKING,
            TransferState.TRANSFERRING,
            TransferState.FINALIZING,
        ):
            fname = service._current_file_path.name if service._current_file_path else "?"
            sent_bytes = service._bytes_transferred
            service._ack_timer.stop()
            service._close_tx_file()
            ack = AckPayload(acked_seq=pkt.seq, status=AckStatus.OK)
            service._serial_manager.send_packet(seq=pkt.seq, opcode=Opcode.ACK, payload=ack.pack())
            service._set_state(TransferState.ABORTED)
            service._persist_failure(f"Remote cancelled transfer: {reason}")
            service._reset_tx_session()
            service.transfer_failed.emit(f"Remote cancelled transfer: {reason}")
            logger.info(
                "%s TX remote-abort %s reason=%r file='%s' sent=%dB",
                LogTag.FILE_SVC,
                format_frame_summary(pkt.seq, pkt.opcode, len(pkt.payload)),
                reason,
                fname,
                sent_bytes,
            )
            return

        logger.debug(
            "%s Ignored TRANSFER_PAUSE in %s: %s",
            LogTag.FILE_SVC,
            service._state.name,
            format_frame_summary(pkt.seq, pkt.opcode, len(pkt.payload)),
        )

    def _is_duplicate_start(
        self,
        pkt: Packet,
        start_payload: FileStartPayload | FileStartPayloadLarge,
        extended: bool,
    ) -> bool:
        """Returns True when a START retransmits the currently accepted RX session."""
        service: FileTransferService = self._service
        return (
            service._state == TransferState.RECEIVING
            and extended == service._rx_extended
            and pkt.seq == service._rx_start_seq
            and start_payload.file_size == service._rx_file_size
            and start_payload.chunk_size == service._rx_chunk_size
            and start_payload.total_chunks == service._rx_total_chunks
            and start_payload.file_crc32 == service._rx_file_crc32
            and start_payload.filename == service._rx_filename
        )

    def _on_transfer_start_received(self, pkt: Packet) -> None:
        """Handles an incoming TRANSFER_START frame by preparing the RX stream and ACKing."""
        service: FileTransferService = self._service
        extended = pkt.opcode == Opcode.TRANSFER_START_LARGE
        try:
            start_payload = ProtocolAdapter.decode_start(pkt)
        except Exception as e:
            logger.exception(
                "%s RX START parse FAILED %s (%s: %s) hex=%s",
                LogTag.FILE_SVC,
                format_frame_summary(pkt.seq, pkt.opcode, len(pkt.payload), pkt.crc32),
                type(e).__name__,
                e,
                format_hex_preview(pkt.payload, max_bytes=24),
            )
            ack = AckPayload(acked_seq=pkt.seq, status=AckStatus.CRC_ERROR)
            service._serial_manager.send_packet(seq=pkt.seq, opcode=Opcode.NACK, payload=ack.pack())
            return

        # A retransmitted START means our previous ACK was lost. Re-ACK the
        # already-active session instead of NACKing it as "busy"; otherwise the
        # sender burns all 5 retries and fails even though the link is healthy.
        if service._is_duplicate_start(pkt, start_payload, extended):
            ack = AckPayload(acked_seq=pkt.seq, status=AckStatus.OK)
            service._serial_manager.send_packet(seq=pkt.seq, opcode=Opcode.ACK, payload=ack.pack())
            logger.info(
                "%s RX START duplicate re-ACK %s file='%s' size=%dB",
                LogTag.ARQ_ENGINE,
                format_frame_summary(pkt.seq, pkt.opcode, len(pkt.payload)),
                service._rx_filename,
                service._rx_file_size,
            )
            return

        # Half-duplex guard: never clobber an ongoing outgoing session. NACK tells
        # the sender to back off and retry after its timeout.
        if service._state in (
            TransferState.HANDSHAKING,
            TransferState.TRANSFERRING,
            TransferState.FINALIZING,
            TransferState.RECEIVING,
        ):
            logger.warning(
                "%s RX START rejected: busy in %s | %s file='%s' size=%dB",
                LogTag.ARQ_ENGINE,
                service._state.name,
                format_frame_summary(pkt.seq, pkt.opcode, len(pkt.payload)),
                start_payload.filename,
                start_payload.file_size,
            )
            ack = AckPayload(acked_seq=pkt.seq, status=AckStatus.OUT_OF_ORDER)
            service._serial_manager.send_packet(seq=pkt.seq, opcode=Opcode.NACK, payload=ack.pack())
            return

        if service._max_file_size_bytes and start_payload.file_size > service._max_file_size_bytes:
            err_msg = (
                f"Incoming file exceeds configured limit {service._max_file_size_bytes}B: "
                f"{start_payload.filename} ({start_payload.file_size} bytes)"
            )
            logger.error(
                "%s RX START rejected: %s | %s",
                LogTag.FILE_SVC,
                err_msg,
                format_frame_summary(pkt.seq, pkt.opcode, len(pkt.payload)),
            )
            ack = AckPayload(acked_seq=pkt.seq, status=AckStatus.OUT_OF_ORDER)
            service._serial_manager.send_packet(seq=pkt.seq, opcode=Opcode.NACK, payload=ack.pack())
            service._set_state(TransferState.FAILED)
            service._persist_failure(err_msg)
            service.transfer_failed.emit(err_msg)
            return

        max_chunk_data = MAX_EXTENDED_CHUNK_DATA_SIZE if extended else MAX_CHUNK_DATA_SIZE
        if start_payload.chunk_size == 0 or start_payload.chunk_size > max_chunk_data:
            err_msg = (
                f"Incoming transfer has invalid chunk size: {start_payload.chunk_size} "
                f"(max {max_chunk_data}B data inside {MAX_PAYLOAD_SIZE}B payload)"
            )
            logger.error(
                "%s RX START rejected: %s | %s payload_hex=%s",
                LogTag.FILE_SVC,
                err_msg,
                format_frame_summary(pkt.seq, pkt.opcode, len(pkt.payload)),
                format_hex_preview(pkt.payload, max_bytes=24),
            )
            ack = AckPayload(acked_seq=pkt.seq, status=AckStatus.CRC_ERROR)
            service._serial_manager.send_packet(seq=pkt.seq, opcode=Opcode.NACK, payload=ack.pack())
            service._set_state(TransferState.FAILED)
            service._persist_failure(err_msg)
            service.transfer_failed.emit(err_msg)
            return

        max_chunks = MAX_EXTENDED_CHUNK_COUNT if extended else MAX_V1_CHUNK_COUNT
        expected_chunks = (
            (start_payload.file_size + start_payload.chunk_size - 1) // start_payload.chunk_size
            if start_payload.file_size
            else 1
        )
        if (
            start_payload.total_chunks == 0
            or start_payload.total_chunks > max_chunks
            or start_payload.total_chunks != expected_chunks
        ):
            err_msg = (
                f"Incoming transfer has invalid total_chunks: {start_payload.total_chunks} "
                f"(expected {expected_chunks}, max {max_chunks})"
            )
            logger.error(
                "%s RX START rejected: %s | %s",
                LogTag.FILE_SVC,
                err_msg,
                format_frame_summary(pkt.seq, pkt.opcode, len(pkt.payload)),
            )
            ack = AckPayload(acked_seq=pkt.seq, status=AckStatus.CRC_ERROR)
            service._serial_manager.send_packet(seq=pkt.seq, opcode=Opcode.NACK, payload=ack.pack())
            service._set_state(TransferState.FAILED)
            service._persist_failure(err_msg)
            service.transfer_failed.emit(err_msg)
            return

        # Open the streaming destination: chunks are written straight to disk,
        # never accumulated in a dict, so memory stays O(chunk_size). The target
        # directory is locked for the whole session so a mid-transfer settings
        # change cannot send the final os.replace across filesystems (EXDEV).
        dest_dir = service._transfers_dir
        try:
            rx_handle, temp_path = ChunkBuffer.create_staging_file(dest_dir)
        except OSError as exc:
            err_msg = f"Cannot create receive file: {exc}"
            logger.exception("%s RX START FAILED: %s", LogTag.FILE_SVC, err_msg)
            ack = AckPayload(acked_seq=pkt.seq, status=AckStatus.OUT_OF_ORDER)
            service._serial_manager.send_packet(seq=pkt.seq, opcode=Opcode.NACK, payload=ack.pack())
            service._set_state(TransferState.FAILED)
            service._persist_failure(err_msg)
            service.transfer_failed.emit(err_msg)
            return

        service._rx_filename = start_payload.filename
        service._rx_file_size = start_payload.file_size
        service._rx_chunk_size = start_payload.chunk_size
        service._rx_total_chunks = start_payload.total_chunks
        service._rx_file_crc32 = start_payload.file_crc32
        service._rx_file_handle = rx_handle
        service._rx_temp_path = temp_path
        service._rx_dest_dir = dest_dir
        service._rx_expected_chunk = 0
        service._rx_crc32 = 0
        service._rx_start_time = time.time()
        service._rx_bytes_received = 0
        service._rx_received_count = 0
        service._rx_extended = extended
        service._rx_start_seq = pkt.seq
        service._rx_end_seq = -1
        service._rx_end_status = AckStatus.OK

        service._set_state(TransferState.RECEIVING)
        logger.info(
            "%s RX START accepted %s file='%s' size=%dB chunks=%d chunk_size=%dB "
            "file_crc=0x%08X extended=%s part=%s",
            LogTag.FILE_SVC,
            format_frame_summary(pkt.seq, pkt.opcode, len(pkt.payload), pkt.crc32),
            service._rx_filename,
            service._rx_file_size,
            service._rx_total_chunks,
            service._rx_chunk_size,
            service._rx_file_crc32,
            extended,
            service._rx_temp_path,
        )

        ack = AckPayload(acked_seq=pkt.seq, status=AckStatus.OK)
        service._serial_manager.send_packet(seq=pkt.seq, opcode=Opcode.ACK, payload=ack.pack())
        service.progress_updated.emit(0, 0.0, 0.0, 0, max(service._rx_total_chunks, 1))

    def _on_data_chunk_received(self, pkt: Packet) -> None:
        """Handles an incoming DATA_CHUNK by appending it to the streaming RX file."""
        service: FileTransferService = self._service
        if service._state != TransferState.RECEIVING:
            logger.warning(
                "%s RX CHUNK out-of-state: state=%s | %s",
                LogTag.ARQ_ENGINE,
                service._state.name,
                format_frame_summary(pkt.seq, pkt.opcode, len(pkt.payload)),
            )
            return

        extended = pkt.opcode == Opcode.DATA_CHUNK_LARGE
        if extended != service._rx_extended:
            logger.warning(
                "%s RX CHUNK opcode/format mismatch: extended=%s session_extended=%s | %s",
                LogTag.ARQ_ENGINE,
                extended,
                service._rx_extended,
                format_frame_summary(pkt.seq, pkt.opcode, len(pkt.payload)),
            )
            ack = AckPayload(acked_seq=pkt.seq, status=AckStatus.OUT_OF_ORDER)
            service._serial_manager.send_packet(seq=pkt.seq, opcode=Opcode.NACK, payload=ack.pack())
            return

        try:
            chunk_payload = ProtocolAdapter.decode_chunk(pkt)
        except Exception as e:
            logger.exception(
                "%s RX CHUNK parse FAILED %s (%s: %s) hex=%s",
                LogTag.ARQ_ENGINE,
                format_frame_summary(pkt.seq, pkt.opcode, len(pkt.payload)),
                type(e).__name__,
                e,
                format_hex_preview(pkt.payload, max_bytes=16),
            )
            ack = AckPayload(acked_seq=pkt.seq, status=AckStatus.CRC_ERROR)
            service._serial_manager.send_packet(seq=pkt.seq, opcode=Opcode.NACK, payload=ack.pack())
            return

        chunk_id = chunk_payload.chunk_id
        chunk_data = chunk_payload.chunk_data
        if chunk_id >= service._rx_total_chunks:
            logger.error(
                "%s RX CHUNK id=%d out of range (total=%d) | %s data_len=%dB",
                LogTag.ARQ_ENGINE,
                chunk_id,
                service._rx_total_chunks,
                format_frame_summary(pkt.seq, pkt.opcode, len(pkt.payload)),
                len(chunk_data),
            )
            ack = AckPayload(acked_seq=pkt.seq, status=AckStatus.OUT_OF_ORDER)
            service._serial_manager.send_packet(seq=pkt.seq, opcode=Opcode.NACK, payload=ack.pack())
            return

        if chunk_id < service._rx_expected_chunk:
            # Duplicate retransmission after our ACK was lost: re-ACK, do not
            # append the same bytes twice.
            ack = AckPayload(acked_seq=pkt.seq, status=AckStatus.OK)
            service._serial_manager.send_packet(seq=pkt.seq, opcode=Opcode.ACK, payload=ack.pack())
            logger.debug(
                "%s RX CHUNK duplicate id=%d (< expected %d) re-ACK | %s",
                LogTag.ARQ_ENGINE,
                chunk_id,
                service._rx_expected_chunk,
                format_frame_summary(pkt.seq, pkt.opcode, len(pkt.payload)),
            )
            return

        if chunk_id > service._rx_expected_chunk:
            logger.warning(
                "%s RX CHUNK out-of-order id=%d expected=%d | %s",
                LogTag.ARQ_ENGINE,
                chunk_id,
                service._rx_expected_chunk,
                format_frame_summary(pkt.seq, pkt.opcode, len(pkt.payload)),
            )
            ack = AckPayload(acked_seq=pkt.seq, status=AckStatus.OUT_OF_ORDER)
            service._serial_manager.send_packet(seq=pkt.seq, opcode=Opcode.NACK, payload=ack.pack())
            return

        start_offset = chunk_id * service._rx_chunk_size
        expected_len = min(service._rx_chunk_size, max(service._rx_file_size - start_offset, 0))
        if len(chunk_data) != expected_len:
            logger.error(
                "%s RX CHUNK length mismatch id=%d got=%dB expected=%dB | %s",
                LogTag.ARQ_ENGINE,
                chunk_id,
                len(chunk_data),
                expected_len,
                format_frame_summary(pkt.seq, pkt.opcode, len(pkt.payload)),
            )
            ack = AckPayload(acked_seq=pkt.seq, status=AckStatus.CRC_ERROR)
            service._serial_manager.send_packet(seq=pkt.seq, opcode=Opcode.NACK, payload=ack.pack())
            return

        if service._rx_file_handle is None:
            ack = AckPayload(acked_seq=pkt.seq, status=AckStatus.OUT_OF_ORDER)
            service._serial_manager.send_packet(seq=pkt.seq, opcode=Opcode.NACK, payload=ack.pack())
            service._fail_rx("Receive file handle is closed", status=AckStatus.OUT_OF_ORDER)
            return

        try:
            ChunkBuffer.append(service._rx_file_handle, chunk_data)
            # Flush per chunk so the .part file reflects durable progress and
            # a crash never leaves megabytes of unflushed buffered chunks.
        except OSError as exc:
            ack = AckPayload(acked_seq=pkt.seq, status=AckStatus.OUT_OF_ORDER)
            service._serial_manager.send_packet(seq=pkt.seq, opcode=Opcode.NACK, payload=ack.pack())
            service._fail_rx(
                f"Failed to write received chunk: {exc}", status=AckStatus.OUT_OF_ORDER
            )
            return

        service._rx_crc32 = IntegrityVerifier.update_crc32(service._rx_crc32, chunk_data)
        service._rx_bytes_received += len(chunk_data)
        service._rx_received_count += 1
        service._rx_expected_chunk += 1

        # Respond ACK
        ack = AckPayload(acked_seq=pkt.seq, status=AckStatus.OK)
        service._serial_manager.send_packet(seq=pkt.seq, opcode=Opcode.ACK, payload=ack.pack())

        # Progress calculation
        now = time.time()
        elapsed = max(now - service._rx_start_time, 0.001)
        speed_kbps = (service._rx_bytes_received / 1024.0) / elapsed
        received_count = service._rx_received_count
        total = max(service._rx_total_chunks, 1)
        pct = min(int((received_count / total) * 100), 100)
        rem_chunks = max(total - received_count, 0)
        eta_sec = rem_chunks * (elapsed / max(received_count, 1))

        service.progress_updated.emit(pct, speed_kbps, eta_sec, received_count, total)
        logger.debug(
            "%s RX CHUNK id=%d (%d/%d) %s data=%dB total_rx=%dB elapsed=%.2fs pct=%d%%",
            LogTag.ARQ_ENGINE,
            chunk_id,
            received_count,
            total,
            format_frame_summary(pkt.seq, pkt.opcode, len(pkt.payload)),
            len(chunk_data),
            service._rx_bytes_received,
            elapsed,
            pct,
        )

    def _on_transfer_end_received(self, pkt: Packet) -> None:
        """Handles TRANSFER_END: verify size + CRC-32, then atomically publish the file."""
        service: FileTransferService = self._service
        # If our final ACK was lost, the sender retransmits the identical END
        # with the same sequence number. Re-ACK the completed session (without
        # writing the file or emitting transfer_completed again), or re-NACK a
        # previously failed verify, so the sender does not exhaust its retries.
        if pkt.seq == service._rx_end_seq and service._state in (
            TransferState.COMPLETED,
            TransferState.FAILED,
        ):
            status = (
                AckStatus.OK
                if service._state == TransferState.COMPLETED
                else service._rx_end_status
            )
            opcode = Opcode.ACK if status == AckStatus.OK else Opcode.NACK
            ack = AckPayload(acked_seq=pkt.seq, status=status)
            service._serial_manager.send_packet(seq=pkt.seq, opcode=opcode, payload=ack.pack())
            logger.info(
                "%s RX END duplicate re-%s %s status=%s(%d)",
                LogTag.ARQ_ENGINE,
                "ACK" if opcode == Opcode.ACK else "NACK",
                format_frame_summary(pkt.seq, pkt.opcode, len(pkt.payload)),
                ack_status_name(status),
                status,
            )
            return

        if service._state != TransferState.RECEIVING:
            logger.debug(
                "%s Ignored TRANSFER_END in %s: %s",
                LogTag.FILE_SVC,
                service._state.name,
                format_frame_summary(pkt.seq, pkt.opcode, len(pkt.payload)),
            )
            return

        extended = pkt.opcode == Opcode.TRANSFER_END_LARGE
        if extended != service._rx_extended:
            service._fail_rx(
                "TRANSFER_END opcode/format mismatch",
                status=AckStatus.OUT_OF_ORDER,
                end_seq=pkt.seq,
            )
            return

        try:
            end_payload = ProtocolAdapter.decode_end(pkt)
        except Exception as e:
            logger.exception(
                "%s RX END parse FAILED %s (%s: %s) hex=%s",
                LogTag.FILE_SVC,
                format_frame_summary(pkt.seq, pkt.opcode, len(pkt.payload)),
                type(e).__name__,
                e,
                format_hex_preview(pkt.payload),
            )
            service._fail_rx(
                "Corrupted TRANSFER_END payload",
                status=AckStatus.CRC_ERROR,
                end_seq=pkt.seq,
            )
            return

        logger.info(
            "%s RX END verify %s claimed_crc=0x%08X claimed_bytes=%d file='%s' "
            "have=%d/%d chunks (%dB)",
            LogTag.FILE_SVC,
            format_frame_summary(pkt.seq, pkt.opcode, len(pkt.payload), pkt.crc32),
            end_payload.file_crc32,
            end_payload.total_bytes_sent,
            service._rx_filename,
            service._rx_received_count,
            service._rx_total_chunks,
            service._rx_bytes_received,
        )

        if service._rx_received_count != service._rx_total_chunks:
            missing = service._rx_expected_chunk
            logger.error(
                "%s RX END missing chunk %d/%d upon TRANSFER_END | %s",
                LogTag.ARQ_ENGINE,
                missing,
                service._rx_total_chunks,
                format_frame_summary(pkt.seq, pkt.opcode, len(pkt.payload)),
            )
            service._fail_rx(
                f"Missing chunk {missing} upon completion",
                status=AckStatus.OUT_OF_ORDER,
                end_seq=pkt.seq,
            )
            return

        if (
            service._rx_bytes_received != end_payload.total_bytes_sent
            or service._rx_bytes_received != service._rx_file_size
        ):
            logger.error(
                "%s RX END size mismatch: streamed=%dB end_total=%dB start_file_size=%dB | %s",
                LogTag.FILE_SVC,
                service._rx_bytes_received,
                end_payload.total_bytes_sent,
                service._rx_file_size,
                format_frame_summary(pkt.seq, pkt.opcode, len(pkt.payload)),
            )
            service._fail_rx(
                f"Size mismatch: streamed {service._rx_bytes_received}B, "
                f"END {end_payload.total_bytes_sent}B, START {service._rx_file_size}B",
                status=AckStatus.CRC_ERROR,
                end_seq=pkt.seq,
            )
            return

        # CRC-32 verification (computed incrementally during streaming writes)
        calculated_crc = service._rx_crc32 & 0xFFFFFFFF
        if not IntegrityVerifier.matches(
            (end_payload.file_crc32, service._rx_file_crc32), calculated_crc
        ):
            logger.error(
                "%s RX CRC MISMATCH: calc=0x%08X end_frame=0x%08X start_frame=0x%08X "
                "streamed=%dB expected=%dB | %s",
                LogTag.FILE_SVC,
                calculated_crc,
                end_payload.file_crc32,
                service._rx_file_crc32,
                service._rx_bytes_received,
                service._rx_file_size,
                format_frame_summary(pkt.seq, pkt.opcode, len(pkt.payload)),
            )
            service._fail_rx(
                f"Checksum mismatch: 0x{calculated_crc:08X} != 0x{end_payload.file_crc32:08X}",
                status=AckStatus.CRC_ERROR,
                end_seq=pkt.seq,
            )
            return

        temp_path = service._rx_temp_path
        rx_filename = service._rx_filename
        rx_size = service._rx_bytes_received
        rx_start_time = service._rx_start_time
        service._close_rx_file(remove_temp=False)
        if temp_path is None:
            service._fail_rx(
                "Receive temp file disappeared before publish",
                status=AckStatus.OUT_OF_ORDER,
                end_seq=pkt.seq,
            )
            return

        # Publish into the directory locked at TRANSFER_START, not the current
        # setting, so changing the setting mid-transfer cannot trigger EXDEV.
        dest_dir = service._rx_dest_dir or service._transfers_dir
        try:
            dest_path = ChunkBuffer.publish(temp_path, dest_dir, rx_filename)
        except OSError as exc:
            logger.exception(
                "%s RX publish FAILED: temp=%s dest=%s (%s: %s)",
                LogTag.FILE_SVC,
                temp_path,
                dest_path,
                type(exc).__name__,
                exc,
            )
            service._rx_temp_path = temp_path
            service._fail_rx(
                f"Could not save received file: {exc}",
                status=AckStatus.OUT_OF_ORDER,
                end_seq=pkt.seq,
            )
            return
        service._rx_temp_path = None

        # Send ACK
        ack = AckPayload(acked_seq=pkt.seq, status=AckStatus.OK)
        service._serial_manager.send_packet(seq=pkt.seq, opcode=Opcode.ACK, payload=ack.pack())

        elapsed = max(time.time() - rx_start_time, 0.001)
        speed_kbps = (rx_size / 1024.0) / elapsed
        crc_hex = f"0x{calculated_crc:08X}"

        # Persist to database
        rec_id = service._storage.save_file_transfer(
            filename=dest_path.name,
            file_path=str(dest_path),
            direction="RX",
            size_bytes=rx_size,
            crc32_hex=crc_hex,
            speed_kbps=speed_kbps,
            duration_sec=elapsed,
            status="SUCCESS",
        )

        service._rx_end_seq = pkt.seq
        service._rx_end_status = AckStatus.OK
        service._set_state(TransferState.COMPLETED)
        service.progress_updated.emit(
            100, speed_kbps, 0.0, service._rx_total_chunks, service._rx_total_chunks
        )
        service.transfer_completed.emit(dest_path.name, rx_size, elapsed, crc_hex)
        logger.info(
            "%s RX COMPLETE %s file='%s' path=%s size=%dB elapsed=%.2fs "
            "speed=%.2fKB/s crc=%s stored_id=%d",
            LogTag.FILE_SVC,
            format_frame_summary(pkt.seq, pkt.opcode, len(pkt.payload), pkt.crc32),
            dest_path.name,
            dest_path,
            rx_size,
            elapsed,
            speed_kbps,
            crc_hex,
            rec_id,
        )
        service._reset_rx_session(remove_temp=False, keep_end_ack=True)
