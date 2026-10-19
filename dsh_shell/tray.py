"""System tray menu for the shell host process.

The tray uses Qt's :class:`QSystemTrayIcon` on every platform, so the icon file
is handed to Qt directly and never decoded in Python.  On Linux Qt speaks the
StatusNotifierItem / DBusMenu protocol, which is what GNOME's AppIndicators
extension renders and which Wayland desktops host (unlike XEmbed).
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

from loguru import logger

from .ipc import (
    OPEN_EXTERNAL,
    OPEN_SETTINGS,
    QUIT,
    RELOAD,
    RESTART_DSH,
    TOGGLE_MAIN,
)
from .paths import APP_DISPLAY_NAME, icon_path


class TrayHost:
    """Owns the tray icon; commands are forwarded to the webview child."""

    def __init__(
        self,
        send: Callable[[str], None],
        *,
        on_quit: Callable[[], None],
        ensure_child: Callable[[], None] | None = None,
    ) -> None:
        self._send = send
        self._on_quit = on_quit
        self._ensure_child = ensure_child
        self._qt_app: Any = None
        self._qt_tray: Any = None
        self._qt_menu: Any = None
        self._quit_bridge: Any = None
        self._lock = threading.Lock()
        self._quit_requested = False

    def _command(self, command: str) -> None:
        if self._ensure_child is not None:
            try:
                self._ensure_child()
            except Exception as error:
                logger.warning("Could not restart webview child: {}", error)
        self._send(command)

    def _toggle(self, icon: Any = None, item: Any = None) -> None:
        self._command(TOGGLE_MAIN)

    def _settings(self, icon: Any = None, item: Any = None) -> None:
        self._command(OPEN_SETTINGS)

    def _reload(self, icon: Any = None, item: Any = None) -> None:
        self._command(RELOAD)

    def _external(self, icon: Any = None, item: Any = None) -> None:
        self._command(OPEN_EXTERNAL)

    def _restart_dsh(self, icon: Any = None, item: Any = None) -> None:
        self._command(RESTART_DSH)

    def _quit(self, icon: Any = None, item: Any = None) -> None:
        with self._lock:
            if self._quit_requested:
                return
            self._quit_requested = True
        try:
            self._send(QUIT)
        except Exception as error:
            logger.warning("Could not send quit command: {}", error)
        try:
            self._on_quit()
        finally:
            self.stop()

    def stop(self) -> None:
        bridge = self._quit_bridge
        if bridge is None:
            return
        try:
            # Queued signal: safe to emit from the monitor thread.
            bridge.requested.emit()
        except Exception as error:
            logger.debug("Qt tray stop failed: {}", error)

    def notify(self, message: str, title: str = APP_DISPLAY_NAME) -> None:
        tray = self._qt_tray
        if tray is None:
            return
        try:
            tray.showMessage(title, message)
        except Exception as error:
            logger.debug("Tray notification unavailable: {}", error)

    # --------------------------------------------------------------------- Qt
    def _run_qt(self) -> bool:
        try:
            from PySide6.QtCore import QObject, Qt, Signal
            from PySide6.QtGui import QIcon
            from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon
        except Exception as error:
            logger.warning("Qt tray unavailable: {}", error)
            return False

        app = QApplication.instance() or QApplication([])
        app.setApplicationName("dsh-shell")
        app.setDesktopFileName("dsh-shell")
        app.setQuitOnLastWindowClosed(False)
        if not QSystemTrayIcon.isSystemTrayAvailable():
            logger.warning("System tray is unavailable; close buttons will quit instead of hiding")
            return False

        class _QuitBridge(QObject):
            requested = Signal()

        tray = QSystemTrayIcon(QIcon(str(icon_path())), app)
        tray.setToolTip(APP_DISPLAY_NAME)
        menu = QMenu()
        for label, handler in (
            ("显示/隐藏", self._toggle),
            ("MCP 与 Skills 设置…", self._settings),
            ("重新加载页面", self._reload),
            ("在浏览器中打开", self._external),
        ):
            action = menu.addAction(label)
            action.triggered.connect(handler)
        menu.addSeparator()
        for label, handler in (
            ("重启 DSH 服务", self._restart_dsh),
            ("退出", self._quit),
        ):
            action = menu.addAction(label)
            action.triggered.connect(handler)
        tray.setContextMenu(menu)

        def on_activated(reason: Any) -> None:
            if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick):
                self._toggle()

        tray.activated.connect(on_activated)

        bridge = _QuitBridge()
        bridge.requested.connect(app.quit, Qt.ConnectionType.QueuedConnection)

        self._qt_app = app
        self._qt_tray = tray
        self._qt_menu = menu
        self._quit_bridge = bridge
        tray.show()
        logger.info("System tray icon started (Qt)")
        try:
            app.exec()
            return True
        except Exception as error:
            logger.warning("Qt tray loop failed: {}", error)
            return False
        finally:
            self._quit_bridge = None
            self._qt_tray = None
            self._qt_app = None
            try:
                tray.hide()
                menu.deleteLater()
            except Exception:
                pass
            self._qt_menu = None

    def run(self) -> bool:
        """Run the tray loop; returns False when the desktop has no tray support."""
        return self._run_qt()
