"""Tệp lệnh chạy trạm trung chuyển tia quang ảo (LinkBroker) độc lập từ dòng lệnh.

Dành cho kỹ sư kiểm thử khi muốn khởi động riêng trạm trung chuyển ảo bên ngoài ứng dụng chính.
Hỗ trợ kiểm tra việc giả lập che khuất tia sáng (bấm 'b' + Enter để giả lập vật cản chắn tia quang).
"""

from __future__ import annotations

from ir_terminal.core.link_broker import LinkBroker, VirtualOpticalLink, main

__all__ = ["LinkBroker", "VirtualOpticalLink", "main"]


if __name__ == "__main__":
    raise SystemExit(main())
