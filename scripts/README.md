# scripts/ — Build & Dev Helpers

| File | Dùng khi nào | Lệnh |
|---|---|---|
| `build_standalone.py` | Đóng gói app (CI + release, xem `.github/workflows/build-windows.yml`) | `uv run python scripts/build_standalone.py` |
| `build_modular_web.py` | Ráp `src/ir_terminal/ui/web/components/*.html` → `src/ir_terminal/ui/web/index.html` + fix encoding BER. Chạy được từ mọi thư mục làm việc | `uv run python scripts/build_modular_web.py` (kiểm tra diff `index.html` trước khi commit) |
| `capture_all_screens.py` | Chụp 4 ảnh demo vào `docs/screenshots/` (offscreen, MOCK loopback) | `uv run python scripts/capture_all_screens.py` |

Quy ước: `index.html` là artifact được commit — sửa UI thì sửa `components/` rồi chạy builder, không sửa thẳng `index.html` và `components/` song song gây drift.
