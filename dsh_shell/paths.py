"""Filesystem locations used by the shell.

The shell deliberately knows nothing about the DeepSeek Harness internals.  It
only understands the documented on-disk surfaces: ``$DSH_HOME`` profiles and
``$DSH_AGENTS_HOME/skills`` (``~/.agents/skills`` by default).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "dsh-shell"
APP_DISPLAY_NAME = "dsh-shell"
APP_VENDOR = "dsh-shell"

# Repository root in development; extraction root in a Nuitka onefile build.
_ROOT = Path(__file__).resolve().parent.parent

ICON_NAMES = ("icon.png", "icon.ico")


def resource_path(*parts: str) -> Path:
    """Return a bundled resource (ui/, assets/) in dev and frozen builds."""
    return _ROOT.joinpath(*parts)


def _xdg_dir(env_name: str, fallback: Path) -> Path:
    value = os.environ.get(env_name)
    return Path(value).expanduser() if value else fallback


def data_dir() -> Path:
    """Per-user writable state (logs, runtime endpoint, trash)."""
    if sys.platform == "win32":
        base = _xdg_dir("APPDATA", Path.home() / "AppData" / "Roaming")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = _xdg_dir("XDG_DATA_HOME", Path.home() / ".local" / "share")
    return base / APP_NAME


def cache_dir() -> Path:
    if sys.platform == "win32":
        base = _xdg_dir("LOCALAPPDATA", Path.home() / "AppData" / "Local")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Caches"
    else:
        base = _xdg_dir("XDG_CACHE_HOME", Path.home() / ".cache")
    return base / APP_NAME


def logs_dir() -> Path:
    return data_dir() / "logs"


def runtime_file() -> Path:
    return data_dir() / "runtime.json"


def trash_dir() -> Path:
    return data_dir() / "trash"


def ensure_data_dirs() -> None:
    for path in (data_dir(), logs_dir(), trash_dir()):
        path.mkdir(parents=True, exist_ok=True)


def dsh_home() -> Path:
    value = os.environ.get("DSH_HOME")
    return Path(value).expanduser() if value else Path.home() / ".dsh"


def agents_home() -> Path:
    value = os.environ.get("DSH_AGENTS_HOME")
    return Path(value).expanduser() if value else Path.home() / ".agents"


def skills_root() -> Path:
    return agents_home() / "skills"


def disabled_skills_root() -> Path:
    return agents_home() / "skills-disabled"


def profiles_root() -> Path:
    return dsh_home() / "profiles"


def profile_patch_file(profile: str) -> Path:
    return profiles_root() / profile / "cordis.patch.yml"


def icon_path() -> Path:
    """Prefer the PNG on Linux/macOS and the multi-size ICO on Windows."""
    if sys.platform == "win32":
        candidates = ("assets/icon.ico", "assets/icon.png")
    else:
        candidates = ("assets/icon.png", "assets/icon.ico")
    for candidate in candidates:
        path = resource_path(candidate)
        if path.is_file():
            return path
    return resource_path("assets", "icon.png")
