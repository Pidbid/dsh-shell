"""The ``window.pywebview.api`` surface used by the shell chrome and settings UI."""

from __future__ import annotations

import os
import platform
import subprocess
import sys
import threading
import time
import webbrowser
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import webview
from loguru import logger

from .dsh_runtime import DshError, DshRuntime
from .mcp_config import McpConfigManager
from .paths import APP_DISPLAY_NAME, data_dir, icon_path, skills_root
from .skills import SkillsManager
from .version import __version__


def _open_path(path: Path) -> bool:
    target = path.expanduser()
    if not target.exists() and target.suffix:
        target.parent.mkdir(parents=True, exist_ok=True)
    try:
        if sys.platform == "win32":
            os.startfile(str(target))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(target)], close_fds=True)
        else:
            subprocess.Popen(["xdg-open", str(target)], close_fds=True)
        return True
    except OSError as error:
        logger.warning("Could not open path {}: {}", target, error)
        return False


class ShellApi:
    """Thread-safe bridge between the two webview windows and Python state."""

    def __init__(
        self,
        runtime: DshRuntime,
        mcp: McpConfigManager,
        skills: SkillsManager,
        *,
        request_quit: Callable[[], None] | None = None,
    ) -> None:
        self.runtime = runtime
        self.mcp = mcp
        self.skills = skills
        self._request_quit = request_quit
        self._main: webview.Window | None = None
        self._settings: webview.Window | None = None
        self._lock = threading.RLock()
        self._runtime_error: str | None = None
        self._tray_available = True
        self._main_visible = True
        self._main_maximized = False
        self._restore_bounds: tuple[int, int, int, int] | None = None
        self._maximized_bounds: tuple[int, int, int, int] | None = None
        self._linux = sys.platform.startswith("linux")

    def bind(
        self,
        main: webview.Window,
        settings: webview.Window,
        *,
        runtime_error: str | None = None,
    ) -> None:
        self._main = main
        self._settings = settings
        self._runtime_error = runtime_error
        if not self._linux:
            main.events.maximized += self._mark_maximized
            main.events.restored += self._mark_restored

    def _mark_maximized(self) -> None:
        self._main_maximized = True

    def _mark_restored(self) -> None:
        self._main_maximized = False

    def _qt_browser_view(self) -> Any:
        if self._main is None:
            return None
        gui = getattr(self._main, "gui", None)
        instances = getattr(getattr(gui, "BrowserView", None), "instances", {})
        return instances.get(getattr(self._main, "uid", None))

    def _linux_geometry(self) -> tuple[tuple[int, int, int, int], tuple[int, int, int, int]]:
        """Read window and available-screen geometry without pywebview waits."""
        window = self._main
        assert window is not None
        gui = window.gui
        x, y = gui.get_position(window.uid)
        width, height = gui.get_size(window.uid)
        screens = gui.get_screens()
        screen = next(
            (
                item
                for item in screens
                if item.x <= int(x) < item.x + item.width and item.y <= int(y) < item.y + item.height
            ),
            screens[0] if screens else None,
        )
        if screen is None:
            view = self._qt_browser_view()
            rect = getattr(view, "screen", None) if view is not None else None
            if rect is None:
                rect = gui.QtGui.QGuiApplication.primaryScreen().geometry()
            screen_bounds = (int(rect.x()), int(rect.y()), int(rect.width()), int(rect.height()))
        else:
            screen_bounds = (int(screen.x), int(screen.y), int(screen.width), int(screen.height))
        return (int(x), int(y), int(width), int(height)), screen_bounds

    def _linux_apply_bounds(self, bounds: tuple[int, int, int, int]) -> None:
        window = self._main
        assert window is not None
        x, y, width, height = bounds
        gui = window.gui
        gui.move(x, y, window.uid)
        from webview.window import FixPoint

        gui.resize(width, height, window.uid, FixPoint.NORTH | FixPoint.WEST)

    def _linux_set_maximized(self, maximized: bool) -> None:
        """Deterministic maximize for frameless Qt/XWayland windows.

        Qt's setWindowState/showMaximized are ignored or leave the window
        iconified on some Wayland/XWayland hosts.  Resizing to the screen and
        restoring the saved geometry always works for a frameless window.
        """
        window = self._main
        if window is None or maximized == self._main_maximized:
            return
        current, screen = self._linux_geometry()
        if maximized:
            self._restore_bounds = current
            self._maximized_bounds = screen
            self._linux_apply_bounds(screen)
            self._main_maximized = True
        else:
            try:
                window.restore()
            except Exception:
                pass
            if self._restore_bounds is not None:
                self._linux_apply_bounds(self._restore_bounds)
            self._main_maximized = False

    # ------------------------------------------------------------------ status
    def boot_state(self) -> dict[str, Any]:
        return {
            "version": __version__,
            "appName": APP_DISPLAY_NAME,
            "platform": sys.platform,
            "platformLabel": platform.platform(),
            "frozen": bool(getattr(sys, "frozen", False) or "__compiled__" in globals()),
            "dataDir": str(data_dir()),
            "iconPath": str(icon_path()),
            "runtime": self.runtime.info(),
            "runtimeError": self._runtime_error,
        }

    def runtime_status(self) -> dict[str, Any]:
        endpoint = self.runtime.endpoint
        return {
            "healthy": self.runtime.health(),
            "port": endpoint.port if endpoint else None,
            "reused": endpoint.reused if endpoint else False,
            "owned": endpoint.owned if endpoint else False,
            "source": endpoint.source if endpoint else None,
            "profile": endpoint.profile if endpoint else self.runtime.profile,
            "url": endpoint.as_dict()["url"] if endpoint else None,
            "error": self._runtime_error,
        }

    def retry_runtime(self) -> dict[str, Any]:
        with self._lock:
            try:
                endpoint = self.runtime.ensure()
            except DshError as error:
                self._runtime_error = str(error)
                raise
            self._runtime_error = None
            if self._main is not None:
                self._main.load_url(endpoint.url)
            return {"endpoint": endpoint.as_dict(), "runtime": self.runtime.info()}

    def restart_dsh(self) -> dict[str, Any]:
        with self._lock:
            try:
                endpoint = self.runtime.restart()
            except DshError as error:
                self._runtime_error = str(error)
                raise
            self._runtime_error = None
            if self._main is not None:
                self._main.load_url(endpoint.url)
            return {"endpoint": endpoint.as_dict(), "runtime": self.runtime.info()}

    # ------------------------------------------------------------------ MCP
    def list_mcp(self) -> dict[str, Any]:
        return self.mcp.list()

    def save_mcp(self, payload: dict[str, Any]) -> dict[str, Any]:
        result = self.mcp.save_entry(payload)
        result["list"] = self.mcp.list()
        return result

    def delete_mcp(self, entry_id: str) -> dict[str, Any]:
        result = self.mcp.delete_entry(entry_id)
        result["list"] = self.mcp.list()
        return result

    def set_mcp_enabled(self, entry_id: str, enabled: bool) -> dict[str, Any]:
        result = self.mcp.set_enabled(entry_id, bool(enabled))
        result["list"] = self.mcp.list()
        return result

    def save_mcp_raw(self, text: str) -> dict[str, Any]:
        result = self.mcp.save_raw(text)
        result["list"] = self.mcp.list()
        return result

    def set_mcp_profile(self, profile: str) -> dict[str, Any]:
        self.mcp.set_profile(profile)
        return self.mcp.list()

    def open_mcp_file(self) -> bool:
        return _open_path(self.mcp.ensure_file())

    def open_mcp_dir(self) -> bool:
        return _open_path(self.mcp.ensure_file().parent)

    # ------------------------------------------------------------------ skills
    def list_skills(self, include_disabled: bool = True) -> dict[str, Any]:
        return self.skills.list(include_disabled=bool(include_disabled))

    def read_skill(self, skill_id: str) -> dict[str, Any]:
        return self.skills.read(skill_id)

    def save_skill(self, payload: dict[str, Any]) -> dict[str, Any]:
        result = self.skills.save(payload)
        result["list"] = self.skills.list()
        return result

    def delete_skill(self, skill_id: str) -> dict[str, Any]:
        result = self.skills.delete(skill_id)
        result["list"] = self.skills.list()
        return result

    def set_skill_enabled(self, skill_id: str, enabled: bool) -> dict[str, Any]:
        result = self.skills.set_enabled(skill_id, bool(enabled))
        result["list"] = self.skills.list()
        return result

    def open_skills_dir(self) -> bool:
        skills_root().mkdir(parents=True, exist_ok=True)
        return _open_path(skills_root())

    def open_skill_dir(self, skill_id: str) -> bool:
        record = self.skills.read(skill_id)["skill"]
        return _open_path(Path(record["directory"]))

    # ------------------------------------------------------------------ windows
    def get_window_bounds(self) -> dict[str, int] | None:
        window = self._main
        if window is None:
            return None
        try:
            x, y = window.gui.get_position(window.uid)
            width, height = window.gui.get_size(window.uid)
            if x is None or y is None or width is None or height is None:
                return None
            return {"x": int(x), "y": int(y), "width": int(width), "height": int(height)}
        except Exception as error:
            logger.debug("Could not read main window bounds: {}", error)
            return None

    def _windows_show(self, command: int) -> bool:
        if sys.platform != "win32" or self._main is None:
            return False
        try:
            import ctypes

            native = getattr(self._main, "native", None)
            handle = getattr(native, "Handle", None)
            if handle is None:
                handle = getattr(native, "handle", None)
            if handle is None:
                return False
            hwnd = None
            for method_name in ("ToInt64", "ToInt32"):
                method = getattr(handle, method_name, None)
                if callable(method):
                    hwnd = int(method())
                    break
            if hwnd is None:
                hwnd = int(handle)
            if not hwnd:
                return False
            ctypes.windll.user32.ShowWindow(hwnd, command)
            return True
        except Exception as error:
            logger.debug("Win32 ShowWindow failed: {}", error)
            return False

    def prepare_resize(self) -> dict[str, int] | None:
        """Exit maximized state once before an edge-resize drag starts."""
        window = self._main
        if window is None:
            return None
        if self._main_maximized:
            try:
                window.restore()
            except Exception as error:
                logger.debug("Could not restore before resize: {}", error)
            self._main_maximized = False
            self._maximized_bounds = None
            time.sleep(0.15)
        return self.get_window_bounds()

    def move_window(self, x: int, y: int) -> bool:
        window = self._main
        if window is None:
            return False
        try:
            window.gui.move(int(x), int(y), window.uid)
            return True
        except Exception as error:
            logger.debug("Could not move main window: {}", error)
            return False

    def set_window_bounds(self, x: int, y: int, width: int, height: int) -> bool:
        """Resize/move the frameless main window from an HTML resize handle."""
        window = self._main
        if window is None:
            return False
        width = max(900, int(width))
        height = max(600, int(height))
        try:
            # The window manager may have maximized the frameless window on a
            # title-bar double-click.  Qt ignores resize/move until that state
            # is cleared, even if our own flag is out of sync.
            if self._main_maximized or sys.platform == "win32":
                window.restore()
                time.sleep(0.1)
            self._main_maximized = False
            self._maximized_bounds = None
            from webview.window import FixPoint

            gui = window.gui
            gui.resize(width, height, window.uid, FixPoint.NORTH | FixPoint.WEST)
            gui.move(int(x), int(y), window.uid)
            return True
        except Exception as error:
            logger.debug("Could not set main window bounds: {}", error)
            return False

    def open_settings(self) -> bool:
        window = self._settings
        if window is None:
            return False
        try:
            window.show()
            window.restore()
            return True
        except Exception as error:
            logger.warning("Could not show settings window: {}", error)
            return False

    def close_settings(self) -> bool:
        if self._settings is not None:
            self._settings.hide()
        return True

    def set_tray_available(self, available: bool) -> bool:
        self._tray_available = bool(available)
        return self._tray_available

    def main_action(self, action: str) -> bool:
        window = self._main
        if window is None:
            return False
        action = str(action)
        try:
            if action == "toggle":
                return self.main_action("hide" if self._main_visible else "show")
            if action == "show":
                window.show()
                self._main_visible = True
                if self._linux:
                    if self._main_maximized:
                        # Avoid a WM-dependent jump to the primary monitor:
                        # return to the saved normal geometry instead.
                        self._linux_set_maximized(False)
                        try:
                            window.evaluate_js(
                                "window.__dshShellRefreshWindowState && "
                                "window.__dshShellRefreshWindowState()"
                            )
                        except Exception:
                            pass
                else:
                    window.restore()
            elif action == "hide":
                window.hide()
                self._main_visible = False
            elif action == "close":
                if self._tray_available:
                    window.hide()
                    self._main_visible = False
                else:
                    self.quit_app()
            elif action == "minimize":
                if self._linux:
                    window.hide()
                    self._main_visible = False
                elif self._windows_show(6):  # SW_MINIMIZE works while maximized
                    pass
                else:
                    window.minimize()
            elif action == "maximize":
                if self._linux:
                    self._linux_set_maximized(True)
                elif self._windows_show(3):  # SW_MAXIMIZE
                    self._main_maximized = True
                else:
                    window.maximize()
                    self._main_maximized = True
            elif action == "restore":
                if self._linux:
                    self._linux_set_maximized(False)
                elif self._windows_show(9):  # SW_RESTORE
                    self._main_maximized = False
                else:
                    window.restore()
                    self._main_maximized = False
            elif action == "toggle_maximize":
                if self._linux:
                    self._linux_set_maximized(not self._main_maximized)
                elif self._main_maximized:
                    if self._windows_show(9):
                        self._main_maximized = False
                    else:
                        window.restore()
                        self._main_maximized = False
                else:
                    if self._windows_show(3):
                        self._main_maximized = True
                    else:
                        window.maximize()
                        self._main_maximized = True
            elif action == "reload":
                return self.reload_main()
            elif action == "quit":
                self.quit_app()
            else:
                logger.warning("Unknown main window action: {}", action)
                return False
            return True
        except Exception as error:
            logger.opt(exception=True).warning("Window action {} failed: {}", action, error)
            return False

    def reload_main(self) -> bool:
        window = self._main
        if window is None:
            return False
        try:
            window.evaluate_js("window.location.reload()")
            return True
        except Exception:
            endpoint = self.runtime.endpoint
            if endpoint:
                window.load_url(endpoint.url)
                return True
            return False

    def open_external(self, url: str = "") -> bool:
        target = str(url or "").strip()
        if not target and self.runtime.endpoint is not None:
            target = self.runtime.endpoint.url
        split = urlsplit(target)
        if split.scheme not in ("http", "https"):
            return False
        webbrowser.open(target)
        return True

    def quit_app(self) -> bool:
        if self._request_quit is not None:
            self._request_quit()
            return True
        for window in (self._settings, self._main):
            if window is not None:
                try:
                    window.destroy()
                except Exception:
                    pass
        return True

    # ------------------------------------------------------------------ support
    def log_client_error(self, message: str) -> bool:
        logger.warning("UI error: {}", str(message)[:2000])
        return True

    def window_state(self) -> dict[str, Any]:
        state = self._main.state if self._main is not None else {}
        return {
            "main": state,
            "maximized": bool(self._main_maximized),
            "settingsVisible": bool(self._settings is not None),
            "icon": str(icon_path()),
        }
