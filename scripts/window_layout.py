"""Shared Win32 screen coordinates for game capture and overlay placement."""

import ctypes
from ctypes import wintypes
from dataclasses import dataclass


CARD_LEFTS = {"hex_1": 0.2539, "hex_2": 0.4414, "hex_3": 0.625}


@dataclass(frozen=True)
class ScreenRect:
    left: int
    top: int
    width: int
    height: int


@dataclass(frozen=True)
class GameLayout:
    hwnd: int
    client: ScreenRect

    @property
    def content(self):
        # 16:9 reference UI, centered when a wider surface is letterboxed
        # horizontally. Keep the original ratios on narrower windows.
        r = self.client
        width = min(r.width, round(r.height * 16 / 9))
        return ScreenRect(r.left + (r.width - width) // 2, r.top, width, r.height)

    def title_region(self, key):
        r = self.content
        return ScreenRect(r.left + int(r.width * CARD_LEFTS[key]),
                          r.top + int(r.height * 0.375),
                          max(1, int(r.width * 0.125)),
                          max(1, int(r.height * 0.0417)))

    def icon_region(self, key):
        """Conservative crop around the artwork above the title."""
        r = self.content
        return ScreenRect(r.left + int(r.width * (CARD_LEFTS[key] + 0.0125)),
                          r.top + int(r.height * 0.195),
                          max(1, int(r.width * 0.10)),
                          max(1, int(r.height * 0.165)))

    def overlay_anchor(self, key):
        title = self.title_region(key)
        return (title.left - self.client.left,
                title.top - self.client.top - round(self.client.height / 12))


def _bind(dll, name, result, *args):
    fn = getattr(dll, name)
    fn.restype, fn.argtypes = result, args
    return fn


_user32 = ctypes.windll.user32
_hwnd, _bool = wintypes.HWND, wintypes.BOOL
_find_window = _bind(_user32, "FindWindowW", _hwnd, wintypes.LPCWSTR, wintypes.LPCWSTR)
_foreground = _bind(_user32, "GetForegroundWindow", _hwnd)
_is_iconic = _bind(_user32, "IsIconic", _bool, _hwnd)
_is_visible = _bind(_user32, "IsWindowVisible", _bool, _hwnd)
_client_rect = _bind(_user32, "GetClientRect", _bool, _hwnd, ctypes.POINTER(wintypes.RECT))
_client_to_screen = _bind(_user32, "ClientToScreen", _bool, _hwnd, ctypes.POINTER(wintypes.POINT))
_ancestor = _bind(_user32, "GetAncestor", _hwnd, _hwnd, wintypes.UINT)
_get_style = _bind(_user32, "GetWindowLongW", wintypes.LONG, _hwnd, ctypes.c_int)
_set_style = _bind(_user32, "SetWindowLongW", wintypes.LONG, _hwnd, ctypes.c_int, wintypes.LONG)
_set_position = _bind(_user32, "SetWindowPos", _bool, _hwnd, _hwnd,
                      ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT)


def enable_per_monitor_dpi():
    """Call before creating Tk windows or initializing screenshot capture."""
    try:
        set_context = _bind(_user32, "SetProcessDpiAwarenessContext", _bool, ctypes.c_void_p)
        if set_context(ctypes.c_void_p(-4)):  # PER_MONITOR_AWARE_V2
            return
    except AttributeError:
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PER_MONITOR_AWARE
    except (AttributeError, OSError):
        _user32.SetProcessDPIAware()


def get_game_layout():
    """Return a fresh snapshot only while the game window can be captured."""
    hwnd = _find_window("RiotWindowClass", "League of Legends (TM) Client")
    if not hwnd or hwnd != _foreground() or _is_iconic(hwnd) or not _is_visible(hwnd):
        return None
    rect = wintypes.RECT()
    if not _client_rect(hwnd, ctypes.byref(rect)):
        return None
    start, end = wintypes.POINT(rect.left, rect.top), wintypes.POINT(rect.right, rect.bottom)
    if not (_client_to_screen(hwnd, ctypes.byref(start)) and _client_to_screen(hwnd, ctypes.byref(end))):
        return None
    width, height = end.x - start.x, end.y - start.y
    if width <= 0 or height <= 0:
        return None
    return GameLayout(hwnd, ScreenRect(start.x, start.y, width, height))


def configure_overlay(tk_hwnd):
    hwnd = _ancestor(tk_hwnd, 2)  # GA_ROOT: Tk's top-level wrapper.
    _set_style(hwnd, -20, _get_style(hwnd, -20) | 0x80000 | 0x20 | 0x08000000)
    return hwnd


def position_overlay(hwnd, rect):
    # Native placement accepts signed screen origins. Tk geometry interprets
    # negative offsets relative to the opposite screen edge.
    return bool(_set_position(hwnd, _hwnd(-1), rect.left, rect.top,
                              rect.width, rect.height, 0x0010))  # SWP_NOACTIVATE
