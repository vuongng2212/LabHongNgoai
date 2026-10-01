"""Exercising Realtime Optical Chat and File Transfer in live WebEngine UI."""

import os
import tempfile
import time

os.environ["QTWEBENGINE_DISABLE_SANDBOX"] = "1"
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from ir_terminal.app import create_app


def run_test() -> None:
    app, window = create_app()
    window.resize(1380, 880)
    window.show()

    # Wait for WebEngine initialization
    for _ in range(30):
        app.processEvents()
        time.sleep(0.05)

    # 1. Connect serial port
    window.bridge.connectPort("MOCK_COM_LOOPBACK", 115200)
    for _ in range(15):
        app.processEvents()
        time.sleep(0.05)

    # 2. Test Realtime Chat
    window.web_view.page().runJavaScript("window.switchView('view-chat');")
    for _ in range(10):
        app.processEvents()
        time.sleep(0.05)

    window.bridge.sendChat("SpectraLink Optical Link Verified @ 880nm NIR!")
    for _ in range(25):
        app.processEvents()
        time.sleep(0.05)

    pixmap_chat = window.grab()
    pixmap_chat.save("docs/screenshots/3_realtime_chat.png", "PNG")
    print("Saved live chat screenshot -> docs/screenshots/3_realtime_chat.png")

    # 3. Test File Transfer
    window.web_view.page().runJavaScript("window.switchView('view-file');")
    for _ in range(10):
        app.processEvents()
        time.sleep(0.05)

    with tempfile.NamedTemporaryFile(suffix=".dat", prefix="laser_payload_", delete=False) as tmp:
        tmp.write(b"OPTICAL_DATA_CHUNK_VERIFICATION_TEST_" * 128)  # ~4.7 KB
        tmp_path = tmp.name

    try:
        window.bridge.startFileTransfer(tmp_path)
        for _ in range(40):
            app.processEvents()
            time.sleep(0.05)

        pixmap_file = window.grab()
        pixmap_file.save("docs/screenshots/2_file_transfer.png", "PNG")
        print("Saved live file transfer screenshot -> docs/screenshots/2_file_transfer.png")
    finally:
        os.unlink(tmp_path)
        window._serial_manager.shutdown()


if __name__ == "__main__":
    run_test()
