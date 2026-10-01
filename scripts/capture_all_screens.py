"""Captures high-resolution pixel-perfect screenshots of all 4 views."""

import os
import sys
import time

os.environ["QTWEBENGINE_DISABLE_SANDBOX"] = "1"
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtWidgets import QApplication
from ir_terminal.app import create_app

def capture_all() -> None:
    app, window = create_app()
    window.resize(1380, 880)
    window.show()

    # Allow WebEngine to initialize and render fonts/scripts
    for _ in range(35):
        app.processEvents()
        time.sleep(0.1)

    os.makedirs("docs/screenshots", exist_ok=True)

    views = [
        ("view-hardware", "docs/screenshots/1_hardware_setup.png"),
        ("view-file", "docs/screenshots/2_file_transfer.png"),
        ("view-chat", "docs/screenshots/3_realtime_chat.png"),
        ("view-telemetry", "docs/screenshots/4_lab_telemetry.png"),
    ]

    for view_id, output_path in views:
        # Switch tab via JavaScript
        window.web_view.page().runJavaScript(f"window.switchView('{view_id}');")
        for _ in range(15):
            app.processEvents()
            time.sleep(0.05)

        pixmap = window.grab()
        pixmap.save(output_path, "PNG")
        print(f"Captured {view_id} -> {output_path} ({os.path.getsize(output_path)} bytes)")

    window._serial_manager.shutdown()

if __name__ == "__main__":
    capture_all()
