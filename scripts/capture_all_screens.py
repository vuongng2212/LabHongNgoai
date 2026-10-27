"""Captures high-resolution pixel-perfect screenshots of all 4 views with live active states."""

import os
import tempfile
import time

os.environ["QTWEBENGINE_DISABLE_SANDBOX"] = "1"
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from ir_terminal.app import create_app


def capture_all() -> None:
    app, window = create_app()
    window.resize(1380, 880)
    window.show()

    for _ in range(40):
        app.processEvents()
        time.sleep(0.05)

    os.makedirs("docs/screenshots", exist_ok=True)

    window.bridge.connectPort("MOCK_COM_LOOPBACK", 115200)
    for _ in range(15):
        app.processEvents()
        time.sleep(0.05)

    window.bridge.sendPing()
    for _ in range(15):
        app.processEvents()
        time.sleep(0.05)

    window.web_view.page().runJavaScript("window.switchView('view-hardware');")
    for _ in range(15):
        app.processEvents()
        time.sleep(0.05)
    pixmap1 = window.grab()
    pixmap1.save("docs/screenshots/1_hardware_setup.png", "PNG")
    print("Captured 1_hardware_setup.png")

    window.web_view.page().runJavaScript("window.switchView('view-file');")
    for _ in range(10):
        app.processEvents()
        time.sleep(0.05)

    with tempfile.NamedTemporaryFile(
        suffix=".pdf", prefix="telemetry_report_", delete=False
    ) as tmp:
        tmp.write(b"INFRARED_OPTICAL_WIRELESS_COMMUNICATION_FRAME_DATA_" * 160)  # ~8 KB
        tmp_path = tmp.name

    try:
        window.bridge.startFileTransfer(tmp_path)
        for _ in range(35):
            app.processEvents()
            time.sleep(0.05)

        pixmap2 = window.grab()
        pixmap2.save("docs/screenshots/2_file_transfer.png", "PNG")
        print("Captured 2_file_transfer.png")
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)

    window.web_view.page().runJavaScript("window.switchView('view-chat');")
    for _ in range(10):
        app.processEvents()
        time.sleep(0.05)

    window.bridge.sendChat("SpectraLink Optical Link 880nm NIR Verified (BER < 10^-9)!")
    for _ in range(25):
        app.processEvents()
        time.sleep(0.05)

    pixmap3 = window.grab()
    pixmap3.save("docs/screenshots/3_realtime_chat.png", "PNG")
    print("Captured 3_realtime_chat.png")

    window.web_view.page().runJavaScript("window.switchView('view-telemetry');")
    for _ in range(15):
        app.processEvents()
        time.sleep(0.05)

    pixmap4 = window.grab()
    pixmap4.save("docs/screenshots/4_lab_telemetry.png", "PNG")
    print("Captured 4_lab_telemetry.png")

    window._serial_manager.shutdown()
    print("All 4 screens successfully captured and saved to docs/screenshots/")


if __name__ == "__main__":
    capture_all()
