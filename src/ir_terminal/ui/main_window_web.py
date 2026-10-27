"""QWebEngineView Main Window integrating full HTML/Tailwind SPA with Python Backend."""

from __future__ import annotations

import logging

from PySide6.QtCore import QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile, QWebEngineSettings
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QMainWindow, QVBoxLayout, QWidget

from ir_terminal.config import InstanceContext
from ir_terminal.core.serial_manager import SerialManager
from ir_terminal.ui.backend_bridge import BackendBridge
from ir_terminal.utils.logger import LogTag
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
            logger.error("%s %s", LogTag.IPC_BRIDGE, text)
        elif level == QWebEnginePage.JavaScriptConsoleMessageLevel.WarningMessageLevel:
            logger.warning("%s %s", LogTag.IPC_BRIDGE, text)
        else:
            logger.debug("%s %s", LogTag.IPC_BRIDGE, text)


class WebMainWindow(QMainWindow):
    """Main Application Window hosting Chromium WebEngine and Python QWebChannel."""

    def __init__(
        self,
        serial_manager: SerialManager,
        bridge: BackendBridge | None = None,
        instance_context: InstanceContext | None = None,
    ) -> None:
        """Initializes main web application window.

        Args:
            serial_manager: Serial manager instance managing background I/O.
            bridge: Optional injected BackendBridge instance.
            instance_context: Optional per-instance layout/identity. When set,
                a dedicated QWebEngineProfile isolates Chromium cache/cookies.
        """
        super().__init__()
        self._serial_manager = serial_manager
        self._instance_context = instance_context
        self._profile: QWebEngineProfile | None = None
        if bridge is None:
            logger.warning(
                "%s WebMainWindow without injected bridge; creating default (port=%s connected=%s)",
                LogTag.IPC_BRIDGE,
                serial_manager.current_port,
                serial_manager.is_connected,
            )
        self._bridge = bridge or BackendBridge(self._serial_manager)

        self._init_window()
        self._init_webengine()
        logger.info(
            "%s WebMainWindow ready: 1380x880 bridge=%s node=%s",
            LogTag.IPC_BRIDGE,
            type(self._bridge).__name__,
            instance_context.display_name if instance_context else "single",
        )

    def closeEvent(self, event: object) -> None:
        """Ensures worker thread and timers stop on window close (no QThread leak)."""
        logger.info(
            "%s WebMainWindow close requested; shutting down bridge+serial", LogTag.IPC_BRIDGE
        )
        try:
            self._bridge.shutdown()
        except Exception as exc:
            logger.warning(
                "%s Bridge shutdown on close FAILED (%s: %s)",
                LogTag.IPC_BRIDGE,
                type(exc).__name__,
                exc,
            )
        try:
            self._serial_manager.shutdown()
        except Exception as exc:
            logger.warning(
                "%s Serial shutdown on close FAILED (%s: %s)",
                LogTag.HW_SERIAL,
                type(exc).__name__,
                exc,
            )
        super().closeEvent(event)  # type: ignore[arg-type]

    def _init_window(self) -> None:
        """Configures window properties, minimum bounds, and title."""
        title = (
            self._instance_context.window_title
            if self._instance_context is not None
            else "SpectraLink IR-OWC Workstation"
        )
        self.setWindowTitle(title)
        self.resize(1380, 880)
        self.setMinimumSize(1100, 700)

    def _create_page(self) -> WebAppPage:
        """Creates the WebEngine page, isolating the profile per instance."""
        ctx = self._instance_context
        if ctx is None:
            return WebAppPage(self._view)
        profile = QWebEngineProfile(ctx.profile_key, self)
        profile.setPersistentStoragePath(str(ctx.webengine_storage_dir))
        profile.setCachePath(str(ctx.webengine_cache_dir))
        profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.DiskHttpCache)
        profile.setPersistentCookiesPolicy(
            QWebEngineProfile.PersistentCookiesPolicy.ForcePersistentCookies
        )
        self._profile = profile
        logger.info(
            "%s Per-instance WebEngine profile: name=%s cache=%s storage=%s",
            LogTag.IPC_BRIDGE,
            ctx.profile_key,
            ctx.webengine_cache_dir,
            ctx.webengine_storage_dir,
        )
        return WebAppPage(profile, self._view)

    def _init_webengine(self) -> None:
        """Instantiates WebEngine view, registers WebChannel bridge, and loads index.html."""
        central_widget = QWidget(self)
        layout = QVBoxLayout(central_widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._view = QWebEngineView(self)
        self._page = self._create_page()
        self._view.setPage(self._page)

        settings = self._view.settings()
        settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)
        settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptEnabled, True)

        self._channel = QWebChannel(self._page)
        self._channel.registerObject("bridge", self._bridge)
        self._page.setWebChannel(self._channel)

        self._bridge.closeWindowRequested.connect(self.close)
        self._bridge.minimizeWindowRequested.connect(self.showMinimized)
        self._bridge.maximizeWindowRequested.connect(self._toggle_maximize)

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

    def apply_window_position(self, position: str) -> None:
        """Places the window on the left/right half of the primary screen.

        Used by the dual-node launcher so Node A and Node B sit side by side.

        Args:
            position: ``left``, ``right``, or anything else (no-op).
        """
        if position not in ("left", "right"):
            return
        screen = QGuiApplication.primaryScreen()
        geo = screen.availableGeometry()
        half_w = max(720, geo.width() // 2)
        self.setMinimumSize(min(self.minimumWidth(), half_w), 700)
        self.resize(half_w, geo.height())
        x = geo.x() if position == "left" else geo.x() + geo.width() - half_w
        self.move(x, geo.y())

    @property
    def instance_context(self) -> InstanceContext | None:
        """Returns the per-instance context, or None in single/default mode."""
        return self._instance_context

    @property
    def bridge(self) -> BackendBridge:
        """Returns the BackendBridge instance exposed to Chromium."""
        return self._bridge

    @property
    def web_view(self) -> QWebEngineView:
        """Returns the inner QWebEngineView instance."""
        return self._view
