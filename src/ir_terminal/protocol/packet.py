"""Mô hình dữ liệu gói tin bất biến (Immutable Packet Model) tương thích WinForm C#.

Đại diện cho một khung truyền nhị phân đã được giải mã đầy đủ từ đường truyền quang IR-OWC.
Bao gồm định danh sequence (4 bytes), độ dài payload length (2 bytes) và dữ liệu thô payload.
"""

from __future__ import annotations

from dataclasses import dataclass

from ir_terminal.protocol.constants import SEQ_END, SEQ_HEADER


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
    def is_data(self) -> bool:
        """Kiểm tra gói tin có phải là chunk dữ liệu nội dung file (0 <= sequence < SEQ_END) hay không."""
        return not self.is_header and not self.is_end
