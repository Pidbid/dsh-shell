"""Native Windows helpers for frameless window behavior.

pywebview renders ``frameless=True`` windows on Windows as WinForms forms with
``FormBorderStyle.None``.  Such a form has no native caption and, when it is
maximized directly, WinForms uses the whole monitor rectangle instead of the
desktop working area.  The helpers here add the missing native style bits and
make maximize operations explicitly use the current monitor's working area.
"""

from __future__ import annotations

import ctypes
import platform
from ctypes import wintypes
from typing import Any

from loguru import logger

GWL_STYLE = -16
WS_THICKFRAME = 0x00040000
WS_MAXIMIZEBOX = 0x00010000
WS_MINIMIZEBOX = 0x00020000
WS_SYSMENU = 0x00080000
WS_CAPTION = 0x00C00000
WS_CLIPCHILDREN = 0x02000000

WM_NCCALCSIZE = 0x0083
WM_NCDESTROY = 0x0082
WM_GETMINMAXINFO = 0x0024
MONITOR_DEFAULTTONEAREST = 2

# SWP_NOMOVE | SWP_NOSIZE | SWP_FRAMECHANGED | SWP_NOZORDER |
# SWP_NOACTIVATE | SWP_NOOWNERZORDER
SWP_FLAGS = 0x0001 | 0x0002 | 0x0020 | 0x0004 | 0x0010 | 0x0200

LONG_PTR = ctypes.c_longlong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_long
_windows_frame_callbacks: dict[int, Any] = {}


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


class MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", RECT),
        ("rcWork", RECT),
        ("dwFlags", wintypes.DWORD),
    ]


class MINMAXINFO(ctypes.Structure):
    _fields_ = [
        ("ptReserved", wintypes.POINT),
        ("ptMaxSize", wintypes.POINT),
        ("ptMaxPosition", wintypes.POINT),
        ("ptMinTrackSize", wintypes.POINT),
        ("ptMaxTrackSize", wintypes.POINT),
    ]


def _build_frameless_window_style(current_style: int, *, enable_resize: bool) -> int:
    """Rebuild a frameless style while preserving native min/max capabilities."""
    style = (
        int(current_style) | WS_CLIPCHILDREN | WS_MINIMIZEBOX | WS_SYSMENU
    ) & ~WS_CAPTION
    if enable_resize:
        return style | WS_THICKFRAME | WS_MAXIMIZEBOX
    return style & ~(WS_THICKFRAME | WS_MAXIMIZEBOX)


def _apply_work_area_to_minmax_info(info: MONITORINFO, limits: MINMAXINFO) -> None:
    """Constrain a ``WM_GETMINMAXINFO`` payload to the monitor working area."""
    work_width = int(info.rcWork.right) - int(info.rcWork.left)
    work_height = int(info.rcWork.bottom) - int(info.rcWork.top)
    limits.ptMaxPosition.x = int(info.rcWork.left) - int(info.rcMonitor.left)
    limits.ptMaxPosition.y = int(info.rcWork.top) - int(info.rcMonitor.top)
    limits.ptMaxSize.x = work_width
    limits.ptMaxSize.y = work_height
    # Some WinForms versions also consult the tracking limits.  Never let a
    # smaller default pull the maximized window back to the full monitor.
    limits.ptMaxTrackSize.x = max(int(limits.ptMaxTrackSize.x), work_width)
    limits.ptMaxTrackSize.y = max(int(limits.ptMaxTrackSize.y), work_height)


def _get_window_hwnd(window: Any) -> int | None:
    """Return the WinForms window handle without scanning unrelated windows."""
    native = getattr(window, "native", None)
    handle = getattr(native, "Handle", None)
    if handle is not None:
        for method_name in ("ToInt64", "ToInt32"):
            method = getattr(handle, method_name, None)
            if callable(method):
                hwnd = int(method())
                if hwnd:
                    return hwnd

    for owner in (native, getattr(window, "gui", None), window):
        hwnd = getattr(owner, "handle", None)
        if hwnd:
            return int(hwnd)
    return None


def _get_window_long_functions() -> tuple[Any, Any, Any]:
    user32 = ctypes.windll.user32
    if ctypes.sizeof(ctypes.c_void_p) == 8 and hasattr(user32, "GetWindowLongPtrW"):
        get_window_long = user32.GetWindowLongPtrW
        set_window_long = user32.SetWindowLongPtrW
        value_type = LONG_PTR
    else:
        get_window_long = user32.GetWindowLongW
        set_window_long = user32.SetWindowLongW
        value_type = ctypes.c_long

    get_window_long.argtypes = [wintypes.HWND, ctypes.c_int]
    get_window_long.restype = value_type
    set_window_long.argtypes = [wintypes.HWND, ctypes.c_int, value_type]
    set_window_long.restype = value_type
    return get_window_long, set_window_long, value_type


def _set_window_pos_frame_changed(hwnd: int) -> bool:
    set_window_pos = ctypes.WINFUNCTYPE(
        wintypes.BOOL,
        wintypes.HWND,
        wintypes.HWND,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_uint,
    )(("SetWindowPos", ctypes.windll.user32))
    return bool(set_window_pos(hwnd, 0, 0, 0, 0, 0, SWP_FLAGS))


def _install_windows_frameless_frame(hwnd: int) -> None:
    """Handle native frame messages needed by a frameless resizable window."""
    if hwnd in _windows_frame_callbacks:
        return

    subclass_type = ctypes.WINFUNCTYPE(
        LONG_PTR,
        wintypes.HWND,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
        ctypes.c_size_t,
        ctypes.c_size_t,
    )
    comctl32 = ctypes.windll.comctl32
    install = comctl32.SetWindowSubclass
    install.argtypes = [
        wintypes.HWND,
        subclass_type,
        ctypes.c_size_t,
        ctypes.c_size_t,
    ]
    install.restype = wintypes.BOOL
    remove = comctl32.RemoveWindowSubclass
    remove.argtypes = [wintypes.HWND, subclass_type, ctypes.c_size_t]
    remove.restype = wintypes.BOOL
    default_proc = comctl32.DefSubclassProc
    default_proc.argtypes = [
        wintypes.HWND,
        wintypes.UINT,
        wintypes.WPARAM,
        wintypes.LPARAM,
    ]
    default_proc.restype = LONG_PTR
    user32 = ctypes.windll.user32
    monitor_from_window = user32.MonitorFromWindow
    monitor_from_window.argtypes = [wintypes.HWND, wintypes.DWORD]
    monitor_from_window.restype = wintypes.HANDLE
    get_monitor_info = user32.GetMonitorInfoW
    get_monitor_info.argtypes = [wintypes.HANDLE, ctypes.POINTER(MONITORINFO)]
    get_monitor_info.restype = wintypes.BOOL

    @subclass_type
    def frame_proc(handle, message, wparam, lparam, subclass_id, _data):
        if message == WM_NCCALCSIZE:
            # Keep the WebView2 client area equal to the frameless form size.
            return 0
        if message == WM_GETMINMAXINFO:
            result = default_proc(handle, message, wparam, lparam)
            info = MONITORINFO()
            info.cbSize = ctypes.sizeof(info)
            monitor = monitor_from_window(handle, MONITOR_DEFAULTTONEAREST)
            if get_monitor_info(monitor, ctypes.byref(info)):
                limits = ctypes.cast(lparam, ctypes.POINTER(MINMAXINFO)).contents
                _apply_work_area_to_minmax_info(info, limits)
                return 0
            return result
        if message == WM_NCDESTROY:
            remove(handle, frame_proc, subclass_id)
            try:
                return default_proc(handle, message, wparam, lparam)
            finally:
                _windows_frame_callbacks.pop(handle, None)
        return default_proc(handle, message, wparam, lparam)

    # before_show runs on the WinForms UI thread. Keep the callback alive until
    # WM_NCDESTROY; installing it from another thread is not safe.
    if not install(hwnd, frame_proc, 1, 0):
        raise RuntimeError("安装 Windows 无边框窗口处理失败")
    _windows_frame_callbacks[hwnd] = frame_proc


def _apply_windows_frameless_style(window: Any, *, enable_resize: bool) -> bool:
    if platform.system() != "Windows":
        return False
    hwnd = _get_window_hwnd(window)
    if hwnd is None:
        return False
    try:
        _install_windows_frameless_frame(hwnd)
        get_window_long, set_window_long, value_type = _get_window_long_functions()
        current_style = get_window_long(hwnd, GWL_STYLE)
        new_style = _build_frameless_window_style(
            current_style,
            enable_resize=enable_resize,
        )
        set_window_long(hwnd, GWL_STYLE, value_type(new_style))
        _set_window_pos_frame_changed(hwnd)
        return True
    except Exception as error:
        logger.warning("应用 Windows 无边框窗口样式失败: {}", error)
        return False


def attach_windows_frameless_window(window: Any, *, enable_resize: bool = True) -> bool:
    """Apply frameless native styles when the WinForms window is first shown."""
    if platform.system() != "Windows":
        return False
    events = getattr(window, "events", None)
    before_show = getattr(events, "before_show", None)
    if before_show is None:
        return False

    def apply_styles() -> None:
        _apply_windows_frameless_style(window, enable_resize=enable_resize)

    before_show += apply_styles
    return True


def maximize_window_to_work_area(window: Any) -> bool:
    """Maximize a frameless WinForms window without covering the taskbar.

    ``FormBorderStyle.None`` forms use the full monitor rectangle when
    ``WindowState`` is set to ``Maximized`` directly.  Setting
    ``MaximizedBounds`` from ``Screen.FromControl(native).WorkingArea`` makes
    the result match a normal bordered window.  The bounds must be recomputed
    before every maximize so moving the window to another monitor works too.
    """
    if platform.system() != "Windows":
        return False

    native = getattr(window, "native", None)
    if native is None:
        return False

    try:
        from System import Action
        from System.Windows.Forms import FormWindowState, Screen

        def apply_maximized_bounds() -> None:
            working_area = Screen.FromControl(native).WorkingArea
            native.MaximizedBounds = working_area
            native.WindowState = FormWindowState.Maximized

        if bool(getattr(native, "InvokeRequired", False)):
            native.Invoke(Action(apply_maximized_bounds))
        else:
            apply_maximized_bounds()
        return True
    except Exception as error:
        logger.warning("按显示器工作区最大化窗口失败: {}", error)
        return False
