"""Transfer protocol handlers split from the public Qt service façade."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING

from ir_terminal.protocol.constants import (
    MAX_CHUNK_DATA_SIZE,
    MAX_EXTENDED_CHUNK_COUNT,
    MAX_EXTENDED_CHUNK_DATA_SIZE,
    MAX_PAYLOAD_SIZE,
    MAX_RETRY_COUNT,
    MAX_V1_CHUNK_COUNT,
    AckStatus,
    Opcode,
)
from ir_terminal.protocol.file_payloads import (
    AckPayload,
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
from .protocol_adapter import ProtocolAdapter

if TYPE_CHECKING:
    from ir_terminal.services.file_transfer_service import FileTransferService

logger = logging.getLogger(__name__)
CANCEL_REASON: bytes = b"ABORT"
CRC_STREAM_BUFFER_SIZE: int = 1024 * 1024


class TxManager:
    def __init__(self, service: FileTransferService) -> None:
        self._service = service

    def _close_tx_file(self) -> None:
        """Closes the streaming TX file handle without touching session metadata."""
        service: FileTransferService = self._service
        handle = service._tx_file
        service._tx_file = None
        if handle is not None:
            try:
                handle.close()
            except OSError as exc:
                logger.warning(
                    "%s Could not close TX file handle (%s: %s)",
                    LogTag.FILE_SVC,
                    type(exc).__name__,
                    exc,
                )

    def _reset_tx_session(self) -> None:
        """Releases TX resources and invalidates retransmit caches."""
        service: FileTransferService = self._service
        service._close_tx_file()
        service._tx_active = False
        service._current_file_path = None
        service._file_size = 0
        service._total_chunks = 0
        service._current_chunk_idx = 0
        service._bytes_transferred = 0
        service._retry_count = 0
        service._use_extended = False
        service._cached_start_payload = None
        service._cached_chunk_key = -1
        service._cached_chunk_payload = None
        service._cached_chunk_len = 0
        service._cached_end_key = -1
        service._cached_end_payload = None

    def _fail_tx(self, reason: str) -> None:
        """Stops an active TX session, persists a FAILED record and emits the error."""
        service: FileTransferService = self._service
        service._ack_timer.stop()
        service._close_tx_file()
        service._set_state(TransferState.FAILED)
        service._persist_failure(reason)
        service.transfer_failed.emit(reason)
        logger.error("%s TX FAILED: %s", LogTag.ARQ_ENGINE, reason)
        service._reset_tx_session()

    def start_transfer(self, file_path_str: str) -> None:
        """Starts transmitting a file over the optical link (streaming)."""
        service: FileTransferService = self._service
        if service._state not in (
            TransferState.IDLE,
            TransferState.COMPLETED,
            TransferState.FAILED,
            TransferState.ABORTED,
        ):
            logger.warning(
                "%s TX start rejected: busy in %s (file=%s chunk=%d/%d)",
                LogTag.FILE_SVC,
                service._state.name,
                file_path_str,
                service._current_chunk_idx,
                service._total_chunks,
            )
            return

        file_path = Path(file_path_str)
        if not file_path.exists() or not file_path.is_file():
            err_msg = f"File not found: {file_path_str}"
            logger.error(
                "%s TX start FAILED: %s (state=%s)",
                LogTag.FILE_SVC,
                err_msg,
                service._state.name,
            )
            service.transfer_failed.emit(err_msg)
            return

        file_size = file_path.stat().st_size
        if service._max_file_size_bytes and file_size > service._max_file_size_bytes:
            err_msg = (
                f"File exceeds configured limit {service._max_file_size_bytes}B: "
                f"{file_path.name} ({file_size} bytes)"
            )
            logger.error("%s TX start FAILED: %s", LogTag.FILE_SVC, err_msg)
            service.transfer_failed.emit(err_msg)
            return

        planned_chunks = (
            (file_size + service._chunk_size - 1) // service._chunk_size if file_size else 1
        )
        extended = planned_chunks > MAX_V1_CHUNK_COUNT or file_size > 0xFFFFFFFF
        max_chunk_data = MAX_EXTENDED_CHUNK_DATA_SIZE if extended else MAX_CHUNK_DATA_SIZE
        if not (1 <= service._chunk_size <= max_chunk_data):
            err_msg = (
                f"Invalid chunk size {service._chunk_size}B; protocol allows "
                f"1..{max_chunk_data}B (payload max {MAX_PAYLOAD_SIZE}B incl. chunk id)"
            )
            logger.error("%s TX start FAILED: %s", LogTag.FILE_SVC, err_msg)
            service.transfer_failed.emit(err_msg)
            return

        max_chunks = MAX_EXTENDED_CHUNK_COUNT if extended else MAX_V1_CHUNK_COUNT
        if planned_chunks > max_chunks:
            err_msg = (
                f"File requires {planned_chunks} chunks; protocol max is {max_chunks} "
                f"(file={file_size}B, chunk_size={service._chunk_size}B)."
            )
            logger.error("%s TX start FAILED: %s", LogTag.FILE_SVC, err_msg)
            service.transfer_failed.emit(err_msg)
            return

        try:
            file_crc32 = service._compute_file_crc(file_path)
            tx_file = file_path.open("rb")
        except OSError as exc:
            err_msg = f"Failed to read file: {exc}"
            logger.exception(
                "%s TX start FAILED: cannot read %s (%s: %s)",
                LogTag.FILE_SVC,
                file_path,
                type(exc).__name__,
                exc,
            )
            service.transfer_failed.emit(err_msg)
            return

        service._tx_active = True
        service._current_file_path = file_path
        service._tx_file = tx_file
        service._file_size = file_size
        service._file_crc32 = file_crc32
        service._total_chunks = planned_chunks
        service._current_chunk_idx = 0
        service._retry_count = 0
        service._bytes_transferred = 0
        service._use_extended = extended
        service._start_time = time.time()
        service._last_chunk_time = service._start_time
        service._cached_start_payload = None
        service._cached_chunk_key = -1
        service._cached_chunk_payload = None
        service._cached_chunk_len = 0
        service._cached_end_key = -1
        service._cached_end_payload = None

        service._set_state(TransferState.HANDSHAKING)

        service._cached_start_payload = ProtocolAdapter.start_payload(
            extended=extended,
            file_size=service._file_size,
            chunk_size=service._chunk_size,
            total_chunks=service._total_chunks,
            file_crc32=service._file_crc32,
            filename=file_path.name,
        )
        start_opcode = Opcode.TRANSFER_START_LARGE if extended else Opcode.TRANSFER_START

        service._seq = (service._seq + 1) % 256
        service._serial_manager.send_packet(
            seq=service._seq,
            opcode=start_opcode,
            payload=service._cached_start_payload,
        )
        service._ack_timer.start()
        logger.info(
            "%s TX START %s file='%s' size=%dB chunks=%d chunk_size=%dB "
            "file_crc=0x%08X payload_hex=%s timeout=250ms retry=0/5",
            LogTag.FILE_SVC,
            format_frame_summary(service._seq, start_opcode, len(service._cached_start_payload)),
            file_path.name,
            service._file_size,
            service._total_chunks,
            service._chunk_size,
            service._file_crc32,
            format_hex_preview(service._cached_start_payload, max_bytes=24),
        )

    def _send_current_chunk(self, new_seq: bool = True) -> None:
        """Transmits the current binary chunk read from disk.

        Args:
            new_seq: True to increment the sequence number for a new chunk;
                False to reuse the current sequence number for retransmission.
        """
        service: FileTransferService = self._service
        if (
            not new_seq
            and service._cached_chunk_payload is not None
            and service._cached_chunk_key == service._current_chunk_idx
        ):
            packed = service._cached_chunk_payload
            chunk_len = service._cached_chunk_len
        else:
            start_offset = service._current_chunk_idx * service._chunk_size
            expected_len = min(service._chunk_size, max(service._file_size - start_offset, 0))
            if service._tx_file is None:
                service._fail_tx("TX file handle is closed")
                return
            try:
                chunk_data = ChunkBuffer.read_at(service._tx_file, start_offset, expected_len)
            except OSError as exc:
                service._fail_tx(f"Failed to read file chunk: {exc}")
                return
            if len(chunk_data) != expected_len:
                service._fail_tx(
                    f"Short read at chunk {service._current_chunk_idx}: "
                    f"{len(chunk_data)}/{expected_len} bytes"
                )
                return

            packed = ProtocolAdapter.chunk_payload(
                extended=service._use_extended,
                chunk_id=service._current_chunk_idx,
                data=chunk_data,
            )
            service._cached_chunk_key = service._current_chunk_idx
            service._cached_chunk_payload = packed
            service._cached_chunk_len = len(chunk_data)
            chunk_len = len(chunk_data)

        if new_seq:
            service._seq = (service._seq + 1) % 256

        opcode = Opcode.DATA_CHUNK_LARGE if service._use_extended else Opcode.DATA_CHUNK
        service._serial_manager.send_packet(
            seq=service._seq,
            opcode=opcode,
            payload=packed,
        )
        service._ack_timer.start()
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(
                "%s TX CHUNK %s chunk_id=%d (%d/%d) chunk_bytes=%dB payload_hex=%s %s",
                LogTag.ARQ_ENGINE,
                format_frame_summary(service._seq, opcode, len(packed)),
                service._current_chunk_idx,
                service._current_chunk_idx + 1,
                service._total_chunks,
                chunk_len,
                format_hex_preview(packed, max_bytes=16),
                "RETRY" if not new_seq else f"retry={service._retry_count}/5",
            )

    def _send_file_end(self, new_seq: bool = True) -> None:
        """Transmits the completion verification frame.

        Args:
            new_seq: True to increment sequence number; False for retransmission.
        """
        service: FileTransferService = self._service
        service._set_state(TransferState.FINALIZING)
        packed_end: bytes
        if (
            not new_seq
            and service._cached_end_payload is not None
            and service._cached_end_key == service._bytes_transferred
        ):
            packed_end = service._cached_end_payload
        else:
            packed_end = ProtocolAdapter.end_payload(
                extended=service._use_extended,
                file_crc32=service._file_crc32,
                total_bytes=service._bytes_transferred,
            )
            service._cached_end_key = service._bytes_transferred
            service._cached_end_payload = packed_end
        if new_seq:
            service._seq = (service._seq + 1) % 256

        opcode = Opcode.TRANSFER_END_LARGE if service._use_extended else Opcode.TRANSFER_END
        service._serial_manager.send_packet(
            seq=service._seq,
            opcode=opcode,
            payload=packed_end,
        )
        service._ack_timer.start()
        logger.info(
            "%s TX END %s file_crc=0x%08X bytes=%dB payload_hex=%s %s",
            LogTag.FILE_SVC,
            format_frame_summary(service._seq, opcode, len(packed_end)),
            service._file_crc32,
            service._bytes_transferred,
            format_hex_preview(packed_end),
            "RETRY" if not new_seq else f"retry={service._retry_count}/5",
        )

    def _on_ack_received(self, pkt: Packet) -> None:
        """Handles successful ACK with sequence and status verification."""
        service: FileTransferService = self._service
        if service._state not in (
            TransferState.HANDSHAKING,
            TransferState.TRANSFERRING,
            TransferState.FINALIZING,
        ):
            logger.debug(
                "%s Ignored ACK in %s: %s",
                LogTag.ARQ_ENGINE,
                service._state.name,
                format_frame_summary(pkt.seq, pkt.opcode, len(pkt.payload), pkt.crc32),
            )
            return

        try:
            ack_info = AckPayload.unpack(pkt.payload)
            acked_seq = ack_info.acked_seq
            status = ack_info.status
        except Exception as exc:
            logger.warning(
                "%s ACK payload undecodable %s (%s: %s) hex=%s — assuming seq/OK",
                LogTag.ARQ_ENGINE,
                format_frame_summary(pkt.seq, pkt.opcode, len(pkt.payload)),
                type(exc).__name__,
                exc,
                format_hex_preview(pkt.payload),
            )
            acked_seq = pkt.seq
            status = AckStatus.OK

        if acked_seq != service._seq:
            logger.debug(
                "%s Stale ACK ignored: expected seq=%d got acked_seq=%d status=%s(%d) "
                "state=%s frame=%s",
                LogTag.ARQ_ENGINE,
                service._seq,
                acked_seq,
                ack_status_name(status),
                status,
                service._state.name,
                format_frame_summary(pkt.seq, pkt.opcode, len(pkt.payload)),
            )
            return

        if status != AckStatus.OK:
            logger.warning(
                "%s ACK error status: acked_seq=%d status=%s(%d) state=%s retry=%d/5 "
                "frame=%s payload_hex=%s",
                LogTag.ARQ_ENGINE,
                acked_seq,
                ack_status_name(status),
                status,
                service._state.name,
                service._retry_count + 1,
                format_frame_summary(pkt.seq, pkt.opcode, len(pkt.payload)),
                format_hex_preview(pkt.payload),
            )
            service._ack_timer.stop()
            service._retry_count += 1
            if service._retry_count > MAX_RETRY_COUNT:
                service._fail_tx(
                    f"Transfer failed: Remote error status {status} after {MAX_RETRY_COUNT} retries"
                )
                return
            service._retransmit()
            return

        service._ack_timer.stop()
        service._retry_count = 0
        logger.debug(
            "%s ACK ok: acked_seq=%d state=%s chunk=%d/%d frame=%s",
            LogTag.ARQ_ENGINE,
            acked_seq,
            service._state.name,
            service._current_chunk_idx,
            service._total_chunks,
            format_frame_summary(pkt.seq, pkt.opcode, len(pkt.payload)),
        )

        if service._state == TransferState.HANDSHAKING:
            service._set_state(TransferState.TRANSFERRING)
            service._send_current_chunk(new_seq=True)

        elif service._state == TransferState.TRANSFERRING:
            start_offset = service._current_chunk_idx * service._chunk_size
            end_offset = min(start_offset + service._chunk_size, service._file_size)
            chunk_len = end_offset - start_offset
            service._bytes_transferred += chunk_len

            elapsed = max(time.time() - service._start_time, 0.001)
            speed_kbps = (service._bytes_transferred / 1024.0) / elapsed
            remaining_bytes = service._file_size - service._bytes_transferred
            eta_sec = (remaining_bytes / (speed_kbps * 1024.0)) if speed_kbps > 0 else 0.0
            pct = int((service._bytes_transferred / max(service._file_size, 1)) * 100)

            service.progress_updated.emit(
                pct, speed_kbps, eta_sec, service._current_chunk_idx + 1, service._total_chunks
            )

            service._current_chunk_idx += 1
            if service._current_chunk_idx < service._total_chunks:
                service._send_current_chunk(new_seq=True)
            else:
                service._send_file_end(new_seq=True)

        elif service._state == TransferState.FINALIZING:
            elapsed = max(time.time() - service._start_time, 0.001)
            speed_kbps = (service._bytes_transferred / 1024.0) / elapsed
            crc_hex = f"0x{service._file_crc32:08X}"
            fname = service._current_file_path.name if service._current_file_path else "file"
            fpath = str(service._current_file_path) if service._current_file_path else ""
            total_chunks = service._total_chunks
            bytes_transferred = service._bytes_transferred
            service._set_state(TransferState.COMPLETED)

            rec_id = service._storage.save_file_transfer(
                filename=fname,
                file_path=fpath,
                direction="TX",
                size_bytes=bytes_transferred,
                crc32_hex=crc_hex,
                speed_kbps=round(speed_kbps, 2),
                duration_sec=round(elapsed, 2),
                status="SUCCESS",
            )

            service.transfer_completed.emit(fname, bytes_transferred, elapsed, crc_hex)
            logger.info(
                "%s TX COMPLETE file='%s' path=%s size=%dB chunks=%d elapsed=%.2fs "
                "speed=%.2fKB/s crc=%s stored_id=%d final_ack=%s",
                LogTag.FILE_SVC,
                fname,
                fpath,
                bytes_transferred,
                total_chunks,
                elapsed,
                speed_kbps,
                crc_hex,
                rec_id,
                format_frame_summary(pkt.seq, pkt.opcode, len(pkt.payload)),
            )
            service._reset_tx_session()

    def _on_nack_received(self, pkt: Packet) -> None:
        """Handles NACK request by retransmitting up to retry limit."""
        service: FileTransferService = self._service
        if service._state not in (
            TransferState.HANDSHAKING,
            TransferState.TRANSFERRING,
            TransferState.FINALIZING,
        ):
            logger.debug(
                "%s Ignored NACK in %s: %s",
                LogTag.ARQ_ENGINE,
                service._state.name,
                format_frame_summary(pkt.seq, pkt.opcode, len(pkt.payload)),
            )
            return

        try:
            nack_info = AckPayload.unpack(pkt.payload)
            nack_detail = f"acked_seq={nack_info.acked_seq} status={ack_status_name(nack_info.status)}({nack_info.status})"
        except Exception:
            logger.warning(
                "%s NACK payload undecodable %s — ignored pending timeout",
                LogTag.ARQ_ENGINE,
                format_frame_summary(pkt.seq, pkt.opcode, len(pkt.payload)),
            )
            return
        if nack_info.acked_seq != service._seq:
            logger.warning(
                "%s Stale NACK ignored: expected seq=%d got acked_seq=%d state=%s frame=%s",
                LogTag.ARQ_ENGINE,
                service._seq,
                nack_info.acked_seq,
                service._state.name,
                format_frame_summary(pkt.seq, pkt.opcode, len(pkt.payload)),
            )
            return
        logger.warning(
            "%s NACK %s state=%s retry=%d/5 seq=%d %s payload_hex=%s",
            LogTag.ARQ_ENGINE,
            format_frame_summary(pkt.seq, pkt.opcode, len(pkt.payload)),
            service._state.name,
            service._retry_count + 1,
            service._seq,
            nack_detail,
            format_hex_preview(pkt.payload),
        )
        service._ack_timer.stop()
        service._retry_count += 1
        if service._retry_count > MAX_RETRY_COUNT:
            service._fail_tx(f"Transfer failed: Max retries ({MAX_RETRY_COUNT}) exceeded on NACK")
            return

        service._retransmit()

    def _on_ack_timeout(self) -> None:
        """Handles Stop-and-Wait ACK timeout."""
        service: FileTransferService = self._service
        if service._state not in (
            TransferState.HANDSHAKING,
            TransferState.TRANSFERRING,
            TransferState.FINALIZING,
        ):
            logger.debug(
                "%s Ignored ACK timeout in %s",
                LogTag.ARQ_ENGINE,
                service._state.name,
            )
            return

        service._retry_count += 1
        if service._retry_count > MAX_RETRY_COUNT:
            service._fail_tx(f"Transfer failed: ACK timeout after {MAX_RETRY_COUNT} retries")
            return

        logger.warning(
            "%s ACK timeout 250ms (retry %d/%d) state=%s chunk=%d/%d seq=%d sent=%dB",
            LogTag.ARQ_ENGINE,
            service._retry_count,
            MAX_RETRY_COUNT,
            service._state.name,
            service._current_chunk_idx,
            service._total_chunks,
            service._seq,
            service._bytes_transferred,
        )
        service._retransmit()

    def _retransmit(self) -> None:
        """Retransmits current unacknowledged packet using existing sequence number."""
        service: FileTransferService = self._service
        logger.info(
            "%s Retransmit state=%s retry=%d/5 seq=%d chunk=%d/%d",
            LogTag.ARQ_ENGINE,
            service._state.name,
            service._retry_count,
            service._seq,
            service._current_chunk_idx,
            service._total_chunks,
        )
        if service._state == TransferState.HANDSHAKING:
            if service._cached_start_payload is None and service._current_file_path:
                service._cached_start_payload = ProtocolAdapter.start_payload(
                    extended=service._use_extended,
                    file_size=service._file_size,
                    chunk_size=service._chunk_size,
                    total_chunks=service._total_chunks,
                    file_crc32=service._file_crc32,
                    filename=service._current_file_path.name,
                )
            if service._cached_start_payload is not None:
                opcode = (
                    Opcode.TRANSFER_START_LARGE if service._use_extended else Opcode.TRANSFER_START
                )
                service._serial_manager.send_packet(
                    seq=service._seq,
                    opcode=opcode,
                    payload=service._cached_start_payload,
                )
                service._ack_timer.start()

        elif service._state == TransferState.TRANSFERRING:
            service._send_current_chunk(new_seq=False)

        elif service._state == TransferState.FINALIZING:
            service._send_file_end(new_seq=False)
