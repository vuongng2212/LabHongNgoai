"""Application bootstrap, stylesheet loading, and dependency wiring."""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from ir_terminal.core.serial_manager import SerialManager
from ir_terminal.services.chat_service import ChatService
from ir_terminal.services.file_transfer_service import FileTransferService
from ir_terminal.services.storage_service import StorageService
from ir_terminal.services.telemetry_service import TelemetryService
from ir_terminal.ui.backend_bridge import BackendBridge
from ir_terminal.ui.main_window_web import WebMainWindow
from ir_terminal.utils.logger import setup_logger
from ir_terminal.utils.path_helpers import (
    get_data_dir,
    get_instance_name,
    get_logs_dir,
    get_transfers_dir,
)


def create_app(instance: str | None = None) -> tuple[QApplication, WebMainWindow]:
    """Bootstraps and configures the Qt application instance.

    Args:
        instance: Optional dual-mock instance name (A/B). When set, JSON
            stores, transfers, and logs are isolated under
            ``<app root>/data/instances/<A|B>/`` so two apps on one PC never
            share state. None keeps the shared ``<app root>/data/`` tree.

    Returns:
        A tuple of (QApplication, WebMainWindow).
    """
    logger, _ = setup_logger()
    inst = get_instance_name(instance)
    # Re-point file handler at the isolated instance log dir when available.
    try:
        import logging
        from pathlib import Path as _Path

        _log_dir = get_logs_dir(inst)
        _root = logging.getLogger("ir_terminal")
        for _h in list(_root.handlers):
            _name = type(_h).__name__
            if _name == "RotatingFileHandler":
                _base = getattr(_h, "baseFilename", "")
                if _base and _Path(_base).parent != _log_dir:
                    _root.removeHandler(_h)
                    try:
                        _h.close()
                    except Exception:
                        pass
        from ir_terminal.utils.logger import _add_file_handler as _add_fh

        _add_fh(_root, _log_dir)
    except Exception:
        pass
    logger.info(
        "Initializing IR-OWC Lab Terminal (QWebEngine + QWebChannel)... instance=%s "
        "data=%s transfers=%s logs=%s",
        inst or "single",
        get_data_dir(inst),
        get_transfers_dir(inst),
        get_logs_dir(inst),
    )

    qt_instance = QApplication.instance()
    if isinstance(qt_instance, QApplication):
        qt_app = qt_instance
    else:
        qt_app = QApplication(sys.argv)

    qt_app.setApplicationName("IR-OWC Lab Terminal")
    qt_app.setOrganizationName("LabHongNgoai")

    # Dependency Injection Container & Composition Root
    storage_service = StorageService(data_dir=get_data_dir(inst))
    serial_manager = SerialManager()
    chat_service = ChatService(serial_manager=serial_manager, storage_service=storage_service)
    file_service = FileTransferService(
        serial_manager=serial_manager,
        storage_service=storage_service,
        transfers_dir=get_transfers_dir(inst),
    )
    telemetry_service = TelemetryService(storage_service=storage_service)

    # Restore persisted transfer settings (max size / receive dir); defaults
    # apply on first run when no settings.json keys exist yet.
    file_service.load_settings()

    bridge = BackendBridge(
        serial_manager=serial_manager,
        chat_service=chat_service,
        file_service=file_service,
        telemetry_service=telemetry_service,
        storage_service=storage_service,
    )
    main_window = WebMainWindow(serial_manager=serial_manager, bridge=bridge)
    logger.info(
        "DI wiring complete: Storage=%s SerialManager=%s Chat=%s File(state=%s) "
        "Telemetry(sent=%d) Bridge=%s Window=%s",
        type(storage_service).__name__,
        type(serial_manager).__name__,
        type(chat_service).__name__,
        file_service.state.name,
        telemetry_service.total_packets_sent,
        type(bridge).__name__,
        type(main_window).__name__,
    )

    return qt_app, main_window


def run() -> int:
    """Application main entry point.

    Supports ``--instance A|B`` for dual-mock mode (2 apps on 1 PC with
    isolated data dirs). Production single-app usage is unchanged.

    Returns:
        Exit code integer.
    """
    import argparse
    import os

    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--instance", default=None)
    args, _ = parser.parse_known_args()
    instance = args.instance or os.environ.get("IR_INSTANCE")
    app, window = create_app(instance=instance)
    window.show()
    return app.exec()
