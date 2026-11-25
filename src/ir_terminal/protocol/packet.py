"""Mô hình dữ liệu gói tin bất biến (Immutable Packet Model) tương thích WinForm C#.

Đại diện cho một khung truyền nhị phân đã được giải mã đầy đủ từ đường truyền quang IR-OWC.
Bao gồm định danh sequence (4 bytes), độ dài payload length (2 bytes) và dữ liệu thô payload.
"""

from __future__ import annotations

from dataclasses import dataclass

from ir_terminal.protocol.constants import (
    SEQ_CHECKPOINT_ACK,
    SEQ_CHECKPOINT_PROBE,
    SEQ_END,
    SEQ_HEADER,
    SEQ_RESUME_REQUEST,
)


@dataclass(frozen=True)
class WinFormPacket:
    """Đối tượng đại diện cho một khung truyền hoàn chỉnh nhận từ kênh quang.

    Attributes:
        sequence: Mã định danh khung truyền (uint32). Giá trị SEQ_HEADER (0xFFFFFFFF)
            thông báo mở file mới, SEQ_END (0xFFFFFFFE) báo kết thúc phiên truyền,
            và các giá trị khác biểu diễn chỉ số chunk dữ liệu tính từ 0.
        length: Độ dài byte thực tế của payload theo khai báo trong header (uint16).
        data: Chuỗi byte thô của phần dữ liệu payload.
    """

    sequence: int
    length: int
    data: bytes

    @property
    def is_header(self) -> bool:
        """Kiểm tra gói tin có phải là khung tiêu đề mở phiên truyền file (SEQ_HEADER) hay không."""
        return self.sequence == SEQ_HEADER

    @property
    def is_end(self) -> bool:
        """Kiểm tra gói tin có phải là khung kết thúc phiên truyền file (SEQ_END) hay không."""
        return self.sequence == SEQ_END

    @property
    def is_checkpoint_ack(self) -> bool:
        """Kiểm tra gói tin có phải là xác nhận điểm kiểm tra (SEQ_CHECKPOINT_ACK) hay không."""
        return self.sequence == SEQ_CHECKPOINT_ACK

    @property
    def is_checkpoint_probe(self) -> bool:
        """Kiểm tra gói tin có phải là thăm dò điểm kiểm tra (SEQ_CHECKPOINT_PROBE) hay không."""
        return self.sequence == SEQ_CHECKPOINT_PROBE

    @property
    def is_resume_request(self) -> bool:
        """Kiểm tra gói tin có phải là yêu cầu tiếp tục truyền (SEQ_RESUME_REQUEST) hay không."""
        return self.sequence == SEQ_RESUME_REQUEST

    @property
    def is_data(self) -> bool:
        """Kiểm tra gói tin có phải là chunk dữ liệu nội dung file hay không."""
        return (
            not self.is_header
            and not self.is_end
            and not self.is_checkpoint_ack
            and not self.is_checkpoint_probe
            and not self.is_resume_request
        )
