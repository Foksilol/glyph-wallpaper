#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Иконка в системном трее (Win32 Shell_NotifyIcon), без внешних зависимостей."""
import ctypes
import os
import sys
import threading
from ctypes import wintypes as wt

user32 = ctypes.WinDLL("user32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
WM_COMMAND = 0x0111
WM_RBUTTONUP = 0x0205
WM_LBUTTONDBLCLK = 0x0203
WM_APP = 0x8000
WM_TRAY = WM_APP + 1

NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP = 0x1, 0x2, 0x4
MF_STRING, MF_SEPARATOR, MF_CHECKED = 0x0, 0x800, 0x8
TPM_RETURNCMD, TPM_NONOTIFY = 0x0100, 0x0080
IMAGE_ICON, LR_LOADFROMFILE, LR_DEFAULTSIZE = 1, 0x10, 0x40
IDI_APPLICATION = 32512

CMD_OPEN, CMD_TOGGLE, CMD_AUTOSTART, CMD_EXIT = 1, 2, 3, 4


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wt.DWORD),
        ("hWnd", wt.HWND),
        ("uID", wt.UINT),
        ("uFlags", wt.UINT),
        ("uCallbackMessage", wt.UINT),
        ("hIcon", wt.HICON),
        ("szTip", wt.WCHAR * 128),
        ("dwState", wt.DWORD),
        ("dwStateMask", wt.DWORD),
        ("szInfo", wt.WCHAR * 256),
        ("uVersion", wt.UINT),
        ("szInfoTitle", wt.WCHAR * 64),
        ("dwInfoFlags", wt.DWORD),
        ("guidItem", ctypes.c_byte * 16),
        ("hBalloonIcon", wt.HICON),
    ]


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [("cbSize", wt.UINT), ("style", wt.UINT), ("lpfnWndProc", ctypes.c_void_p),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                ("hInstance", wt.HINSTANCE), ("hIcon", wt.HICON), ("hCursor", wt.HANDLE),
                ("hbrBackground", wt.HBRUSH), ("lpszMenuName", wt.LPCWSTR),
                ("lpszClassName", wt.LPCWSTR), ("hIconSm", wt.HICON)]


class MSG(ctypes.Structure):
    _fields_ = [("hwnd", wt.HWND), ("message", wt.UINT), ("wParam", wt.WPARAM),
                ("lParam", wt.LPARAM), ("time", wt.DWORD), ("pt", wt.POINT)]


WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)

user32.DefWindowProcW.restype = ctypes.c_ssize_t
user32.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.RegisterClassExW.restype = wt.ATOM
user32.CreateWindowExW.restype = wt.HWND
user32.CreateWindowExW.argtypes = [wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD,
                                   ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                   wt.HWND, wt.HMENU, wt.HINSTANCE, wt.LPVOID]
user32.TrackPopupMenu.restype = ctypes.c_int
user32.TrackPopupMenu.argtypes = [wt.HMENU, wt.UINT, ctypes.c_int, ctypes.c_int,
                                  ctypes.c_int, wt.HWND, ctypes.c_void_p]
user32.GetMessageW.argtypes = [ctypes.POINTER(MSG), wt.HWND, wt.UINT, wt.UINT]
user32.GetMessageW.restype = wt.BOOL
user32.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.LoadImageW.restype = wt.HANDLE
user32.LoadImageW.argtypes = [wt.HINSTANCE, wt.LPCWSTR, wt.UINT,
                              ctypes.c_int, ctypes.c_int, wt.UINT]
user32.LoadIconW.restype = wt.HICON
user32.LoadIconW.argtypes = [wt.HINSTANCE, ctypes.c_void_p]
shell32.Shell_NotifyIconW.argtypes = [wt.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]


def _app_dir():
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def load_app_icon():
    path = os.path.join(_app_dir(), "app.ico")
    h = user32.LoadImageW(None, path, IMAGE_ICON, 0, 0,
                          LR_LOADFROMFILE | LR_DEFAULTSIZE)
    if h:
        return h
    large = wt.HICON()
    small = wt.HICON()
    if shell32.ExtractIconExW(sys.executable, 0, ctypes.byref(large),
                              ctypes.byref(small), 1) > 0:
        return small.value or large.value
    return user32.LoadIconW(None, ctypes.c_void_p(IDI_APPLICATION))


class TrayIcon:
    """Иконка в трее с меню. Колбэк on_command(id) вызывается из потока трея."""

    def __init__(self, on_command, is_live=None, is_autostart=None,
                 tooltip="Glyph Wallpaper — живые обои"):
        self.on_command = on_command
        self.is_live = is_live or (lambda: False)
        self.is_autostart = is_autostart or (lambda: False)
        self.tooltip = tooltip
        self.hwnd = None
        self._thread = None
        self._proc = None
        self._nid = None
        self._icon = None

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, name="tray", daemon=True)
        self._thread.start()

    def stop(self):
        if self.hwnd:
            user32.PostMessageW(self.hwnd, WM_CLOSE, 0, 0)
        if self._thread:
            self._thread.join(2.0)

    # ------------------------------------------------------------------
    def _run(self):
        hinst = kernel32.GetModuleHandleW(None)
        cls = f"GlyphTrayWnd_{id(self):x}"
        proc = WNDPROC(self._wndproc)
        self._proc = proc
        wc = WNDCLASSEXW()
        wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
        wc.lpfnWndProc = ctypes.cast(proc, ctypes.c_void_p)
        wc.hInstance = hinst
        wc.lpszClassName = cls
        user32.RegisterClassExW(ctypes.byref(wc))

        self.hwnd = user32.CreateWindowExW(0, cls, "GlyphTray", 0, 0, 0, 0, 0,
                                           None, None, hinst, None)
        if not self.hwnd:
            return

        self._icon = load_app_icon()
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = self.hwnd
        nid.uID = 1
        nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        nid.uCallbackMessage = WM_TRAY
        nid.hIcon = self._icon
        nid.szTip = self.tooltip[:127]
        self._nid = nid
        shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid))

        msg = MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

        shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid))
        self.hwnd = None

    def _menu(self):
        menu = user32.CreatePopupMenu()
        user32.AppendMenuW(menu, MF_STRING, CMD_OPEN, "Открыть настройки")
        live = bool(self.is_live())
        user32.AppendMenuW(menu, MF_STRING, CMD_TOGGLE,
                           "Остановить обои" if live else "Включить обои")
        auto = bool(self.is_autostart())
        user32.AppendMenuW(menu, MF_STRING | (MF_CHECKED if auto else 0),
                           CMD_AUTOSTART, "Автозапуск при входе")
        user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
        user32.AppendMenuW(menu, MF_STRING, CMD_EXIT, "Выход")
        return menu

    def _popup(self):
        menu = self._menu()
        pt = wt.POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        user32.SetForegroundWindow(self.hwnd)
        cmd = user32.TrackPopupMenu(menu, TPM_RETURNCMD | TPM_NONOTIFY,
                                    pt.x, pt.y, 0, self.hwnd, None)
        user32.PostMessageW(self.hwnd, 0, 0, 0)
        user32.DestroyMenu(menu)
        if cmd:
            self.on_command(int(cmd))

    def _wndproc(self, hwnd, msg, wparam, lparam):
        if msg == WM_TRAY:
            event = lparam & 0xFFFF
            if event in (WM_RBUTTONUP, 0x007B):
                self._popup()
            elif event == WM_LBUTTONDBLCLK:
                self.on_command(CMD_OPEN)
            return 0
        if msg == WM_CLOSE:
            user32.DestroyWindow(hwnd)
            return 0
        if msg == WM_DESTROY:
            user32.PostQuitMessage(0)
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)
