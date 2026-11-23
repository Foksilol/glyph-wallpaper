#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Покадровый рендер живых обоев из символов.

Animator.frame(t) собирает кадр заново: поле тона пересчитывается для момента t,
глифы выбираются по тону (в «матрице» — перерисовываются), палитра — LUT.
Кадр отдаётся в BGR-порядке, чтобы без конвертации класть его в DIB.
"""
import ctypes
import math
import os
import time as _time
from ctypes import wintypes as wt
from datetime import datetime

import numpy as np
from PIL import Image, ImageDraw, ImageFont

import glyph_render as gr

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_user32.FindWindowW.restype = wt.HWND
_user32.FindWindowW.argtypes = [wt.LPCWSTR, wt.LPCWSTR]


def _ground_fraction():
    """Доля высоты экрана, где «земля»: над панелью задач, если она внизу."""
    try:
        hwnd = _user32.FindWindowW("Shell_TrayWnd", None)
        if hwnd:
            r = wt.RECT()
            _user32.GetWindowRect(hwnd, ctypes.byref(r))
            gsm = _user32.GetSystemMetrics
            H, oy = gsm(79), gsm(77)
            if H > 0 and r.top > oy + H * 0.75:
                return min(1.0, max(0.5, (r.top - oy) / H))
    except Exception:
        pass
    return 1.0

# --------------------------------------------------------------------------
# шум с фиксированными решётками: координаты можно двигать во времени
# --------------------------------------------------------------------------


def _smooth(t):
    return t * t * (3.0 - 2.0 * t)


class Noise:
    def __init__(self, seed, octaves=5, base=2):
        rng = np.random.default_rng(seed)
        self.grids = []
        res = base
        for _ in range(octaves):
            g = rng.random((res, res), dtype=np.float32)
            self.grids.append(np.pad(g, ((0, 1), (0, 1)), mode="wrap"))
            res *= 2

    def _one(self, x, y, res, g):
        fx = x * res
        fy = y * res
        ix = np.floor(fx)
        iy = np.floor(fy)
        tx = _smooth(fx - ix)
        ty = _smooth(fy - iy)
        x0 = ix.astype(np.int32) % res
        y0 = iy.astype(np.int32) % res
        x1 = (x0 + 1) % res
        y1 = (y0 + 1) % res
        a = g[y0, x0]
        b = g[y0, x1]
        c = g[y1, x0]
        d = g[y1, x1]
        top = a + (b - a) * tx
        bot = c + (d - c) * tx
        return top + (bot - top) * ty

    def sample(self, x, y, octaves=None):
        out = np.zeros(x.shape, np.float32)
        amp, norm = 1.0, 0.0
        n = octaves or len(self.grids)
        for i in range(n):
            out += amp * self._one(x, y, 2 ** (i + 1), self.grids[i])
            norm += amp
            amp *= 0.5
        return out / norm

# --------------------------------------------------------------------------
# палитры
# --------------------------------------------------------------------------


def _palette(name):
    return gr.PALETTES[name]


def _hue_matrix(deg):
    """Матрица поворота оттенка (как hue-rotate в CSS)."""
    a = math.radians(deg)
    c, s = math.cos(a), math.sin(a)
    return np.array([
        [0.213 + c * 0.787 - s * 0.213, 0.715 - c * 0.715 - s * 0.715,
         0.072 - c * 0.072 + s * 0.928],
        [0.213 - c * 0.213 + s * 0.143, 0.715 + c * 0.285 + s * 0.140,
         0.072 - c * 0.072 - s * 0.283],
        [0.213 - c * 0.213 - s * 0.787, 0.715 - c * 0.715 + s * 0.715,
         0.072 + c * 0.928 + s * 0.072],
    ], dtype=np.float32)


class Animator:
    """Собирает кадр (H×W, BGR) для момента t (секунды)."""

    def __init__(self, style, palette, seed, width, height, cell_px,
                 speed=1.0, ramp=None):
        self.style = style
        self.W, self.H = int(width), int(height)
        self.speed = float(speed)
        pal = _palette(palette)
        self.pal = pal
        self.palette_name = palette
        self.hue = 0.0
        self._base_lut = gr.gradient_lut(pal["stops"])          # (1024,3) float RGB
        self._base_bg = gr.hex2rgb(pal["bg"])
        self._rebuild_colors()

        ch = max(7, int(round(cell_px * self.H / 2160.0)))
        self.ch = ch
        mode = "cjk" if style == "matrix" else "mono"
        spec = gr.fit_font(ch, mode)
        self.spec = spec
        self.cw = spec["cw"]
        self.rows = int(math.ceil(self.H / ch))
        self.cols = int(math.ceil(self.W / self.cw))
        rows, cols = self.rows, self.cols

        charset = gr.RAMPS["katakana" if mode == "cjk" else (ramp or "dense")]
        ramp_chars, masks = gr.build_ramp(charset, spec, sort=(style != "field"))
        self.ramp = ramp_chars
        self.n = len(ramp_chars)
        self.masks = np.stack([(masks[c] * 255.0).astype(np.uint8)
                               for c in ramp_chars])          # (n, ch, cw)
        self.ink_h, self.ink_w = rows * ch, cols * self.cw

        rng = np.random.default_rng(seed + 7)
        u = (np.arange(cols, dtype=np.float32) + 0.5) / cols
        v = (np.arange(rows, dtype=np.float32) + 0.5) / rows
        self.X = np.broadcast_to(u[None, :] * (self.W / self.H), (rows, cols)).copy()
        self.Y = np.broadcast_to(v[:, None], (rows, cols)).copy()

        # поля считаем в половинном разрешении — узор всё равно низкочастотный
        hr, hc = rows // 2 + 1, cols // 2 + 1
        self.hr, self.hc = hr, hc
        uh = (np.arange(hc, dtype=np.float32) + 0.5) / hc
        vh = (np.arange(hr, dtype=np.float32) + 0.5) / hr
        self.Xh = np.broadcast_to(uh[None, :] * (self.W / self.H), (hr, hc)).copy()
        self.Yh = np.broadcast_to(vh[:, None], (hr, hc)).copy()

        self.noise1 = Noise(seed * 3 + 1, octaves=4, base=2)
        self.noise2 = Noise(seed * 5 + 2, octaves=4, base=2)
        self.env_h = gr.norm01(self.noise2.sample(self.Xh * 0.7, self.Yh * 0.7), 8, 97)
        self.env = self._up(self.env_h)
        self.jitter = (rng.random((rows, cols)).astype(np.float32) - 0.5)

        cx, cy = 0.5, 0.5
        r2 = np.clip(((self.X / (self.W / self.H) - cx) ** 2 + (self.Y - cy) ** 2) / 0.5,
                     0.0, 1.0)
        self.vig = (1.0 - self.pal["vignette"] * r2 ** 1.6).astype(np.float32)
        self.cell_noise = rng.normal(0.0, 0.035, (rows, cols)).astype(np.float32)

        self.rnd_glyphs = rng.integers(0, self.n, size=(8, rows, cols)).astype(np.int32)
        self.m_heads = rng.random(cols).astype(np.float32)
        self.m_len = rng.uniform(0.12, 0.30, cols).astype(np.float32)
        self.m_speed = rng.uniform(0.05, 0.16, cols).astype(np.float32)
        self.m_gain = rng.uniform(0.55, 1.0, cols).astype(np.float32)
        self.m_empty = rng.random(cols) < 0.18
        self.m_tail_gamma = 3.4

        if style == "orbit":
            self.o_c = []
            for _ in range(5):
                self.o_c.append((
                    rng.uniform(-0.15, 1.0) * (self.W / self.H),
                    rng.uniform(-0.15, 1.0),
                    rng.uniform(9.0, 22.0),
                    rng.uniform(0.0, 2.0 * np.pi),
                    rng.uniform(0.55, 1.0),
                    rng.uniform(0.4, 1.0),
                ))

        self._bg = Image.new("RGB", (self.W, self.H), tuple(int(x) for x in self.bg))

        # ---- реакция на курсор ----
        ar0 = self.W / self.H
        self.cursor_on = True
        self.cursor_mode = "all"
        self.cursor_strength = 1.0
        self._cur_target = (0.5 * ar0, 0.5)
        self._cur_pos = (0.5 * ar0, 0.5)
        self._cur_active = 0.0
        self._cur_want = False
        self._trail = []
        self._last_pt = None
        self._shots = []          # волны от кликов: [x, y, время рождения]
        self._water = 0.0
        self._cur_act2 = 0.0
        self._wprev = None
        self._tm_prev = None
        self._tm_since = 0.0
        self._tm_flash = 0.0

        # ---- часы и таймер ----
        self.show_clock = False
        self.timer_end = None
        self._clock_font = None
        self._clock_font_size = 0

        # ---- снег ----
        self.snow_on = False
        self.snow_intensity = 1.0
        self._snow_ready = False
        self._snow_last_t = 0.0
        self._snow_color = self._snow_colour()
        self._snow_rng = np.random.default_rng(4242)

    # ------------------------------------------------------------------
    def _rebuild_colors(self):
        """Пересобирает LUT и фон с учётом текущего оттенка."""
        if self.hue:
            m = _hue_matrix(self.hue)
            lut = np.clip(self._base_lut @ m.T, 0, 255)
            bg = np.clip(self._base_bg @ m.T, 0, 255)
        else:
            lut = self._base_lut
            bg = self._base_bg
        self.lut = np.ascontiguousarray(lut).astype(np.uint8)   # RGB
        self.bg = bg.astype(np.uint8)                          # RGB
        self._bg = Image.new("RGB", (self.W, self.H), tuple(int(x) for x in self.bg))

    def set_palette(self, name):
        pal = _palette(name)
        self.pal = pal
        self.palette_name = name
        self._base_lut = gr.gradient_lut(pal["stops"])
        self._base_bg = gr.hex2rgb(pal["bg"])
        self._rebuild_colors()

    def set_hue(self, deg):
        self.hue = float(deg) % 360.0
        self._rebuild_colors()
        self._snow_color = self._snow_colour()

    # ------------------------------------------------------------------
    def _snow_colour(self):
        lum = 0.2126 * self.bg[0] + 0.7152 * self.bg[1] + 0.0722 * self.bg[2]
        return (120, 134, 152) if lum > 140 else (232, 244, 255)

    def set_snow(self, on, intensity=None):
        self.snow_on = bool(on)
        if intensity is not None:
            self.snow_intensity = float(intensity)
        if self.snow_on and not self._snow_ready:
            self._init_snow()

    def _init_snow(self):
        rng = self._snow_rng
        n = 280
        self.sn_x = rng.uniform(0, self.W, n).astype(np.float32)
        self.sn_y = rng.uniform(-self.H, 0, n).astype(np.float32)
        self.sn_v = rng.uniform(40, 115, n).astype(np.float32)
        self.sn_r = rng.uniform(0.8, 2.4, n).astype(np.float32)
        self.sn_ph = rng.uniform(0, 2 * np.pi, n).astype(np.float32)
        self.pile = np.zeros(self.W // 4 + 2, np.float32)
        self.pile_cap = self.H * 0.18
        self.ground = int(self.H * _ground_fraction())
        self._snow_ready = True
        self._snow_last_t = 0.0

    def _draw_snow(self, img, t):
        dt = min(0.1, max(0.0, t - self._snow_last_t))
        self._snow_last_t = t
        if dt <= 0:
            return img
        k = self.snow_intensity
        wind = 14.0 * math.sin(t * 0.7) + 6.0 * math.sin(t * 1.9)
        self.sn_y += self.sn_v * dt * k
        self.sn_x += (wind + 9.0 * np.sin(t * 1.3 + self.sn_ph)) * dt
        self.sn_x %= self.W

        ci = np.clip((self.sn_x / 4).astype(np.int32), 0, len(self.pile) - 1)
        floor = self.ground - self.pile[ci]
        landed = self.sn_y >= floor
        if landed.any():
            np.add.at(self.pile, ci[landed], 2.6 * k)
            # сугроб «оседает»: сглаживаем профиль, чтобы были плавные барханы
            p = self.pile
            self.pile = (p * 2.0 + np.roll(p, 1) + np.roll(p, -1)) / 4.0
            self.pile *= (1.0 - 0.02 * dt)          # медленно тает
            self.pile[:] = np.minimum(self.pile, self.pile_cap)
            self.sn_y[landed] = -6.0
            self.sn_x[landed] = self._snow_rng.uniform(0, self.W, int(landed.sum()))

        d = ImageDraw.Draw(img)
        # сугроб
        step = 4
        g = self.ground
        pts = [(x, g - float(self.pile[min(x // step, len(self.pile) - 1)]))
               for x in range(0, self.W + step, step)]
        pts += [(self.W, self.H), (0, self.H)]
        d.polygon(pts, fill=self._snow_color)
        # снежинки
        col = self._snow_color
        for x, y, r in zip(self.sn_x, self.sn_y, self.sn_r):
            if y < 0:
                continue
            d.ellipse((x - r, y - r, x + r, y + r), fill=col)
        return img

    # ------------------------------------------------------------------
    def set_cursor(self, nx, ny, active):
        """nx, ny — позиция курсора в долях экрана (0..1)."""
        ar = self.W / self.H
        self._cur_target = (float(nx) * ar, float(ny))
        self._cur_want = bool(active)

    def set_effect(self, mode, strength=None, enabled=None):
        if mode is not None:
            self.cursor_mode = mode
        if strength is not None:
            self.cursor_strength = float(strength)
        if enabled is not None:
            self.cursor_on = bool(enabled)

    def trigger_wave(self, nx=None, ny=None, scale=1.0):
        """Волна из точки (nx, ny) в долях экрана. scale < 1 — волна меньше."""
        ar = self.W / self.H
        if nx is None or ny is None:
            nx, ny = self._cur_target[0] / ar, self._cur_target[1]
        self._shots.append([float(nx) * ar, float(ny), None, float(scale)])
        if len(self._shots) > 8:
            self._shots.pop(0)
        self._water = max(self._water, 0.5 * scale)

    # ---- часы / таймер ---------------------------------------------------
    @property
    def keep_awake(self):
        """Таймер/часы на обоях: не даём анимации засыпать."""
        return bool(self.show_clock or self.timer_end is not None)

    def set_clock(self, on):
        self.show_clock = bool(on)

    def set_timer(self, seconds):
        self.timer_end = None if seconds is None else (_time.time() + float(seconds))

    def timer_left(self):
        if self.timer_end is None:
            return None
        return max(0.0, self.timer_end - _time.time())

    def _font_for(self, size):
        if self._clock_font is None or self._clock_font_size != size:
            path = os.path.join(os.environ.get("WINDIR", r"C:\Windows"),
                                "Fonts", "consolab.ttf")
            try:
                self._clock_font = ImageFont.truetype(path, size)
            except Exception:
                self._clock_font = ImageFont.load_default()
            self._clock_font_size = size
        return self._clock_font

    def _timer_text(self, left):
        total = int(round(left))
        h, rem = divmod(total, 3600)
        m, ss = divmod(rem, 60)
        return f"{h}:{m:02d}:{ss:02d}" if h else f"{m:02d}:{ss:02d}"

    def _timer_mask(self, t):
        """Маска цифр таймера на сетке глифов: цифры из тех же символов."""
        left = self.timer_left()
        if left is None:
            return None
        if left <= 0.0:
            self.timer_end = None
            return None
        text = self._timer_text(left)
        if getattr(self, "_tm_text", None) != text:
            old_text = getattr(self, "_tm_text", "") or ""
            gsm = _user32.GetSystemMetrics
            vx, vy, vw, vh = gsm(76), gsm(77), gsm(78), gsm(79)
            pw, ph = gsm(0), gsm(1)
            # центр основного монитора в координатах холста
            cx = (0 - vx + pw / 2.0) / max(1, vw) * self.W
            cy = (0 - vy + ph / 2.0) / max(1, vh) * self.H
            prim_h = ph * self.H / max(1, vh)
            path = os.path.join(os.environ.get("WINDIR", r"C:\Windows"),
                                "Fonts", "consolab.ttf")
            size = int(prim_h * 0.26)
            try:
                font = ImageFont.truetype(path, size)
            except Exception:
                font = ImageFont.load_default()
            # подгоняем под ширину экрана
            for _ in range(4):
                if font.getlength(text) <= self.W * 0.9:
                    break
                size = max(12, int(size * 0.9))
                try:
                    font = ImageFont.truetype(path, size)
                except Exception:
                    break
            big = Image.new("L", (self.W, self.H), 0)
            ImageDraw.Draw(big).text((cx, cy), text, font=font,
                                     fill=255, anchor="mm")
            small = big.resize((self.cols, self.rows), Image.BILINEAR)
            m = (np.asarray(small, np.float32) / 255.0 > 0.5).astype(np.float32)
            self._tm_prev = getattr(self, "_tm_base", None)
            self._tm_since = t
            self._tm_flash = 1.0
            self._tm_base = m
            self._tm_text = text
            # маленькая волна — из той цифры, которая изменилась
            total = font.getlength(text)
            start_x = cx - total / 2.0
            changed = [i for i in range(min(len(old_text), len(text)))
                       if old_text[i] != text[i]]
            if not changed:
                changed = [len(text) - 1]
            if old_text:
                # раз в минуту — волна вполовину от клика, из центра таймера
                old_min = old_text.split(":")[-2] if ":" in old_text else None
                new_min = text.split(":")[-2] if ":" in text else None
                if old_min is not None and new_min is not None and old_min != new_min:
                    self.trigger_wave(cx / max(1, self.W), cy / max(1, self.H),
                                      scale=0.5)
                for i in changed[:3]:
                    x_i = (start_x + font.getlength(text[:i])
                           + font.getlength(text[i]) / 2.0)
                    self.trigger_wave(x_i / max(1, self.W), cy / max(1, self.H),
                                      scale=1.0 / 3.0)

        base = getattr(self, "_tm_base", None)
        if base is None:
            return None

        # смена цифр: чётко, но с короткой вспышкой и рябью
        self._tm_flash = max(0.0, 1.0 - (t - self._tm_since) / 0.22)

        # «вода»: цифры колышутся и расходятся под курсором
        act = getattr(self, "_cur_act2", 0.0)
        water = getattr(self, "_water", 0.0)
        if act < 0.03 and water < 0.02:
            return base
        ar = self.W / self.H
        u = self.X / ar * self.cols
        v = self.Y * self.rows
        px, py = self._cur_pos
        cu = px / ar * self.cols
        cv = py * self.rows
        du = u - cu
        dv = v - cv
        dd = np.sqrt(du * du + dv * dv) + 1e-3
        near = np.exp(-(dd / (self.rows * 0.26)) ** 2)
        amp = (2.5 + 9.0 * water) * near * act
        wave = np.sin(dd * 0.17 - t * 3.6)
        ix = np.clip((u + du / dd * amp * wave).astype(np.int32), 0, self.cols - 1)
        iy = np.clip((v + dv / dd * amp * wave).astype(np.int32), 0, self.rows - 1)
        return base[iy, ix]

    def _overlay_clock(self, img):
        if not self.show_clock:
            return img
        now = datetime.now()
        text = now.strftime("%H:%M")
        size = max(16, int(self.H * 0.085))
        font = self._font_for(size)
        small = self._font_for(max(12, size // 3))
        d = ImageDraw.Draw(img)
        lum = 0.2126 * self.bg[0] + 0.7152 * self.bg[1] + 0.0722 * self.bg[2]
        idx = 150 if lum > 140 else 965
        col = tuple(int(v) for v in self.lut[idx])

        # реакция на курсор: подсвечиваем, когда он рядом с часами
        ar = self.W / self.H
        cx_px = self._cur_pos[0] / ar * self.W
        cy_px = self._cur_pos[1] * self.H
        box = d.textbbox((0, 0), text, font=font)
        tw, th = box[2] - box[0], box[3] - box[1]
        margin = int(size * 0.5)
        x = self.W - tw - margin
        ground = int(self.H * _ground_fraction())
        y = ground - th - margin
        dist = math.hypot(cx_px - (x + tw / 2), cy_px - (y + th / 2)) / max(1, self.H)
        boost = max(0.0, 1.0 - dist / 0.30) * self._cur_active
        col = tuple(int(c + (255 - c) * 0.5 * boost) for c in col)

        d.text((x + max(1, size // 20), y + max(1, size // 20)), text, font=font,
               fill=(0, 0, 0))
        d.text((x, y), text, font=font, fill=col)
        return img

    def _update_water(self):
        """Копим «волнение» от движения курсора: цифры колышутся как вода."""
        tx, ty = self._cur_target
        prev = getattr(self, "_wprev", None)
        if prev is None:
            self._water = 0.0
        else:
            speed = abs(tx - prev[0]) + abs(ty - prev[1])
            self._water = min(1.0, self._water * 0.94 + speed * 22.0)
        self._wprev = (tx, ty)
        want = 1.0 if self._cur_want else 0.0
        self._cur_act2 += (want - getattr(self, "_cur_act2", 0.0)) * 0.25

    def _waves(self, T, c, t):
        if not self._shots:
            return T, c
        keep = []
        for shot in self._shots:
            x, y, born, sc = shot
            if born is None:
                born = t
                shot[2] = t
            life = 1.5 * max(0.35, sc)
            age = t - born
            if age > life:
                continue
            keep.append(shot)
            r = age * 0.75 * sc                  # радиус растёт со временем
            w = 0.055 * sc
            d = np.sqrt((self.X - x) ** 2 + (self.Y - y) ** 2) + 1e-4
            fade = max(0.0, 1.0 - age / life) ** 1.2
            ring = np.exp(-((d - r) / max(0.008, w)) ** 2) * fade
            flash = np.exp(-(d / (0.10 * sc)) ** 2) * max(0.0, 1.0 - age / (0.25 * sc))
            T = T + (0.40 * ring + 0.30 * flash) * sc
            c = c + (0.50 * ring + 0.45 * flash) * sc
        self._shots = keep
        return T, c

    # ------------------------------------------------------------------
    def _cursor_layer(self, T, c, t, idx=None):
        if not self.cursor_on or self.cursor_strength <= 0:
            return T, c, idx
        ar = self.W / self.H
        tx, ty = self._cur_target
        px, py = self._cur_pos
        # без сглаживания-тормоза: курсор догоняется за 1-2 кадра
        px += (tx - px) * 0.55
        py += (ty - py) * 0.55
        self._cur_pos = (px, py)
        self._cur_active += ((1.0 if self._cur_want else 0.0) - self._cur_active) * 0.35
        act = self._cur_active
        if act < 0.02:
            return T, c, idx

        s = self.cursor_strength * act
        mode = self.cursor_mode
        dx = self.X - px
        dy = self.Y - py
        d = np.sqrt(dx * dx + dy * dy) + 1e-4

        if mode in ("all", "magnet"):
            # магнитно-вихревое искажение поля
            f = s * 0.055 * np.exp(-(d / 0.18) ** 2)
            ang = np.arctan2(dy, dx)
            swirl = f * 1.4
            ox = (-dy / d * f + np.cos(ang + 1.2) * swirl) * ar
            oy = (dx / d * f + np.sin(ang + 1.2) * swirl)
            cols, rows = self.cols, self.rows
            ix = np.clip(((self.X + ox) / ar * cols).astype(np.int32), 0, cols - 1)
            iy = np.clip(((self.Y + oy) * rows).astype(np.int32), 0, rows - 1)
            T = T[iy, ix]
            c = c[iy, ix]
            dx = self.X - px
            dy = self.Y - py
            d = np.sqrt(dx * dx + dy * dy) + 1e-4
        elif mode == "aura":
            # «аура»: содержимое отталкивается от курсора (магнит наоборот)
            ring_w = np.exp(-((d - 0.06) / 0.20) ** 2)
            f = s * 0.11 * ring_w
            cols, rows = self.cols, self.rows
            ix = np.clip(((self.X - dx / d * f * ar) / ar * cols).astype(np.int32),
                         0, cols - 1)
            iy = np.clip(((self.Y - dy / d * f) * rows).astype(np.int32), 0, rows - 1)
            T = T[iy, ix]
            c = c[iy, ix]
            dx = self.X - px
            dy = self.Y - py
            d = np.sqrt(dx * dx + dy * dy) + 1e-4

        if mode in ("all", "glow"):
            g = np.exp(-(d / 0.13) ** 2)
            c = c + 0.55 * s * g
            T = T + 0.18 * s * g
        elif mode == "aura":
            ring = np.exp(-((d - 0.15) / 0.075) ** 2)
            c = c + 0.50 * s * ring
            T = T + 0.12 * s * ring

        return np.clip(T, 0.0, 1.0), c, idx

    def _up(self, a):
        big = np.repeat(np.repeat(a, 2, axis=0), 2, axis=1)
        return np.ascontiguousarray(big[:self.rows, :self.cols])

    def _flow(self, t):
        s = self.speed
        a = t * 0.045 * s
        ca, sa = math.cos(a), math.sin(a)
        x = self.Xh * ca - self.Yh * sa
        y = self.Xh * sa + self.Yh * ca
        x = x * 1.15 + 0.55 * math.sin(t * 0.07 * s)
        y = y * 1.15 + 0.55 * math.cos(t * 0.055 * s)
        w = self.noise1.sample(x * 0.9, y * 0.9)
        q = self.noise2.sample(x * 1.05 + 1.4 * (w - 0.5), y * 1.05 + 1.4 * (w - 0.5))
        qf = self._up(q)
        band = 0.5 + 0.5 * np.sin(2.0 * np.pi * (qf * 2.1 + 0.3 * self.Y + t * 0.02 * s))
        band = np.clip(band, 0, 1) ** 2.0
        T = band * (0.10 + 0.90 * self.env)
        T = np.clip((T - 0.02) / 0.9, 0, 1) ** 1.25
        c = 0.45 * qf + 0.55 * T
        return T, c

    def _field(self, t):
        s = self.speed
        x = self.Xh + 0.25 * math.sin(t * 0.05 * s)
        y = self.Yh + 0.25 * math.cos(t * 0.042 * s)
        a = self.noise1.sample(x * 1.15, y * 1.15)
        b = self.noise2.sample(x * 2.6 + 4.2, y * 2.6 + 1.1)
        angle = (a * 2.0 + 0.45 * b + t * 0.03 * s) * np.pi * 2.0
        dens = np.clip(self.noise2.sample(x * 0.8 + 3.3, y * 0.8 + 6.6), 0, 1)
        dens = self._up(np.clip((dens - 0.18) / 0.72, 0, 1) ** 1.3)
        angf = self._up(np.floor((angle % (2.0 * np.pi)) / (np.pi / 2.0)).astype(np.float32))
        idx = angf.astype(np.int32) % 4
        return dens, dens * 0.55 + self._up(a) * 0.45, idx

    def _orbit(self, t):
        s = self.speed
        acc = np.zeros((self.hr, self.hc), np.float32)
        for cx, cy, k, ph, amp, w in self.o_c:
            d = np.sqrt((self.Xh - cx) ** 2 + (self.Yh - cy) ** 2)
            acc += amp * np.cos(d * k + ph + t * w * 0.5 * s)
        acc /= len(self.o_c)
        T = np.clip(0.5 + 0.5 * self._up(acc), 0, 1)
        T = np.clip((T - 0.08) / 0.84, 0, 1) ** 1.15
        c = 0.5 * T + 0.5 * self.env
        return T, c

    def _matrix(self, t):
        s = self.speed
        v = (np.arange(self.rows, dtype=np.float32) + 0.5) / self.rows
        head = (self.m_heads + self.m_speed * t * 0.9 * s) % 1.35 - 0.15
        tail = (head[None, :] - v[:, None]) / self.m_len[None, :]
        inten = np.exp(-np.clip(tail, 0, None) * self.m_tail_gamma)
        inten = np.where((tail < 0) | (tail > 1), 0.0, inten)
        inten[:, self.m_empty] = 0.0
        T = np.clip(inten * self.m_gain[None, :], 0, 1)
        c = 0.25 + 0.75 * T ** 0.6
        slot = int(t * 7.0) % self.rnd_glyphs.shape[0]
        return T, c, self.rnd_glyphs[slot]

    def _fire(self, t):
        s = self.speed
        x = self.Xh + 0.35 * math.sin(t * 0.6 * s)
        w = self.noise2.sample(x * 1.3 + 0.4, self.Yh * 0.8 + 2.0)
        q = self.noise1.sample(x * 3.0 + 1.6 * (w - 0.5), self.Yh * 0.6 + t * 0.9 * s)
        q = q + 0.35 * (self.noise2.sample(x * 6.0 + 3.0, self.Yh * 1.2 + t * 0.35 * s) - 0.5)
        base = np.clip((self.Yh - 0.12) / 0.88, 0, 1) ** 0.95
        flicker = 0.92 + 0.08 * math.sin(t * 13.0 * s) + 0.04 * math.sin(t * 29.0 * s)
        T = np.clip((q * 2.4 * base - 0.52) / 0.42, 0, 1) ** 0.85
        T = self._up(T * flicker)
        c = np.clip(0.14 + 0.94 * T, 0, 1)
        return T, c

    # ------------------------------------------------------------------
    def frame(self, t):
        idx_override = None
        if self.style == "matrix":
            T, c, idx_override = self._matrix(t)
        elif self.style == "flow":
            T, c = self._flow(t)
        elif self.style == "field":
            T, c, idx_override = self._field(t)
        elif self.style == "fire":
            T, c = self._fire(t)
        else:
            T, c = self._orbit(t)

        c = np.clip(c + self.cell_noise, 0.0, 1.0)
        T, c, idx_override = self._cursor_layer(T, c, t, idx_override)
        self._update_water()
        tm = self._timer_mask(t)
        if tm is not None:
            T = np.maximum(T, np.clip(tm * 1.1, 0.0, 1.0))
            c = np.maximum(c, 0.30 + 0.75 * tm)
            c = c + 0.30 * getattr(self, "_tm_flash", 0.0) * tm
        T, c = self._waves(T, c, t)
        c = np.clip(c, 0.0, 1.0)
        ci = (c * 1023.0).astype(np.int32)
        col = self.lut[ci]                          # (rows, cols, 3) uint8 (BGR)
        col = (col.astype(np.float32) * self.vig[:, :, None]).astype(np.uint8)

        if idx_override is None:
            idx = np.clip(np.floor(T * (self.n - 1) + self.jitter * 0.75),
                          0, self.n - 1).astype(np.int32)
        else:
            idx = idx_override

        ink = self.masks[idx]                       # (rows, cols, ch, cw)
        ink = ink.transpose(0, 2, 1, 3).reshape(self.ink_h, self.ink_w)
        ink = np.ascontiguousarray(ink[:self.H, :self.W])

        color_img = Image.fromarray(col, "RGB").resize((self.W, self.H), Image.NEAREST)
        mask_img = Image.fromarray(ink, "L")
        out = Image.composite(color_img, self._bg, mask_img)
        if self.snow_on:
            out = self._draw_snow(out, t)
        if self.show_clock:
            out = self._overlay_clock(out)
        return out
