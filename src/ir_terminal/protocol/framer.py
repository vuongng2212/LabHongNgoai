"""Đóng gói và phân tích cú pháp khung truyền (Wire Framing & Stream Parsing) cho giao thức quang WinForm C#.

Module này chịu trách nhiệm:
1. Đóng gói (pack) các khung dữ liệu nhị phân 8 byte overhead theo chuẩn Little-Endian.
2. Tuần tự hóa và giải mã siêu dữ liệu (metadata) của khung tiêu đề HEADER.
3. Giải mã dòng byte nối tiếp (stream parsing) nhận từ UART: tự động khử nhiễu kênh quang,
   quét tìm cặp byte đồng bộ SYNC (0xAA 0x55) và bảo toàn các mẩu khung chưa trọn vẹn cho chu kỳ sau.
"""

from __future__ import annotations

import struct

from ir_terminal.protocol.constants import (
    HEADER_OVERHEAD,
    MAX_HEADER_PAYLOAD_SIZE,
    SYNC_MAGIC,
)
from ir_terminal.protocol.packet import WinFormPacket

_PACKET_HEADER: struct.Struct = struct.Struct("<IH")

_HEADER_NAME_LENGTH: struct.Struct = struct.Struct("<H")

_HEADER_TOTAL_PACKETS: struct.Struct = struct.Struct("<i")

_MAX_SEQUENCE: int = 0xFFFFFFFF


def pack_packet(sequence: int, data: bytes) -> bytes:
    """Đóng gói một khung truyền nhị phân chuẩn WinForm C# để phát qua đường truyền quang.

    Cấu trúc khung trên dây cáp quang:
        - SYNC MAGIC: 2 bytes cố định (0xAA, 0x55)
        - SEQUENCE: 4 bytes unsigned int Little-Endian (uint32 LE)
        - LENGTH: 2 bytes unsigned short Little-Endian (uint16 LE)
        - PAYLOAD: N bytes dữ liệu thô (đúng bằng giá trị LENGTH)

    Args:
        sequence: Mã định danh khung truyền (0xFFFFFFFF = HEADER, 0xFFFFFFFE = END, 0..N-1 = DATA).
        data: Dữ liệu thô payload cần đóng gói (tối đa 65535 bytes, thông thường 256 bytes cho gói DATA).

    Returns:
        Chuỗi byte hoàn chỉnh (wire bytes) sẵn sàng truyền qua UART COM1.

    Raises:
        ValueError: Nếu sequence vượt quá giới hạn uint32 hoặc kích thước payload vượt quá uint16.
    """
    if not 0 <= sequence <= _MAX_SEQUENCE:
        raise ValueError(f"Sequence {sequence} must fit uint32")
    payload = bytes(data)
    if len(payload) > MAX_HEADER_PAYLOAD_SIZE:
        raise ValueError(
            f"Payload size {len(payload)} exceeds wire limit {MAX_HEADER_PAYLOAD_SIZE} bytes"
        )
    return b"".join((SYNC_MAGIC, _PACKET_HEADER.pack(sequence, len(payload)), payload))


def pack_header_payload(file_name: str, total_packets: int) -> bytes:
    """Đóng gói nội dung payload cho khung tiêu đề SEQ_HEADER (0xFFFFFFFF).

    Định dạng payload tiêu đề chuẩn WinForm C#:
        - nameLength: uint16 LE (2 bytes) - Số byte của tên file mã hóa UTF-8.
        - fileName: Chuỗi ký tự UTF-8 (nameLength bytes).
        - totalPackets: int32 LE (4 bytes) - Tổng số lượng chunk DATA sẽ truyền tiếp theo.

    Args:
        file_name: Tên file gốc cần thông báo cho trạm nhận.
        total_packets: Tổng số lượng chunk dữ liệu (thường là ceil(fileSize ÷ 256)).

    Returns:
        Chuỗi byte payload của gói HEADER.

    Raises:
        ValueError: Nếu tên file quá dài hoặc số lượng packet là số âm.
    """
    name_bytes = (file_name or "").encode("utf-8")
    if len(name_bytes) > MAX_HEADER_PAYLOAD_SIZE:
        raise ValueError(
            f"File name is {len(name_bytes)} bytes, wire limit is {MAX_HEADER_PAYLOAD_SIZE}"
        )
    if total_packets < 0:
        raise ValueError(f"Total packets must be non-negative, got {total_packets}")
    return b"".join(
        (
            _HEADER_NAME_LENGTH.pack(len(name_bytes)),
            name_bytes,
            _HEADER_TOTAL_PACKETS.pack(total_packets),
        )
    )


def unpack_header_payload(data: bytes) -> tuple[str, int]:
    """Giải mã nội dung payload của khung tiêu đề SEQ_HEADER.

    Bóc tách các trường: độ dài tên (uint16 LE), tên file UTF-8 và tổng số packet (int32 LE).

    Args:
        data: Dữ liệu thô payload của gói HEADER nhận từ kênh quang.

    Returns:
        Tuple gồm (tên_file, tổng_số_packet).

    Raises:
        ValueError: Nếu payload bị cắt ngắn, độ dài không hợp lệ hoặc số lượng packet âm.
    """
    raw = bytes(data)
    if len(raw) < _HEADER_NAME_LENGTH.size + _HEADER_TOTAL_PACKETS.size:
        raise ValueError(f"Header payload too short: {len(raw)} bytes")
    name_length = _HEADER_NAME_LENGTH.unpack_from(raw, 0)[0]
    total_offset = _HEADER_NAME_LENGTH.size + name_length
    if len(raw) < total_offset + _HEADER_TOTAL_PACKETS.size:
        raise ValueError(
            f"Header payload truncated: declared name {name_length} bytes in {len(raw)} byte payload"
        )
    file_name = raw[_HEADER_NAME_LENGTH.size : total_offset].decode("utf-8", errors="replace")
    total_packets = _HEADER_TOTAL_PACKETS.unpack_from(raw, total_offset)[0]
    if total_packets < 0:
        raise ValueError(f"Header declares negative packet count {total_packets}")
    return file_name, total_packets


def parse_stream(buffer: bytearray) -> tuple[list[WinFormPacket], bytearray]:
    """Trích xuất toàn bộ các khung truyền hợp lệ từ dòng byte thô nhận được qua đường quang.

    Thuật toán xử lý:
        1. Quét tìm vị trí cặp byte đồng bộ SYNC MAGIC (0xAA 0x55).
        2. Tự động loại bỏ toàn bộ byte rác/nhiễu kênh quang đứng trước vị trí SYNC.
        3. Kiểm tra độ dài: Nếu còn thiếu 8 byte tiêu đề hoặc thiếu payload so với trường length,
           giữ lại đoạn byte chưa hoàn chỉnh vào bộ đệm chờ lượt đọc sau.
        4. Kiểm tra an toàn: Nếu trường length > 1024 bytes (dấu hiệu nhiễu giả mạo SYNC),
           bỏ qua byte 0xAA hiện tại và tiếp tục trượt tìm SYNC tiếp theo.
        5. Nếu frame hoàn chỉnh, khởi tạo đối tượng WinFormPacket và tiếp tục phân tích dòng byte còn lại.

    Args:
        buffer: Bộ đệm tích lũy các byte thô nhận từ cổng nối tiếp COM1.

    Returns:
        Tuple gồm danh sách các gói tin WinFormPacket đã giải mã thành công và phần byte dư thừa chưa hoàn tất.
    """
    raw = bytes(buffer)
    total = len(raw)
    packets: list[WinFormPacket] = []
    position = 0

    while True:
        sync_at = raw.find(SYNC_MAGIC, position)
        if sync_at < 0:
            position = total - 1 if total > position and raw.endswith(SYNC_MAGIC[:1]) else total
            break
        if total - sync_at < HEADER_OVERHEAD:
            position = sync_at
            break
        sequence, length = _PACKET_HEADER.unpack_from(raw, sync_at + len(SYNC_MAGIC))
        if length > 1024:
            position = sync_at + 1
            continue
        frame_end = sync_at + HEADER_OVERHEAD + length
        if total < frame_end:
            position = sync_at
            break
        payload = raw[sync_at + HEADER_OVERHEAD : frame_end]
        packets.append(WinFormPacket(sequence=sequence, length=length, data=payload))
        position = frame_end

    return packets, bytearray(raw[position:])
