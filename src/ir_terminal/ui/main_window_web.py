"""QWebEngineView Main Window integrating full HTML/Tailwind SPA with Python Backend."""

from __future__ import annotations

import logging

from PySide6.QtCore import QUrl
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QMainWindow, QVBoxLayout, QWidget

from ir_terminal.core.serial_manager import SerialManager
from ir_terminal.ui.backend_bridge import BackendBridge
from ir_terminal.utils.path_helpers import get_web_index_path

logger = logging.getLogger(__name__)


class WebAppPage(QWebEnginePage):
    """Custom WebEnginePage with enhanced logging for JS console."""

    def javaScriptConsoleMessage(
        self,
        level: QWebEnginePage.JavaScriptConsoleMessageLevel,
        message: str,
        line_number: int,
        source_id: str,
    ) -> None:
        """Forwards JavaScript console output to Python logger.

        Args:
            level: Log level enum.
            message: Raw log text.
            line_number: Line number of the JS file.
            source_id: Source file URI or name.
        """
        text = f"JS Console [{source_id}:{line_number}]: {message}"
        if level == QWebEnginePage.JavaScriptConsoleMessageLevel.ErrorMessageLevel:
            logger.error(text)
        elif level == QWebEnginePage.JavaScriptConsoleMessageLevel.WarningMessageLevel:
            logger.warning(text)
        else:
            logger.info(text)


class WebMainWindow(QMainWindow):
    """Main Application Window hosting Chromium WebEngine and Python QWebChannel."""

    def __init__(
        self,
        serial_manager: SerialManager,
        bridge: BackendBridge | None = None,
    ) -> None:
        """Initializes main web application window.

        Args:
            serial_manager: Serial manager instance managing background I/O.
            bridge: Optional injected BackendBridge instance.
        """
        super().__init__()
        self._serial_manager = serial_manager
        if bridge is None:
            logger.warning("WebMainWindow created without injected bridge; using default")
        self._bridge = bridge or BackendBridge(self._serial_manager)

        self._init_window()
        self._init_webengine()

    def closeEvent(self, event: object) -> None:
        """Ensures worker thread and timers stop on window close (no QThread leak)."""
        try:
            self._bridge.shutdown()
        except Exception:
            pass
        try:
            self._serial_manager.shutdown()
        except Exception:
            pass
        super().closeEvent(event)  # type: ignore[arg-type]

    def _init_window(self) -> None:
        """Configures window properties, minimum bounds, and title."""
        self.setWindowTitle("SpectraLink IR-OWC Workstation")
        self.resize(1380, 880)
        self.setMinimumSize(1100, 700)

    def _init_webengine(self) -> None:
        """Instantiates WebEngine view, registers WebChannel bridge, and loads index.html."""
        central_widget = QWidget(self)
        layout = QVBoxLayout(central_widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._view = QWebEngineView(self)
        self._page = WebAppPage(self._view)
        self._view.setPage(self._page)

        # Enable local storage and JS (least privilege: file URLs only).
        settings = self._view.settings()
        settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptEnabled, True)

        # Setup QWebChannel
        self._channel = QWebChannel(self._page)
        self._channel.registerObject("bridge", self._bridge)
        self._page.setWebChannel(self._channel)

        # Wire Bridge Window Controls
        self._bridge.closeWindowRequested.connect(self.close)
        self._bridge.minimizeWindowRequested.connect(self.showMinimized)
        self._bridge.maximizeWindowRequested.connect(self._toggle_maximize)

        # Load HTML index
        try:
            index_path = get_web_index_path()
        except FileNotFoundError as exc:
            logger.error("Web UI index.html not found: %s", exc)
            raise
        self._view.load(QUrl.fromLocalFile(str(index_path.resolve())))

        layout.addWidget(self._view)
        self.setCentralWidget(central_widget)

    def _toggle_maximize(self) -> None:
        """Toggles window state between maximized and normal restored geometry."""
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()

    @property
    def bridge(self) -> BackendBridge:
        """Returns the BackendBridge instance exposed to Chromium."""
        return self._bridge

    @property
    def web_view(self) -> QWebEngineView:
        """Returns the inner QWebEngineView instance."""
        return self._view
