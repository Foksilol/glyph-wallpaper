#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Glyph Wallpaper — живые обои из символов для Windows.

Запуск:  python app.py
Сборка:  см. build.ps1
"""
import json
import os
import queue
import random
import sys
import threading
import time
import tkinter as tk
import winreg
from tkinter import messagebox, ttk

from PIL import Image, ImageTk

import anim
import engine
import glyph_render as gr
import numpy as np
import tray

APP_NAME = "GlyphWallpaper"
CONFIG_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), APP_NAME)
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")

# дизайны: ключ -> подпись, стиль, палитра, seed, cell(px при 2160p), режим движения
DESIGNS = [
    ("flow-cyan",     "Кибер-жилы",      "flow",    "midnight-cyan",  1841, 22, "drift"),
    ("field-cyan",    "Гравюра (циан)",  "field",   "midnight-cyan",  1777, 21, "drift"),
    ("matrix-green",  "Матрица",         "matrix",  "terminal-green",   99, 28, "rain"),
    ("orbit-noir",    "Интерференция",   "orbit",   "magenta-noir",    320, 21, "zoom"),
    ("flow-amber",    "Янтарные жилы",   "flow",    "amber-terminal", 4211, 21, "drift"),
    ("field-ember",   "Медная гравюра",  "field",   "ember-carbon",    812, 20, "drift"),
    ("flow-paper",    "Бумага и тушь",   "flow",    "paper-ink",      2718, 26, "drift"),
    ("field-paper",   "Офорт",           "field",   "paper-ink",       640, 21, "drift"),
    ("fire",          "Огонь",           "fire",    "fire",            707, 19, "drift"),
]
DESIGN_BY_KEY = {d[0]: d for d in DESIGNS}
MOTIONS = [("drift", "Дрейф"), ("rain", "Дождь"), ("zoom", "Зум")]
CURSOR_LABELS = {"all": "всё вместе", "magnet": "магнит", "aura": "аура (отталкивает)",
                 "glow": "свечение"}
CURSOR_MODES = {v: k for k, v in CURSOR_LABELS.items()}
PALETTE_LABELS = {
    "midnight-cyan": "циан",
    "ember-carbon": "уголь",
    "magenta-noir": "магента",
    "terminal-green": "зелёный",
    "amber-terminal": "янтарь",
    "paper-ink": "бумага",
    "fire": "огонь",
}
PALETTE_NAMES = {v: k for k, v in PALETTE_LABELS.items()}
TWEAKS = {
    "matrix-green": dict(haze=1.4, bloom=1.15, strength=1.22),
    "field-ember": dict(bloom=0.7, haze=0.6, gain=1.35),
    "field-cyan": dict(bloom=0.8, haze=0.7, gain=1.20),
    "field-paper": dict(bloom=0.5, haze=0.4, gain=1.30),
}

BG = "#101014"
PANEL = "#17171d"
FG = "#e8e8ee"
MUTED = "#8d8d99"
ACCENT = "#35d8cf"


def log(msg):
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(os.path.join(CONFIG_DIR, "app.log"), "a", encoding="utf-8") as fh:
            fh.write(f"{time.strftime('%H:%M:%S')} {msg}\n")
    except Exception:
        pass


RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "GlyphWallpaper"


def autostart_command():
    """Команда, которую прописываем в автозапуск."""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" --live --tray'
    pyw = sys.executable.replace("python.exe", "pythonw.exe")
    return f'"{pyw}" "{os.path.abspath(sys.argv[0])}" --live --tray'


def autostart_enabled():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            value, _ = winreg.QueryValueEx(key, RUN_VALUE)
            return bool(value)
    except OSError:
        return False


def _running_from_temp():
    """Запуск из архива/временной папки (WinRAR, zip-preview): автозапуск ставить нельзя."""
    exe = os.path.normcase(getattr(sys, "executable", "") or "")
    return any(mark in exe for mark in
               ("\\temp\\", "\\tmp\\", "rartemp", "\\appdata\\local\\temp\\"))


def clean_stale_autostart():
    """Убирает автозапись на несуществующий exe (например, после распаковки в Temp)."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            value, _ = winreg.QueryValueEx(key, RUN_VALUE)
    except OSError:
        return
    path = value.strip().strip('"').split('"')[0].split(" --")[0]
    if path and not os.path.exists(path):
        log(f"autostart points to missing file, removing: {path}")
        set_autostart(False)


def set_autostart(enabled):
    if enabled and getattr(sys, "frozen", False) and _running_from_temp():
        log("autostart skipped: running from a temporary folder")
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                            winreg.KEY_SET_VALUE) as key:
            if enabled:
                winreg.SetValueEx(key, RUN_VALUE, 0, winreg.REG_SZ, autostart_command())
            else:
                try:
                    winreg.DeleteValue(key, RUN_VALUE)
                except FileNotFoundError:
                    pass
        return True
    except OSError as exc:
        log(f"autostart failed: {exc!r}")
        return False


def load_config():
    try:
        with open(CONFIG_PATH, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def save_config(cfg):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, ensure_ascii=False, indent=2)


def render_design(key, seed, w, h, cell_override=None, palette=None, hue=0.0):
    """Рендерит кадр обоев нужного размера (без записи на диск)."""
    _, _, style, design_palette, _, cell, _ = DESIGN_BY_KEY[key]
    cell = cell_override if cell_override is not None else cell * h / 2160.0
    img = gr.render(style, palette or design_palette, seed, w, h, cell,
                    out_path=None, verbose=False,
                    ramp_name="katakana" if style == "matrix" else "dense",
                    tweak=TWEAKS.get(key))
    if hue:
        img = apply_hue(img, hue)
    return img


def apply_hue(img, deg):
    m = anim._hue_matrix(deg)
    arr = np.asarray(img.convert("RGB"), np.float32) @ m.T
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGB")


class App(tk.Tk):
    def __init__(self, auto_live=False):
        super().__init__()
        self.title("Glyph Wallpaper")
        self.configure(bg=BG)
        self.geometry("900x680")
        self.minsize(860, 640)
        self.resizable(False, True)

        cfg = load_config()
        self.design_key = cfg.get("design", "flow-cyan")
        if self.design_key not in DESIGN_BY_KEY:
            self.design_key = "flow-cyan"
        self.seed = int(cfg.get("seed", DESIGN_BY_KEY[self.design_key][4]))
        self.speed = float(cfg.get("speed", 1.0))
        self.cursor_on = bool(cfg.get("cursor_on", True))
        self.cursor_mode = cfg.get("cursor_mode", "all")
        if self.cursor_mode not in CURSOR_LABELS:
            self.cursor_mode = "all"
        self.cursor_strength = float(cfg.get("cursor_strength", 1.0))
        self.snow = bool(cfg.get("snow", False))
        self.show_clock = bool(cfg.get("clock", False))
        self.palette_name = cfg.get("palette", DESIGN_BY_KEY[self.design_key][3])
        if self.palette_name not in PALETTE_LABELS:
            self.palette_name = DESIGN_BY_KEY[self.design_key][3]
        self.hue = float(cfg.get("hue", 0.0))
        self.live = None
        self.animators = []
        self.worker = None
        self.q = queue.Queue()
        self._preview_job = None
        self._busy = False
        self._photo = None

        engine.enable_dpi_awareness()
        self._build_style()
        self._build_ui()
        self.after(120, self._poll_queue)
        self.after(200, self.refresh_preview)
        if auto_live:
            self.after(1500, self.toggle_live)
        self.protocol("WM_DELETE_WINDOW", self.hide_to_tray)

        clean_stale_autostart()      # запись на удалённый exe больше не нужна

        if not cfg.get("autostart_set"):
            ok = set_autostart(True)
            self.autostart_var.set(autostart_enabled())
            if ok:                   # из временной папки не прописываем — попробуем позже
                cfg["autostart_set"] = True
            save_config(cfg)
        elif self.autostart_var.get():
            set_autostart(True)      # обновляем путь, если переехали/пересобрали
        self.tray = tray.TrayIcon(
            self._tray_command,
            is_live=lambda: bool(self.live and self.live.running),
            is_autostart=lambda: bool(self.autostart_var.get()))
        self.tray.start()

    # ---------------------------------------------------------------- UI ---
    def _build_style(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(".", background=PANEL, foreground=FG, fieldbackground="#22222a",
                        bordercolor="#2c2c36", focuscolor=ACCENT)
        style.configure("TFrame", background=BG)
        style.configure("Card.TFrame", background=PANEL)
        style.configure("TLabel", background=BG, foreground=FG, font=("Segoe UI", 10))
        style.configure("Muted.TLabel", background=BG, foreground=MUTED, font=("Segoe UI", 9))
        style.configure("Head.TLabel", background=BG, foreground=FG, font=("Segoe UI Semibold", 15))
        style.configure("TButton", font=("Segoe UI", 10), padding=6)
        style.configure("Accent.TButton", background=ACCENT, foreground="#06201e",
                        font=("Segoe UI Semibold", 10))
        style.map("Accent.TButton", background=[("active", "#5fe8e0"), ("disabled", "#2a4a48")])
        style.configure("TCombobox", padding=4, fieldbackground="#22222a",
                        background="#22222a", foreground=FG, arrowcolor=FG)
        style.map("TCombobox",
                  fieldbackground=[("readonly", "#22222a"), ("disabled", "#1a1a20")],
                  foreground=[("readonly", FG), ("disabled", MUTED)],
                  selectbackground=[("readonly", "#22222a")],
                  selectforeground=[("readonly", FG)],
                  background=[("readonly", "#22222a")])
        style.configure("TEntry", fieldbackground="#22222a", foreground=FG,
                        insertcolor=FG, padding=4)
        self.option_add("*TCombobox*Listbox.background", "#1c1c24")
        self.option_add("*TCombobox*Listbox.foreground", FG)
        self.option_add("*TCombobox*Listbox.selectBackground", "#2f6f6a")
        self.option_add("*TCombobox*Listbox.font", "{Segoe UI} 10")
        style.configure("TCheckbutton", background=BG, foreground=FG)
        style.configure("TScale", background=BG)

    def _build_ui(self):
        root = ttk.Frame(self, style="TFrame", padding=18)
        root.pack(fill="both", expand=True)

        head = ttk.Frame(root)
        head.pack(fill="x")
        ttk.Label(head, text="Glyph Wallpaper", style="Head.TLabel").pack(side="left")
        ttk.Label(head, text="живые обои из символов", style="Muted.TLabel").pack(
            side="left", padx=(10, 0), pady=(6, 0))

        body = ttk.Frame(root)
        body.pack(fill="both", expand=True, pady=(14, 0))

        # --- превью
        left = tk.Frame(body, bg=PANEL, highlightthickness=0)
        left.pack(side="left", anchor="n")
        self.preview = tk.Label(left, bg="#0a0a0c", bd=0)
        self.preview.pack(padx=2, pady=2)
        self.preview_note = ttk.Label(left, text="превью 960×540", style="Muted.TLabel")
        self.preview_note.pack(pady=(6, 8))

        # --- настройки
        right = ttk.Frame(body)
        right.pack(side="left", fill="both", expand=True, padx=(18, 0))

        ttk.Label(right, text="Дизайн").pack(anchor="w")
        self.design_var = tk.StringVar(value=DESIGN_BY_KEY[self.design_key][1])
        self.design_box = ttk.Combobox(right, textvariable=self.design_var, state="readonly",
                                       values=[d[1] for d in DESIGNS], width=30)
        self.design_box.pack(fill="x", pady=(4, 12))
        self.design_box.bind("<<ComboboxSelected>>", self._on_design)

        row = ttk.Frame(right)
        row.pack(fill="x")
        ttk.Label(row, text="Seed").pack(side="left")
        self.seed_var = tk.StringVar(value=str(self.seed))
        self.seed_entry = ttk.Entry(row, textvariable=self.seed_var, width=12)
        self.seed_entry.pack(side="left", padx=(8, 6))
        self.seed_entry.bind("<Return>", lambda e: self.refresh_preview())
        ttk.Button(row, text="🎲", width=3, command=self.random_seed).pack(side="left")
        ttk.Label(row, text="  скорость").pack(side="left", padx=(12, 6))
        self.speed_var = tk.DoubleVar(value=self.speed)
        ttk.Scale(row, from_=0.2, to=3.0, variable=self.speed_var, length=120,
                  command=lambda v: None).pack(side="left")
        self.speed_note = ttk.Label(row, text=f"×{self.speed:.1f}", style="Muted.TLabel")
        self.speed_note.pack(side="left", padx=(6, 0))
        self.speed_var.trace_add("write", self._on_speed)

        cur = ttk.Frame(right)
        cur.pack(fill="x", pady=(14, 0))
        self.cursor_var = tk.BooleanVar(value=self.cursor_on)
        ttk.Checkbutton(cur, text="Курсор", variable=self.cursor_var,
                        command=self._on_cursor).pack(side="left")
        self.cursor_box = ttk.Combobox(cur, state="readonly", width=11,
                                       values=list(CURSOR_LABELS.values()))
        self.cursor_box.set(CURSOR_LABELS[self.cursor_mode])
        self.cursor_box.pack(side="left", padx=(10, 6))
        self.cursor_box.bind("<<ComboboxSelected>>", self._on_cursor)
        self.cursor_strength_var = tk.DoubleVar(value=self.cursor_strength)
        ttk.Scale(cur, from_=0.2, to=2.0, variable=self.cursor_strength_var,
                  length=80, command=lambda v: self._on_cursor()).pack(side="left")

        col = ttk.Frame(right)
        col.pack(fill="x", pady=(10, 0))
        ttk.Label(col, text="Цвет").pack(side="left")
        self.palette_box = ttk.Combobox(col, state="readonly", width=11,
                                        values=list(PALETTE_LABELS.values()))
        self.palette_box.set(PALETTE_LABELS.get(self.palette_name, "циан"))
        self.palette_box.pack(side="left", padx=(8, 6))
        self.palette_box.bind("<<ComboboxSelected>>", self._on_palette)
        self.hue_var = tk.DoubleVar(value=self.hue)
        ttk.Scale(col, from_=0, to=360, variable=self.hue_var, length=100,
                  command=lambda v: self._on_hue()).pack(side="left")
        self.hue_note = ttk.Label(col, text=f"{int(self.hue)}°", style="Muted.TLabel")
        self.hue_note.pack(side="left", padx=(6, 0))

        snow_row = ttk.Frame(right)
        snow_row.pack(fill="x", pady=(10, 0))
        self.snow_var = tk.BooleanVar(value=self.snow)
        ttk.Checkbutton(snow_row, text="Снег (копится внизу)",
                        variable=self.snow_var,
                        command=self._on_snow).pack(side="left")

        trow = ttk.Frame(right)
        trow.pack(fill="x", pady=(10, 0))
        self.clock_var = tk.BooleanVar(value=self.show_clock)
        ttk.Checkbutton(trow, text="Часы", variable=self.clock_var,
                        command=self._on_clock).pack(side="left")
        ttk.Label(trow, text="  таймер").pack(side="left")
        self.timer_var = tk.StringVar(value="")
        ttk.Entry(trow, textvariable=self.timer_var, width=5).pack(side="left", padx=4)
        ttk.Label(trow, text="мин").pack(side="left")
        ttk.Button(trow, text="▶", width=3, command=self._on_timer_start).pack(
            side="left", padx=(6, 2))
        ttk.Button(trow, text="×", width=3, command=self._on_timer_stop).pack(side="left")
        self.timer_note = ttk.Label(trow, text="", style="Muted.TLabel")
        self.timer_note.pack(side="left", padx=(6, 0))

        self.run_btn = ttk.Button(right, text="▶  Включить обои", style="Accent.TButton",
                                  command=self.toggle_live)
        self.run_btn.pack(fill="x", ipady=5)
        ttk.Button(right, text="Сделать статичными (PNG + обои Windows)",
                   command=self.set_static).pack(fill="x", pady=(8, 0), ipady=3)
        ttk.Button(right, text="Сохранить 4K PNG", command=self.save_4k).pack(
            fill="x", pady=(8, 0), ipady=3)

        self.progress = ttk.Progressbar(right, mode="indeterminate")
        self.progress.pack(fill="x", pady=(16, 6))
        self.status = ttk.Label(right, text="готов", style="Muted.TLabel", wraplength=330)
        self.status.pack(anchor="w")

        self.autostart_var = tk.BooleanVar(value=autostart_enabled())
        ttk.Checkbutton(right, text="Запускать при входе в Windows",
                        variable=self.autostart_var,
                        command=self._on_autostart).pack(anchor="w", pady=(10, 0))

        foot = ttk.Label(root, style="Muted.TLabel",
                         text="Живой режим рисует обои за значками: ярлыки, меню и "
                              "клики работают как обычно. Крестик сворачивает в трей — "
                              "обои продолжают жить.")
        foot.pack(anchor="w", pady=(12, 0))

    # ------------------------------------------------------------ helpers ---
    def _each(self, fn):
        for a in getattr(self, "animators", []):
            try:
                fn(a)
            except Exception:
                pass

    def _on_speed(self, *_):
        self.speed = float(self.speed_var.get())
        self.speed_note.configure(text=f"×{self.speed:.1f}")
        if self.live:
            self._each(lambda a: setattr(a, "speed", self.speed))

    def _on_cursor(self, *_):
        self.cursor_on = bool(self.cursor_var.get())
        self.cursor_mode = CURSOR_MODES.get(self.cursor_box.get(), "all")
        self.cursor_strength = float(self.cursor_strength_var.get())
        self._each(lambda a: a.set_effect(self.cursor_mode, self.cursor_strength,
                                          self.cursor_on))
        self._save_cfg()

    def _on_snow(self, *_):
        self.snow = bool(self.snow_var.get())
        self._each(lambda a: a.set_snow(self.snow))
        self._save_cfg()

    def _on_palette(self, *_):
        self.palette_name = PALETTE_NAMES.get(self.palette_box.get(), self.palette_name)
        self._each(lambda a: a.set_palette(self.palette_name))
        self._save_cfg()
        self.refresh_preview()

    def _on_hue(self, *_):
        self.hue = float(self.hue_var.get())
        self.hue_note.configure(text=f"{int(self.hue)}°")
        self._each(lambda a: a.set_hue(self.hue))
        self._save_cfg()
        if getattr(self, "_hue_job", None):
            try:
                self.after_cancel(self._hue_job)
            except Exception:
                pass
        self._hue_job = self.after(350, self.refresh_preview)

    def _on_clock(self, *_):
        self.show_clock = bool(self.clock_var.get())
        self._each(lambda a: a.set_clock(self.show_clock))
        self._save_cfg()

    def _on_timer_start(self, *_):
        try:
            minutes = float(str(self.timer_var.get()).replace(",", "."))
        except ValueError:
            minutes = 0.0
        if minutes <= 0:
            return
        self._each(lambda a: a.set_timer(minutes * 60.0))
        self.timer_note.configure(text=f"{minutes:g} мин")
        self.set_status("таймер идёт — обои не заснут")

    def _on_timer_stop(self, *_):
        self._each(lambda a: a.set_timer(None))
        self.timer_note.configure(text="")

    def _save_cfg(self):
        save_config({"design": self.design_key, "seed": self.seed, "speed": self.speed,
                     "cursor_on": self.cursor_on, "cursor_mode": self.cursor_mode,
                     "cursor_strength": self.cursor_strength,
                     "palette": self.palette_name, "hue": self.hue,
                     "snow": self.snow, "clock": self.show_clock,
                     "autostart_set": True})

    def _on_design(self, *_):
        key = next(k for k, label, *_ in DESIGNS if label == self.design_var.get())
        self.design_key = key
        self.seed = DESIGN_BY_KEY[key][4]
        self.seed_var.set(str(self.seed))
        self.palette_name = DESIGN_BY_KEY[key][3]
        self.palette_box.set(PALETTE_LABELS[self.palette_name])
        self._each(lambda a: a.set_palette(self.palette_name))
        self.refresh_preview()

    def random_seed(self):
        self.seed = random.randint(1, 999999)
        self.seed_var.set(str(self.seed))
        self.refresh_preview()

    def set_status(self, text, busy=False):
        self.status.configure(text=text)
        self._busy = busy
        if busy:
            self.progress.start(12)
        else:
            self.progress.stop()

    def refresh_preview(self):
        try:
            self.seed = int(self.seed_var.get())
        except ValueError:
            self.seed = 1
        key, seed = self.design_key, self.seed
        self.set_status("рисую превью…", busy=True)
        threading.Thread(target=self._preview_worker,
                         args=(key, seed, self.palette_name, self.hue),
                         daemon=True).start()

    def _preview_worker(self, key, seed, palette, hue):
        try:
            img = render_design(key, seed, 960, 540, palette=palette, hue=hue)
            self.q.put(("preview", img))
        except Exception as exc:      # noqa: BLE001
            self.q.put(("error", exc))

    def _poll_queue(self):
        try:
            while True:
                kind, payload = self.q.get_nowait()
                if kind == "preview":
                    self._show_preview(payload)
                    self.set_status("готов")
                elif kind == "static":
                    self.set_status(payload, busy=False)
                elif kind == "tray":
                    self._handle_tray(payload)
                elif kind == "error":
                    self.set_status(f"ошибка: {payload}", busy=False)
                    self.run_btn.configure(state="normal", text="▶  Включить обои")
                    messagebox.showerror(APP_NAME, str(payload))
        except queue.Empty:
            pass
        self.after(120, self._poll_queue)

    def _show_preview(self, img):
        img = img.resize((478, 269), Image.LANCZOS)
        self._photo = ImageTk.PhotoImage(img)
        self.preview.configure(image=self._photo)

    # ------------------------------------------------------------- actions ---
    def toggle_live(self):
        log(f"toggle_live busy={self._busy} live={bool(self.live and self.live.running)}")
        if self.live and self.live.running:
            self.stop_live()
            return
        if self._busy:
            self.after(400, self.toggle_live)      # превью ещё рисуется
            return
        self.run_btn.configure(state="disabled")
        self.set_status("запускаю покадровый рендер…", busy=True)
        self.after(60, self._start_engine)

    def _start_engine(self):
        log("start_engine begin")
        try:
            W, H = engine.desktop_rect()
            vx, vy = engine.desktop_origin()
            key = self.design_key
            _, _, style, palette, _, cell, _ = DESIGN_BY_KEY[key]
            # один холст на весь рабочий стол: узор идёт сквозь стык мониторов
            scale = min(1.0, (1_150_000.0 / max(1, W * H)) ** 0.5)
            rw = max(320, int(W * scale) // 2 * 2)
            rh = max(200, int(H * scale) // 2 * 2)
            a = anim.Animator(style, palette, self.seed, rw, rh, cell,
                              speed=self.speed,
                              ramp="katakana" if style == "matrix"
                              else ("dirs" if style == "field" else "dense"))
            a.set_effect(self.cursor_mode, self.cursor_strength, self.cursor_on)
            a.set_palette(self.palette_name)
            a.set_hue(self.hue)
            a.set_snow(self.snow)
            a.set_clock(self.show_clock)
            _tsec = os.environ.get("GLYPH_TIMER")
            if _tsec:
                try:
                    a.set_timer(float(_tsec))
                except ValueError:
                    pass
            self.animators = [a]
            walls = [(a, r) for r in engine.enumerate_monitors()]
            log(f"animator ok {rw}x{rh} scale={scale:.2f} {style}, monitors={len(walls)}")
            fps = 30 if style in ("matrix", "fire") else 24
            engine.set_low_priority()
            self.live = engine.LiveWallpaper(
                walls, fps=fps,
                on_error=lambda e: self.q.put(("error", e)))
            self.live.start()
            log(f"engine started running={self.live.running} err={self.live.error!r}")
            if self.live.error:
                raise self.live.error
            self.run_btn.configure(state="normal", text="■  Остановить обои")
            self.set_status("обои включены · считаю fps…")
            self._save_cfg()
            self.after(600, self._tick_fps)
        except Exception as exc:      # noqa: BLE001
            log(f"start_engine FAILED: {exc!r}")
            self.run_btn.configure(state="normal")
            self.set_status(f"не удалось включить: {exc}")
            messagebox.showerror(APP_NAME, f"Не удалось включить обои:\n{exc}")

    def _tick_fps(self):
        if self.live and self.live.running:
            extra = ""
            if self.animators:
                left = self.animators[0].timer_left()
                if left is not None:
                    m, s = divmod(int(round(left)), 60)
                    h, m = divmod(m, 60)
                    extra = f" · таймер {h}:{m:02d}:{s:02d}" if h else f" · таймер {m:02d}:{s:02d}"
                elif self.show_clock:
                    extra = " · часы"
            self.set_status(f"обои включены · {self.live.fps_actual:.0f} fps{extra}")
            self.after(800, self._tick_fps)

    def stop_live(self):
        if self.live:
            self.live.stop()
            self.live = None
        self.run_btn.configure(text="▶  Включить обои", state="normal")
        self.set_status("обои остановлены")

    def set_static(self):
        if self._busy:
            return
        self.set_status("генерирую статичные обои…", busy=True)
        key, seed = self.design_key, self.seed
        threading.Thread(target=self._static_worker, args=(key, seed), daemon=True).start()

    def _static_worker(self, key, seed):
        try:
            W, H = engine.desktop_rect()
            img = render_design(key, seed, W, H, palette=self.palette_name, hue=self.hue)
            out_dir = os.path.join(CONFIG_DIR, "output")
            os.makedirs(out_dir, exist_ok=True)
            path = os.path.join(out_dir, f"{key}-{seed}-{W}x{H}.png")
            img.save(path)
            engine.set_static_wallpaper(path)
            self.q.put(("static", f"обои установлены: {path}"))
        except Exception as exc:      # noqa: BLE001
            self.q.put(("error", exc))

    def save_4k(self):
        if self._busy:
            return
        self.set_status("рендерю 3840×2160…", busy=True)
        key, seed = self.design_key, self.seed
        threading.Thread(target=self._save4k_worker, args=(key, seed), daemon=True).start()

    def _save4k_worker(self, key, seed):
        try:
            img = render_design(key, seed, 3840, 2160, palette=self.palette_name,
                                hue=self.hue)
            out_dir = os.path.join(CONFIG_DIR, "output")
            os.makedirs(out_dir, exist_ok=True)
            path = os.path.join(out_dir, f"{key}-{seed}-3840x2160.png")
            img.save(path)
            self.q.put(("static", f"сохранено: {path}"))
        except Exception as exc:      # noqa: BLE001
            self.q.put(("error", exc))

    # --------------------------------------------------------------- трей ---
    def _tray_command(self, cmd):
        self.q.put(("tray", cmd))

    def _handle_tray(self, cmd):
        if cmd == tray.CMD_OPEN:
            self.show_window()
        elif cmd == tray.CMD_TOGGLE:
            self.toggle_live()
        elif cmd == tray.CMD_AUTOSTART:
            self.autostart_var.set(not self.autostart_var.get())
            self._on_autostart()
        elif cmd == tray.CMD_EXIT:
            self.quit_app()

    def _on_autostart(self):
        enabled = bool(self.autostart_var.get())
        ok = set_autostart(enabled)
        self.autostart_var.set(autostart_enabled())
        self.set_status("автозапуск включён" if ok and enabled else
                        ("автозапуск выключен" if ok else "не удалось изменить автозапуск"))

    def show_window(self):
        self.deiconify()
        self.lift()
        self.focus_force()

    def hide_to_tray(self):
        self._save_cfg()
        self.withdraw()

    def quit_app(self):
        self.stop_live()
        if getattr(self, "tray", None):
            self.tray.stop()
        self.destroy()

    def on_close(self):
        self.hide_to_tray()


def main():
    # один экземпляр: второй запуск просто показывает уже открытое окно
    import ctypes
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32.CreateMutexW(None, False, "GlyphWallpaper_SingleInstance")
    if ctypes.get_last_error() == 183:      # ERROR_ALREADY_EXISTS
        # уже запущено (возможно, свёрнуто в трей) — показываем окно
        hwnd = user32.FindWindowW(None, "Glyph Wallpaper")
        if hwnd:
            user32.ShowWindow(hwnd, 5)      # SW_SHOW
            user32.ShowWindow(hwnd, 9)      # SW_RESTORE
            user32.BringWindowToTop(hwnd)
            user32.SetForegroundWindow(hwnd)
        return
    try:
        app = App(auto_live="--live" in sys.argv)
        if "--tray" in sys.argv:
            app.withdraw()
        app.mainloop()
    except Exception:      # noqa: BLE001
        import traceback
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(os.path.join(CONFIG_DIR, "crash.log"), "w", encoding="utf-8") as fh:
            traceback.print_exc(file=fh)
        raise


if __name__ == "__main__":
    main()
