#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Живые обои для Windows 10: окно сажается в WorkerW за иконки рабочего стола
и каждый кадр рисует новую картинку из символов (см. anim.py).

    lw = LiveWallpaper(renderer, fps=30)
    lw.start(); ...; lw.stop()
"""
import ctypes
import os
import threading
import time
from ctypes import wintypes as wt

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", wt.UINT), ("dwTime", wt.DWORD)]


user32.GetLastInputInfo.argtypes = [ctypes.POINTER(LASTINPUTINFO)]
user32.GetLastInputInfo.restype = wt.BOOL


def idle_seconds():
    """Сколько секунд пользователь не трогал мышь/клавиатуру."""
    info = LASTINPUTINFO()
    info.cbSize = ctypes.sizeof(LASTINPUTINFO)
    if not user32.GetLastInputInfo(ctypes.byref(info)):
        return 999.0
    return max(0.0, (kernel32.GetTickCount() - info.dwTime) / 1000.0)


def set_low_priority():
    """Ниже обычного приоритета: системе и играм не мешаем."""
    try:
        kernel32.GetCurrentProcess.restype = wt.HANDLE
        kernel32.SetPriorityClass.argtypes = [wt.HANDLE, wt.DWORD]
        kernel32.SetPriorityClass.restype = wt.BOOL
        kernel32.SetPriorityClass(kernel32.GetCurrentProcess(), 0x00004000)
    except Exception:
        pass

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)

user32.DefWindowProcW.restype = LRESULT
user32.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
user32.RegisterClassExW.restype = wt.ATOM
user32.CreateWindowExW.restype = wt.HWND
user32.CreateWindowExW.argtypes = [wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD,
                                   ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                   wt.HWND, wt.HMENU, wt.HINSTANCE, wt.LPVOID]
user32.SetParent.argtypes = [wt.HWND, wt.HWND]
user32.SetWindowPos.argtypes = [wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, wt.UINT]
user32.FindWindowW.restype = wt.HWND
user32.FindWindowW.argtypes = [wt.LPCWSTR, wt.LPCWSTR]
user32.FindWindowExW.restype = wt.HWND
user32.FindWindowExW.argtypes = [wt.HWND, wt.HWND, wt.LPCWSTR, wt.LPCWSTR]
user32.SendMessageTimeoutW.restype = ctypes.c_size_t
user32.SendMessageTimeoutW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM,
                                       wt.UINT, wt.UINT, ctypes.POINTER(ctypes.c_size_t)]
user32.GetDC.restype = wt.HDC
user32.GetDC.argtypes = [wt.HWND]
user32.ReleaseDC.argtypes = [wt.HWND, wt.HDC]
user32.IsWindow.argtypes = [wt.HWND]
user32.IsWindow.restype = wt.BOOL

gdi32.CreateCompatibleDC.restype = wt.HDC
gdi32.CreateCompatibleDC.argtypes = [wt.HDC]
gdi32.CreateDIBSection.restype = wt.HBITMAP
gdi32.CreateDIBSection.argtypes = [wt.HDC, ctypes.c_void_p, wt.UINT,
                                   ctypes.POINTER(ctypes.c_void_p), wt.HANDLE, wt.DWORD]
gdi32.SelectObject.restype = wt.HGDIOBJ
gdi32.SelectObject.argtypes = [wt.HDC, wt.HGDIOBJ]
gdi32.BitBlt.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                         wt.HDC, ctypes.c_int, ctypes.c_int, wt.DWORD]
gdi32.StretchBlt.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                             wt.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                             wt.DWORD]
gdi32.SetStretchBltMode.argtypes = [wt.HDC, ctypes.c_int]
gdi32.DeleteObject.argtypes = [wt.HGDIOBJ]
gdi32.DeleteDC.argtypes = [wt.HDC]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG),
                ("biPlanes", wt.WORD), ("biBitCount", wt.WORD),
                ("biCompression", wt.DWORD), ("biSizeImage", wt.DWORD),
                ("biXPelsPerMeter", wt.LONG), ("biYPelsPerMeter", wt.LONG),
                ("biClrUsed", wt.DWORD), ("biClrImportant", wt.DWORD)]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wt.DWORD * 3)]


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [("cbSize", wt.UINT), ("style", wt.UINT), ("lpfnWndProc", WNDPROC),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                ("hInstance", wt.HINSTANCE), ("hIcon", wt.HICON), ("hCursor", wt.HANDLE),
                ("hbrBackground", wt.HBRUSH), ("lpszMenuName", wt.LPCWSTR),
                ("lpszClassName", wt.LPCWSTR), ("hIconSm", wt.HICON)]


class MSG(ctypes.Structure):
    _fields_ = [("hwnd", wt.HWND), ("message", wt.UINT), ("wParam", wt.WPARAM),
                ("lParam", wt.LPARAM), ("time", wt.DWORD), ("pt", wt.POINT)]


WM_ERASEBKGND = 0x0014
WM_NCHITTEST = 0x0084
HTTRANSPARENT = -1
WS_POPUP = 0x80000000
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOOLWINDOW = 0x00000080
HWND_TOP = wt.HWND(0)
SMTO_NORMAL = 0
SRCCOPY = 0x00CC0020
HALFTONE = 4
COLORONCOLOR = 3
PM_REMOVE = 0x0001
SPI_SETDESKWALLPAPER = 0x0014
SPI_GETDESKWALLPAPER = 0x0073
SPIF_UPDATEINIFILE = 0x01
SPIF_SENDCHANGE = 0x02

_spawned = False
_host_cache = []


def _classname(hwnd):
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def find_wallpaper_host(force=False):
    """WorkerW, который лежит ЗА иконками рабочего стола (Windows 10)."""
    global _spawned
    if not force and _host_cache and user32.IsWindow(_host_cache[0]):
        return _host_cache[0]

    progman = user32.FindWindowW("Progman", None)
    if not progman:
        raise RuntimeError("не найден Progman — это не оболочка Windows?")

    if not _spawned or force:
        res = ctypes.c_size_t()
        for wp, lp in ((0xD, 1), (0xD, 0), (0, 0)):
            user32.SendMessageTimeoutW(progman, 0x052C, wp, lp, SMTO_NORMAL, 1500,
                                       ctypes.byref(res))
        _spawned = True

    hosts = []
    enum_proc = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)

    def cb(hwnd, _):
        if user32.FindWindowExW(hwnd, None, "SHELLDLL_DefView", None):
            hosts.append(hwnd)
        return True

    user32.EnumWindows(enum_proc(cb), 0)

    target = None
    for host in hosts:
        w = user32.FindWindowExW(None, host, "WorkerW", None)
        if w:
            target = w
            break
    if target is None and hosts:
        target = hosts[0]
    if target is None:
        target = progman
    _host_cache[:] = [target]
    return target


def desktop_rect():
    gsm = user32.GetSystemMetrics
    return gsm(78), gsm(79)


def desktop_origin():
    gsm = user32.GetSystemMetrics
    return gsm(76), gsm(77)


def enable_dpi_awareness():
    try:
        ctypes.windll.ShCore.SetProcessDpiAwareness(2)   # PER_MONITOR_AWARE
        return
    except Exception:
        pass
    try:
        user32.SetProcessDPIAware()
    except Exception:
        pass


def create_dib(w, h):
    """32-битная DIB-секция: (hbitmap, указатель на пиксели, число байт)."""
    bmi = BITMAPINFO()
    bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bmi.bmiHeader.biWidth = w
    bmi.bmiHeader.biHeight = -h
    bmi.bmiHeader.biPlanes = 1
    bmi.bmiHeader.biBitCount = 32
    bmi.bmiHeader.biCompression = 0
    bits = ctypes.c_void_p()
    hbmp = gdi32.CreateDIBSection(None, ctypes.byref(bmi), 0, ctypes.byref(bits), None, 0)
    if not hbmp:
        raise ctypes.WinError(ctypes.get_last_error())
    ctypes.memset(bits, 255, w * h * 4)
    return hbmp, bits, w * h * 4


user32.EnumDisplayMonitors.restype = wt.BOOL
user32.EnumDisplayMonitors.argtypes = [wt.HDC, ctypes.POINTER(wt.RECT), ctypes.c_void_p, wt.LPARAM]
user32.GetMonitorInfoW.restype = wt.BOOL
user32.GetMonitorInfoW.argtypes = [wt.HMONITOR, ctypes.c_void_p]
user32.GetForegroundWindow.restype = wt.HWND
user32.IsIconic.argtypes = [wt.HWND]
user32.IsWindowVisible.argtypes = [wt.HWND]
user32.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]

user32.GetCursorPos.argtypes = [ctypes.POINTER(wt.POINT)]
user32.RedrawWindow.argtypes = [wt.HWND, ctypes.c_void_p, wt.HRGN, wt.UINT]
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.restype = ctypes.c_short
VK_RBUTTON = 0x02
user32.WindowFromPoint.restype = wt.HWND
user32.WindowFromPoint.argtypes = [wt.POINT]
user32.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]

DESKTOP_CLASSES = ("Progman", "WorkerW", "SHELLDLL_DefView", "SysListView32")


def _cursor_over_desktop():
    pt = wt.POINT()
    if not user32.GetCursorPos(ctypes.byref(pt)):
        return False, 0, 0
    hwnd = user32.WindowFromPoint(pt)
    name = ""
    if hwnd:
        buf = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, buf, 256)
        name = buf.value
    over = name in DESKTOP_CLASSES or name.startswith("GlyphLiveWallpaper")
    return over, pt.x, pt.y


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("rcMonitor", wt.RECT),
                ("rcWork", wt.RECT), ("dwFlags", wt.DWORD)]


def enumerate_monitors():
    """Список (x, y, w, h) физических мониторов в координатах экрана."""
    monitors = []
    enum_proc = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HMONITOR, wt.HDC,
                                   ctypes.POINTER(wt.RECT), wt.LPARAM)

    def cb(hmon, hdc, lprect, lparam):
        mi = MONITORINFO()
        mi.cbSize = ctypes.sizeof(MONITORINFO)
        if user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
            r = mi.rcMonitor
            monitors.append((r.left, r.top, r.right - r.left, r.bottom - r.top))
        return True

    user32.EnumDisplayMonitors(None, None, enum_proc(cb), 0)
    if not monitors:
        W, H = desktop_rect()
        monitors.append((0, 0, W, H))
    return monitors


EXCLUDE_CLASSES = {"Progman", "WorkerW", "SHELLDLL_DefView", "SysListView32",
                   "Shell_TrayWnd", "Shell_SecondaryTrayWnd",
                   "Windows.UI.Core.CoreWindow", "ForegroundStaging"}


def foreground_fullscreen_monitor(monitors, our_windows):
    """Индекс монитора, целиком закрытого активным окном (иначе -1)."""
    hwnd = user32.GetForegroundWindow()
    if not hwnd or hwnd in our_windows:
        return -1
    if user32.IsIconic(hwnd) or not user32.IsWindowVisible(hwnd):
        return -1
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    name = buf.value
    if name in EXCLUDE_CLASSES or name.startswith("GlyphLiveWallpaper"):
        return -1
    r = wt.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(r)):
        return -1
    for i, (mx, my, mw, mh) in enumerate(monitors):
        tol = 2
        if (r.left <= mx + tol and r.top <= my + tol
                and r.right >= mx + mw - tol and r.bottom >= my + mh - tol):
            return i
    return -1


class LiveWallpaper:
    """Одно окно на монитор: на мониторе с фуллскрин-окном анимация встаёт."""

    def __init__(self, walls, fps=30, on_error=None, idle_fps=4, idle_after=60.0,
                 touch_timeout=20.0):
        self.walls = list(walls)            # [(animator, (x, y, w, h)), ...]
        self.fps = int(fps)
        self.base_fps = int(fps)
        self.idle_fps = int(idle_fps)
        self.idle_after = float(os.environ.get("GLYPH_IDLE_AFTER", idle_after))
        self.touch_timeout = float(os.environ.get("GLYPH_TOUCH_TIMEOUT", touch_timeout))
        self._last_touch = 0.0
        self.on_error = on_error
        self.fps_actual = 0.0
        self.paused = [False] * len(self.walls)
        self._stop = threading.Event()
        self._thread = None
        self._windows = []
        self._proc = None
        self._cls = None
        self.error = None
        self._prev_rb = False
        self._prev_over = False
        self._prev_pos = None
        self._fade_at = None
        self._fade_secs = 0.0
        self._wallpaper = _wallpaper_path()

    # -- публичное ---------------------------------------------------------
    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self.error = None
        self._create_windows()
        self._thread = threading.Thread(target=self._run, name="glyph-live", daemon=True)
        self._thread.start()

    def stop(self, timeout=5.0, fade=0.8):
        """Плавно гасит картинку и убирает окна."""
        if fade > 0 and self.running:
            self._fade_at = time.perf_counter()
            self._fade_secs = float(fade)
        else:
            self._stop.set()
        if self._thread:
            self._thread.join(timeout)
        self._stop.set()
        self._destroy_windows()
        restore_wallpaper(self._wallpaper)

    @property
    def running(self):
        return bool(self._thread and self._thread.is_alive() and not self._stop.is_set())

    # -- окна --------------------------------------------------------------
    def _create_windows(self):
        host = find_wallpaper_host()
        vx, vy = desktop_origin()
        hinst = ctypes.windll.kernel32.GetModuleHandleW(None)
        self._cls = f"GlyphLiveWallpaper_{id(self):x}"
        proc = WNDPROC(self._wndproc)
        self._proc = proc
        wc = WNDCLASSEXW()
        wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
        wc.style = 0
        wc.lpfnWndProc = proc
        wc.hInstance = hinst
        wc.hbrBackground = None
        wc.lpszClassName = self._cls
        if not user32.RegisterClassExW(ctypes.byref(wc)):
            raise ctypes.WinError(ctypes.get_last_error())

        self._windows = []
        for _animator, (mx, my, mw, mh) in self.walls:
            hwnd = user32.CreateWindowExW(
                WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW, self._cls, "glyph wallpaper",
                WS_POPUP, 0, 0, mw, mh, None, None, hinst, None)
            if not hwnd:
                raise ctypes.WinError(ctypes.get_last_error())
            user32.SetParent(hwnd, host)
            user32.SetWindowPos(hwnd, HWND_TOP, mx - vx, my - vy, mw, mh,
                                0x0010 | 0x0040)
            self._windows.append(hwnd)

    def _destroy_windows(self):
        for hwnd in self._windows:
            try:
                user32.ShowWindow(hwnd, 0)      # SW_HIDE: без чёрной вспышки
            except Exception:
                pass
            user32.DestroyWindow(hwnd)
        self._windows = []
        if self._cls:
            user32.UnregisterClassW(self._cls, ctypes.windll.kernel32.GetModuleHandleW(None))
            self._cls = None

    # -- отрисовка ---------------------------------------------------------
    def _run(self):
        try:
            self._paint_loop()
        except Exception as exc:            # noqa: BLE001
            self.error = exc
            if os.environ.get("GLYPH_DEBUG"):
                import traceback
                traceback.print_exc()
            if self.on_error:
                self.on_error(exc)

    def _paint_loop(self):
        vx, vy = desktop_origin()
        W, H = desktop_rect()
        animator = self.walls[0][0]
        rw, rh = animator.W, animator.H
        kx = rw / float(max(1, W))
        ky = rh / float(max(1, H))
        monitors = [(x, y, w, h) for _, (x, y, w, h) in self.walls]
        our = set(self._windows)

        prep = []
        for i, (_anim, (mx, my, mw, mh)) in enumerate(self.walls):
            hwnd = self._windows[i]
            hdc = user32.GetDC(hwnd)
            if not hdc:
                raise ctypes.WinError(ctypes.get_last_error())
            gdi32.SetStretchBltMode(hdc, COLORONCOLOR)
            sx = max(0, min(rw - 1, int((mx - vx) * kx)))
            sy = max(0, min(rh - 1, int((my - vy) * ky)))
            sw = max(1, min(rw - sx, int(round(mw * kx))))
            sh = max(1, min(rh - sy, int(round(mh * ky))))
            prep.append(dict(hwnd=hwnd, hdc=hdc, rect=(mx, my, mw, mh),
                             src=(sx, sy, sw, sh)))

        hbmp, bits, nbytes = create_dib(rw, rh)
        mdc = gdi32.CreateCompatibleDC(prep[0]["hdc"])
        old = gdi32.SelectObject(mdc, hbmp)
        msg = MSG()
        t0 = time.perf_counter()
        frame = 0
        ema = 0.0
        try:
            while not self._stop.is_set():
                now = time.perf_counter()
                t = now - t0
                fade_k = None
                if self._fade_at is not None:
                    fade_k = 1.0 - (now - self._fade_at) / max(1e-3, self._fade_secs)
                    if fade_k <= 0.0:
                        break

                full = foreground_fullscreen_monitor(monitors, our)
                over, cx, cy = _cursor_over_desktop()
                if over:
                    self._last_touch = t
                frozen = (t - self._last_touch) > self.touch_timeout
                awake = bool(getattr(animator, "keep_awake", False))
                if awake:
                    frozen = False
                skip_render = (len(monitors) == 1 and full == 0 and not awake) or frozen

                if not skip_render:
                    animator.set_cursor((cx - vx) / max(1, W), (cy - vy) / max(1, H), over)
                    rb = bool(user32.GetAsyncKeyState(VK_RBUTTON) & 0x8000)
                    pressed = rb and not self._prev_rb
                    if pressed and self._prev_over:
                        px, py = self._prev_pos or (cx, cy)
                        animator.trigger_wave((px - vx) / max(1, W), (py - vy) / max(1, H))
                    self._prev_rb = rb
                    self._prev_over = over
                    self._prev_pos = (cx, cy)

                    img = animator.frame(t)
                    if fade_k is not None and fade_k < 1.0:
                        img = img.point(lambda v, kk=fade_k: int(v * kk))
                    data = img.tobytes("raw", "BGRX")
                    ctypes.memmove(bits, data, min(len(data), nbytes))

                for i, p in enumerate(prep):
                    paused = ((i == full) and not awake) or frozen
                    self.paused[i] = paused
                    if paused:
                        continue
                    sx, sy, sw, sh = p["src"]
                    mx, my, mw, mh = p["rect"]
                    if (sw, sh) == (mw, mh):
                        gdi32.BitBlt(p["hdc"], 0, 0, mw, mh, mdc, sx, sy, SRCCOPY)
                    else:
                        gdi32.StretchBlt(p["hdc"], 0, 0, mw, mh, mdc,
                                         sx, sy, sw, sh, SRCCOPY)

                while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_REMOVE):
                    user32.TranslateMessage(ctypes.byref(msg))
                    user32.DispatchMessageW(ctypes.byref(msg))

                frame += 1
                if frame % 30 == 0:
                    if not all(user32.IsWindow(h) for h in self._windows):
                        raise RuntimeError("рабочий стол был перезапущен")

                dt = time.perf_counter() - now
                target = self.base_fps if (idle_seconds() < self.idle_after or awake) else self.idle_fps
                if frozen and self._fade_at is None:
                    target = 10          # редко опрашиваем курсор, пока обои спят
                self.fps = target
                time.sleep(max(0.0, 1.0 / target - dt))
                period = time.perf_counter() - now
                ema = period if ema == 0 else ema * 0.9 + period * 0.1
                self.fps_actual = 1.0 / ema if ema else 0.0
        finally:
            gdi32.SelectObject(mdc, old)
            gdi32.DeleteObject(hbmp)
            gdi32.DeleteDC(mdc)
            for p in prep:
                user32.ReleaseDC(p["hwnd"], p["hdc"])

    def _wndproc(self, hwnd, msg, wparam, lparam):
        if msg == WM_ERASEBKGND:
            return 1
        if msg == WM_NCHITTEST:
            return HTTRANSPARENT
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)


def _wallpaper_path():
    """Текущие обои пользователя (путь к картинке), чтобы вернуть их при выходе."""
    buf = ctypes.create_unicode_buffer(1024)
    try:
        user32.SystemParametersInfoW(SPI_GETDESKWALLPAPER, 1024, buf, 0)
    except Exception:
        return ""
    return buf.value or ""


def restore_wallpaper(path):
    """Перерисовывает рабочий стол и возвращает обои пользователя."""
    refresh_desktop()
    if path and os.path.exists(path):
        try:
            user32.SystemParametersInfoW(SPI_SETDESKWALLPAPER, 0, path,
                                         SPIF_UPDATEINIFILE | SPIF_SENDCHANGE)
        except Exception:
            pass


def refresh_desktop():
    """Заставляет проводник перерисовать рабочий стол (возврат своих обоев)."""
    RDW = 0x0001 | 0x0004 | 0x0080 | 0x0100   # INVALIDATE|ERASE|ALLCHILDREN|UPDATENOW
    hwnds = [user32.FindWindowW("Progman", None)]
    w = user32.FindWindowExW(None, None, "WorkerW", None)
    while w:
        hwnds.append(w)
        w = user32.FindWindowExW(None, w, "WorkerW", None)
    for h in hwnds:
        if h:
            try:
                user32.RedrawWindow(h, None, None, RDW)
            except Exception:
                pass


def set_static_wallpaper(path):
    ok = user32.SystemParametersInfoW(SPI_SETDESKWALLPAPER, 0, str(path),
                                      SPIF_UPDATEINIFILE | SPIF_SENDCHANGE)
    if not ok:
        raise ctypes.WinError(ctypes.get_last_error())
