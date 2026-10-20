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
    # The PySide6 wheels only ship the "compose" and "ibus" input-context
    # plugins.  The distro fcitx5 plugin is linked against the distro Qt and
    # cannot be loaded by the bundled Qt (private-API symbol mismatch), and
    # "wayland" is unavailable under the forced xcb platform, so a session
    # QT_IM_MODULES of "wayland;fcitx" leaves QtWebEngine with compose only --
    # no Chinese/Japanese/Korean input.  fcitx5 runs an IBus frontend (it owns
    # org.freedesktop.IBus when it is the session IME), so fall back to the
    # bundled IBus plugin whenever the requested modules are none we ship.
    _shipped_im_modules = {"ibus", "compose", "qtvirtualkeyboard"}
    _requested_im = [
        module.strip()
        for module in (
            os.environ.get("QT_IM_MODULES") or os.environ.get("QT_IM_MODULE") or ""
        ).split(";")
        if module.strip()
    ]
    if not any(module in _shipped_im_modules for module in _requested_im):
        os.environ["QT_IM_MODULES"] = "ibus"
        os.environ["QT_IM_MODULE"] = "ibus"
