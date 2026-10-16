# SPDX-FileCopyrightText: 2026 Zepidem
# SPDX-License-Identifier: GPL-3.0-only
"""Read-only Windows viewport tracking. No game hooks, injection or memory reads."""
import ctypes as ct
from ctypes import wintypes as wt
from dataclasses import dataclass
import os
import queue
import threading
import time


class ProcessEntry(ct.Structure):
    _fields_=[('size',wt.DWORD),('usage',wt.DWORD),('pid',wt.DWORD),
              ('heap',ct.c_size_t),('module',wt.DWORD),('threads',wt.DWORD),
              ('parent',wt.DWORD),('priority',wt.LONG),('flags',wt.DWORD),('exe',wt.WCHAR*260)]


@dataclass(frozen=True)
class Viewport:
    x: int
    y: int
    width: int
    height: int
    minimized: bool = False
    foreground: bool = True
    obscured: bool = False


def fit_hud(viewport, size, offset):
    """Clamp position and dimensions to client bounds, including negative monitors."""
    w, h = min(size[0], viewport.width), min(size[1], viewport.height)
    x = viewport.x + max(0, min(offset[0], viewport.width - w))
    y = viewport.y + max(0, min(offset[1], viewport.height - h))
    return int(x), int(y), max(1, int(w)), max(1, int(h))


def hud_visible(viewport, own_foreground=False, preview=False):
    return preview or viewport is None or (not viewport.minimized and
                                          not viewport.obscured and
                                          (viewport.foreground or own_foreground))


def enable_dpi_awareness():
    if os.name == 'nt':
        try:
            api = ct.WinDLL('user32', use_last_error=True)
            api.SetProcessDpiAwarenessContext.argtypes = [ct.c_void_p]
            api.SetProcessDpiAwarenessContext.restype = wt.BOOL
            api.SetProcessDpiAwarenessContext(ct.c_void_p(-4))
        except (AttributeError, OSError):
            pass


class GameWindow:
    def __init__(self):
        self.hwnd = None
        self.next_scan = 0
        self.overlay_error_code = 0
        self.region_error_code = 0
        self._regions = {}
        self._edit_handle = None
        self.user = ct.WinDLL('user32', use_last_error=True) if os.name == 'nt' else None
        if not self.user:
            return
        u = self.user
        self.callback_type = ct.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
        signatures = {
            'EnumWindows': ([self.callback_type, wt.LPARAM], wt.BOOL),
            'IsWindow': ([wt.HWND], wt.BOOL),
            'IsWindowVisible': ([wt.HWND], wt.BOOL),
            'IsIconic': ([wt.HWND], wt.BOOL),
            'GetClientRect': ([wt.HWND, ct.POINTER(wt.RECT)], wt.BOOL),
            'ClientToScreen': ([wt.HWND, ct.POINTER(wt.POINT)], wt.BOOL),
            'GetForegroundWindow': ([], wt.HWND),
            'GetWindowThreadProcessId': ([wt.HWND, ct.POINTER(wt.DWORD)], wt.DWORD),
            'GetAncestor': ([wt.HWND, wt.UINT], wt.HWND),
            'GetWindowLongW': ([wt.HWND, ct.c_int], wt.LONG),
            'SetWindowLongW': ([wt.HWND, ct.c_int, wt.LONG], wt.LONG),
            'SetWindowPos': ([wt.HWND, wt.HWND, ct.c_int, ct.c_int, ct.c_int,
                              ct.c_int, wt.UINT], wt.BOOL),
            'SetWindowRgn': ([wt.HWND, wt.HANDLE, wt.BOOL], ct.c_int),
            'IsWindowEnabled': ([wt.HWND], wt.BOOL),
            'GetAsyncKeyState': ([ct.c_int], ct.c_short),
            'SetForegroundWindow': ([wt.HWND], wt.BOOL),
        }
        for name, (args, result) in signatures.items():
            method = getattr(u, name); method.argtypes = args; method.restype = result
        self.kernel = ct.WinDLL('kernel32', use_last_error=True)
        self.kernel.CreateToolhelp32Snapshot.argtypes=[wt.DWORD,wt.DWORD]
        self.kernel.CreateToolhelp32Snapshot.restype=wt.HANDLE
        for method in (self.kernel.Process32FirstW,self.kernel.Process32NextW):
            method.argtypes=[wt.HANDLE,ct.POINTER(ProcessEntry)];method.restype=wt.BOOL
        self.kernel.CloseHandle.argtypes = [wt.HANDLE]
        self.kernel.CloseHandle.restype = wt.BOOL
        self.game_pids=set()
        self.gdi = ct.WinDLL('gdi32', use_last_error=True)
        self.gdi.CreateRoundRectRgn.argtypes = [ct.c_int] * 6
        self.gdi.CreateRoundRectRgn.restype = wt.HANDLE
        self.gdi.DeleteObject.argtypes = [wt.HANDLE]
        self.gdi.DeleteObject.restype = wt.BOOL

    def own_foreground(self):
        if not self.user:
            return True
        pid = wt.DWORD()
        self.user.GetWindowThreadProcessId(self.user.GetForegroundWindow(), ct.byref(pid))
        return pid.value == os.getpid()

    def _is_game(self, hwnd):
        pid = wt.DWORD()
        self.user.GetWindowThreadProcessId(hwnd, ct.byref(pid))
        return pid.value in self.game_pids

    def _game_pids(self):
        # Process names from the system snapshot; do not open the protected game.
        handle=self.kernel.CreateToolhelp32Snapshot(2,0)
        if handle in (None,ct.c_void_p(-1).value):return set()
        found=set()
        try:
            entry=ProcessEntry();entry.size=ct.sizeof(entry)
            more=self.kernel.Process32FirstW(handle,ct.byref(entry))
            while more:
                if entry.exe.lower() in {'aion2.exe','aion2-win64-shipping.exe'}:found.add(entry.pid)
                more=self.kernel.Process32NextW(handle,ct.byref(entry))
            return found
        finally:
            self.kernel.CloseHandle(handle)

    def passive_overlay(self, tk_handle):
        """Style our own HUD so showing/clicking it does not activate over the game."""
        return self.overlay_interaction(tk_handle, interactive=True)

    def _own_overlay_handle(self, tk_handle):
        """Resolve Tk's top-level wrapper, and refuse foreign HWNDs."""
        if not self.user:
            return None
        wrapper = self.user.GetAncestor(tk_handle, 2)  # GA_ROOT, not root owner.
        if not wrapper or not self.user.IsWindow(wrapper):
            return None
        pid = wt.DWORD()
        self.user.GetWindowThreadProcessId(wrapper, ct.byref(pid))
        return wrapper if pid.value == os.getpid() else None

    def overlay_interaction(self, tk_handle, interactive):
        """Toggle click-through on our HWND; never modify the game window.

        The same-frame inventory and dialogs are game pixels, not child HWNDs.
        A layered, transparent overlay lets Windows route clicks to the game.
        Interactive adjustment clears transparency and NOACTIVATE so native
        controls can receive focus and the game's cursor capture can release.
        """
        wrapper = self._own_overlay_handle(tk_handle)
        if not wrapper:
            self.overlay_error_code = 6  # ERROR_INVALID_HANDLE / foreign HWND.
            return False
        ct.set_last_error(0)
        current = self.user.GetWindowLongW(wrapper, -20)
        if not current and ct.get_last_error():
            self.overlay_error_code = ct.get_last_error()
            return False
        # The HUD stays out of Alt+Tab even while adjustment receives focus.
        # NOACTIVATE changes with interaction, TOOLWINDOW never does.
        flags = (current | 0x00080000 | 0x80) & ~0x00040000
        flags = flags & ~(0x20 | 0x08000000) if interactive else flags | 0x20 | 0x08000000
        if flags != current:
            ct.set_last_error(0)
            prior = self.user.SetWindowLongW(wrapper, -20, flags)
            if not prior and ct.get_last_error():
                self.overlay_error_code = ct.get_last_error()
                return False
            # Notify the frame without moving, reordering or activating it.
            if not self.user.SetWindowPos(wrapper, None, 0, 0, 0, 0, 0x37):
                self.overlay_error_code = ct.get_last_error() or 1
                return False
        actual = self.user.GetWindowLongW(wrapper, -20)
        required = 0x00080000 | 0x80 | (0 if interactive else 0x20 | 0x08000000)
        success = (actual & required == required and not actual & 0x00040000
                   and (not interactive or not actual & (0x20 | 0x08000000)))
        self.overlay_error_code = 0 if success else (ct.get_last_error() or 1)
        return success

    def edit_overlay_focus(self,tk_handle,editing):
        """A user-held modifier lends focus to our HUD, then returns it safely.

        No game input is synthesized. Never steal focus from another app, a
        panel, or another game window. Windows can decline foreground changes.
        """
        wrapper=self._own_overlay_handle(tk_handle)
        if not wrapper:return False
        foreground=self.user.GetForegroundWindow()
        if editing:
            if self._edit_handle==wrapper:return True
            if foreground==self.hwnd and self.user.IsWindow(self.hwnd):
                if self.user.SetForegroundWindow(wrapper):self._edit_handle=wrapper;return True
            return foreground==wrapper
        previous=self._edit_handle;self._edit_handle=None
        if previous==wrapper and foreground==wrapper and self.hwnd and self.user.IsWindow(self.hwnd):
            return bool(self.user.SetForegroundWindow(self.hwnd))
        return False

    def adjustment_held(self):
        """Read only Ctrl+Alt modifier state; no hook or input recording."""
        return bool(self.user and self.user.GetAsyncKeyState(0x11) & 0x8000 and
                    self.user.GetAsyncKeyState(0x12) & 0x8000)

    def hint_dismiss_pressed(self):
        """Only Escape/mouse state while the startup hint is visible; no hook."""
        if not self.user:return False
        states=[self.user.GetAsyncKeyState(key) for key in (0x1B,0x01,0x02,0x04)]
        return any(state & 0x8001 for state in states)

    def rounded_overlay(self, tk_handle, width, height, radius=16):
        """Round only our HUD's region, reusing geometry to avoid GDI churn.

        On success Windows takes ownership of HRGN. On failure we delete it.
        A failed geometry is also cached; retries require a size/radius change.
        """
        wrapper = self._own_overlay_handle(tk_handle)
        if not wrapper:
            self.region_error_code = 6
            return False
        width, height = max(1, int(width)), max(1, int(height))
        radius = max(0, min(int(radius), width // 2, height // 2))
        geometry = width, height, radius
        cached = self._regions.get(wrapper)
        if cached and cached[0] == geometry:
            return cached[1]
        region = self.gdi.CreateRoundRectRgn(0, 0, width + 1, height + 1,
                                             radius * 2, radius * 2)
        success = False
        if region:
            success = bool(self.user.SetWindowRgn(wrapper, region, True))
            if not success:
                self.region_error_code = ct.get_last_error() or 1
                self.gdi.DeleteObject(region)
        else:
            self.region_error_code = ct.get_last_error() or 1
        if success:
            self.region_error_code = 0
        self._regions[wrapper] = geometry, success
        # Only a few app-owned popups need regions; avoid retaining stale HWNDs.
        while len(self._regions) > 3:
            self._regions.pop(next(iter(self._regions)))
        return success

    def viewport(self):
        u = self.user
        if not u:
            return None
        if self.hwnd and not u.IsWindow(self.hwnd):
            self.hwnd = None
        if not self.hwnd and time.monotonic() >= self.next_scan:
            self.next_scan = time.monotonic() + 2
            self.game_pids=self._game_pids()
            candidates = []
            def visit(hwnd, data):
                if u.IsWindowVisible(hwnd) and self._is_game(hwnd):
                    rect = wt.RECT()
                    if u.GetClientRect(hwnd, ct.byref(rect)) and rect.right > 300:
                        candidates.append((rect.right * rect.bottom, hwnd))
                return True
            u.EnumWindows(self.callback_type(visit), 0)
            if candidates:
                self.hwnd = max(candidates)[1]
        if not self.hwnd:
            return None
        if u.IsIconic(self.hwnd):
            return Viewport(0, 0, 1, 1, minimized=True, foreground=False)
        rect = wt.RECT(); origin = wt.POINT(0, 0)
        if not u.GetClientRect(self.hwnd, ct.byref(rect)) or not u.ClientToScreen(self.hwnd, ct.byref(origin)):
            return None
        if rect.right <= 0 or rect.bottom <= 0:
            return Viewport(0, 0, 1, 1, minimized=True, foreground=False)
        foreground = u.GetForegroundWindow()
        separate_game_window = bool(foreground and foreground != self.hwnd and
                                    self._is_game(foreground))
        return Viewport(origin.x, origin.y, rect.right, rect.bottom,
                        foreground=foreground == self.hwnd,
                        obscured=separate_game_window or not u.IsWindowEnabled(self.hwnd))

    def screen_scope(self):
        """Identify only the visible, tracked AION window and its process.

        This uses the same executable-name inventory as viewport tracking. A
        title match or an unrelated foreground window cannot seed pet data.
        No protected process is opened and no game input is sent.
        """
        view = self.viewport()
        if (not self.user or not view or view.minimized or not view.foreground
                or view.obscured or not self.hwnd):
            return None
        hwnd = self.hwnd
        u = self.user
        if (not u.IsWindow(hwnd) or not u.IsWindowVisible(hwnd)
                or not u.IsWindowEnabled(hwnd)
                or u.GetForegroundWindow() != hwnd):
            return None
        pid = wt.DWORD()
        u.GetWindowThreadProcessId(hwnd, ct.byref(pid))
        if not pid.value or pid.value not in self.game_pids:
            return None
        # Recheck after obtaining the PID: a focus/window change must cancel
        # rather than bind an image to the next foreground application.
        if self.hwnd != hwnd or u.GetForegroundWindow() != hwnd:
            return None
        return dict(process_id=int(pid.value), window_id=int(hwnd))


from native_shortcuts import PanelHotkey
