"""dsh-shell package."""

from __future__ import annotations

import os
import sys

if sys.platform.startswith("linux"):
    # QtWebEngine's GPU sandbox/Vulkan fallback can spin and make the renderer
    # unresponsive on X11/VM hosts.  These must be set before PySide6 loads.
    os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--disable-gpu --no-sandbox")
    os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")
    # Native Wayland ignores programmatic unminimize (setWindowState/NoState)
    # for frameless Qt windows.  XWayland is available on Wayland desktops that
    # expose DISPLAY and gives deterministic minimize/restore behavior.
    if os.environ.get("WAYLAND_DISPLAY") and os.environ.get("DISPLAY"):
        os.environ.setdefault("QT_QPA_PLATFORM", "xcb")
