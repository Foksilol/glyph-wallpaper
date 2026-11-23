#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
glyph_wallpaper.py — процедурные обои, нарисованные символами.

Стили:
    flow     — доменно-искажённый fbm, полосы-течения
    contour  — топографические изолинии по fbm
    orbit    — интерференция концентрических волн
    matrix   — падающие столбцы катаканы

Палитры: midnight-cyan, ember-carbon, magenta-noir, terminal-green,
         paper-ink, amber-terminal

Примеры:
    python glyph_wallpaper.py --preset all
    python glyph_wallpaper.py --style flow --palette paper-ink --seed 42 \
        --size 2560x1440 --out my.png
"""

import argparse
import json
import math
import os
import time

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

WIN = os.environ.get("WINDIR", r"C:\Windows")
FONTS = {
    "mono": (os.path.join(WIN, "Fonts", "consola.ttf"), 0),
    "cjk": (os.path.join(WIN, "Fonts", "msgothic.ttc"), 0),
}

# --------------------------------------------------------------------------
# палитры
# --------------------------------------------------------------------------


def hex2rgb(h):
    h = h.lstrip("#")
    return np.array([int(h[i:i + 2], 16) for i in (0, 2, 4)], np.float32)


def gradient_lut(stops, n=1024):
    stops = sorted(stops, key=lambda s: s[0])
    xs = np.array([s[0] for s in stops], np.float32)
    cols = np.stack([hex2rgb(s[1]) for s in stops])
    t = np.linspace(0.0, 1.0, n, dtype=np.float32)
    lut = np.stack([np.interp(t, xs, cols[:, i]) for i in range(3)], axis=1)
    return lut.astype(np.float32)


PALETTES = {
    "midnight-cyan": dict(
        bg="#04070c", strength=1.10,
        stops=[(0.00, "#050c12"), (0.22, "#0a2836"), (0.50, "#116b80"),
               (0.78, "#3fd8cb"), (1.00, "#f2fffd")],
        bloom=0.50, haze=0.24, vignette=0.42, grain=1.5,
    ),
    "ember-carbon": dict(
        bg="#0a0604", strength=1.12,
        stops=[(0.00, "#120a07"), (0.22, "#3d1a0e"), (0.50, "#a3471a"),
               (0.78, "#f59a4a"), (1.00, "#fff1d6")],
        bloom=0.50, haze=0.26, vignette=0.44, grain=1.6,
    ),
    "magenta-noir": dict(
        bg="#080509", strength=1.10,
        stops=[(0.00, "#120a13"), (0.22, "#3c1240"), (0.50, "#a83079"),
               (0.78, "#ff7cc0"), (1.00, "#ffe6f4")],
        bloom=0.50, haze=0.24, vignette=0.45, grain=1.6,
    ),
    "terminal-green": dict(
        bg="#020604", strength=1.08,
        stops=[(0.00, "#03120a"), (0.22, "#0b3f20"), (0.50, "#22a34b"),
               (0.78, "#8cf7a8"), (1.00, "#effff4")],
        bloom=0.55, haze=0.30, vignette=0.40, grain=1.4, scanlines=0.05,
    ),
    "amber-terminal": dict(
        bg="#060402", strength=1.08,
        stops=[(0.00, "#130d03"), (0.22, "#4a2c06"), (0.50, "#b06f0a"),
               (0.78, "#ffc95c"), (1.00, "#fff6df")],
        bloom=0.55, haze=0.30, vignette=0.42, grain=1.4, scanlines=0.05,
    ),
    "paper-ink": dict(
        bg="#efe9dc", strength=0.98,
        stops=[(0.00, "#e4ddcd"), (0.22, "#b9b09c"), (0.50, "#7d7666"),
               (0.78, "#3a352c"), (1.00, "#0a0a08")],
        bloom=0.10, haze=0.05, vignette=0.16, grain=1.2,
    ),
    "fire": dict(
        bg="#0b0301", strength=1.00,
        stops=[(0.00, "#1a0503"), (0.22, "#5a1204"), (0.48, "#c23a0a"),
               (0.74, "#ff8a1e"), (0.92, "#ffd47a"), (1.00, "#fff8e0")],
        bloom=0.75, haze=0.40, vignette=0.38, grain=1.8,
    ),
}

RAMPS = {
    "dense": " .`'\":;~-+=*xX#%$@&8BWM",
    "line": " .-~=+*#%@",
    "blocks": " .:-=+*#%@",
    "dirs": "-/|\\",
    "katakana": "アイウエオカキクケコサシスセソタチツテトナニヌネノ"
                "ハヒフヘホマミムメモヤユヨラリルレロワン0123456789",
}

DESIGNS = [
    dict(key="flow-cyan",      style="flow",    palette="midnight-cyan",
         seed=1841, cell=22, ramp="dense"),
    dict(key="flow-amber",     style="flow",    palette="amber-terminal",
         seed=4211, cell=21, ramp="dense"),
    dict(key="orbit-noir",     style="orbit",   palette="magenta-noir",
         seed=320,  cell=21, ramp="dense"),
    dict(key="matrix-green",   style="matrix",  palette="terminal-green",
         seed=99,   cell=28, ramp="katakana",
         tweak=dict(haze=1.4, bloom=1.15, strength=1.22)),
    dict(key="field-ember",    style="field",   palette="ember-carbon",
         seed=812,  cell=20, ramp="dirs",
         tweak=dict(bloom=0.7, haze=0.6, gain=1.35)),
    dict(key="field-cyan",     style="field",   palette="midnight-cyan",
         seed=1777, cell=21, ramp="dirs",
         tweak=dict(bloom=0.8, haze=0.7, gain=1.20)),
    dict(key="field-paper",    style="field",   palette="paper-ink",
         seed=640,  cell=21, ramp="dirs",
         tweak=dict(bloom=0.5, haze=0.4, gain=1.30)),
    dict(key="flow-paper",     style="flow",    palette="paper-ink",
         seed=2718, cell=26, ramp="dense"),
]

# --------------------------------------------------------------------------
# шум
# --------------------------------------------------------------------------


def smoothstep(t):
    return t * t * (3.0 - 2.0 * t)


def vnoise(x, y, res, rng):
    """Периодический value-noise, координаты — 2D-массивы."""
    res = int(max(2, res))
    g = rng.random((res, res), dtype=np.float32)
    g = np.pad(g, ((0, 1), (0, 1)), mode="wrap")
    fx = np.asarray(x, np.float32) * res
    fy = np.asarray(y, np.float32) * res
    ix = np.floor(fx)
    iy = np.floor(fy)
    tx = smoothstep(fx - ix)
    ty = smoothstep(fy - iy)
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


def fbm(x, y, res, octaves, rng, gain=0.5):
    out = np.zeros(x.shape, np.float32)
    amp, norm, r = 1.0, 0.0, float(res)
    for _ in range(octaves):
        out += amp * vnoise(x, y, max(2, int(round(r))), rng)
        norm += amp
        amp *= gain
        r *= 2.0
    return out / norm


def norm01(a, lo=2.0, hi=98.0):
    p1, p2 = np.percentile(a, [lo, hi])
    if float(p2 - p1) < 1e-7:
        return np.zeros_like(a)
    return np.clip((a - p1) / (p2 - p1), 0.0, 1.0).astype(np.float32)


def smooth2d(a, passes=1):
    a = np.asarray(a, np.float32)
    for _ in range(passes):
        a = (a * 4.0 + np.roll(a, 1, 0) + np.roll(a, -1, 0)
             + np.roll(a, 1, 1) + np.roll(a, -1, 1)) / 8.0
    return a


def grid_coords(rows, cols, aspect):
    u = (np.arange(cols, dtype=np.float32) + 0.5) / cols
    v = (np.arange(rows, dtype=np.float32) + 0.5) / rows
    X = np.broadcast_to(u[None, :] * aspect, (rows, cols))
    Y = np.broadcast_to(v[:, None], (rows, cols))
    return X.astype(np.float32), Y.astype(np.float32)

# --------------------------------------------------------------------------
# стили
# --------------------------------------------------------------------------


def style_flow(rows, cols, rng, aspect, freq=1.0, bands=2.2):
    X, Y = grid_coords(rows, cols, aspect)
    X = X * freq + 0.37
    Y = Y * freq + 1.93
    w1 = fbm(X * 1.45, Y * 1.45, 3, 4, rng)
    w2 = fbm(X * 1.45 + 11.3, Y * 1.45 + 4.7, 3, 4, rng)
    q = fbm(X * 1.05 + 1.9 * (w1 - 0.5), Y * 1.05 + 1.9 * (w2 - 0.5), 3, 5, rng)
    band = 0.5 + 0.5 * np.sin(2.0 * np.pi * (q * bands + 0.30 * Y))
    band = norm01(band, 2, 98) ** 2.0
    env = fbm(X * 0.62 + 5.1, Y * 0.62 + 2.8, 2, 3, rng)
    env = norm01(env, 8, 97)
    T = band * (0.10 + 0.90 * env)
    T = norm01(T, 0.5, 99.5) ** 1.3
    c = 0.45 * norm01(q, 2, 98) + 0.55 * T
    return T, None, c


def style_contour(rows, cols, rng, aspect, levels=8, width=0.12):
    X, Y = grid_coords(rows, cols, aspect)
    X = X * 2.0 + 3.7
    Y = Y * 2.0 + 8.1
    h = norm01(fbm(X, Y, 3, 5, rng), 2, 98)
    h = np.clip(h + 0.04 * (fbm(X * 3.1, Y * 3.1, 4, 3, rng) - 0.5), 0, 1)
    h = smooth2d(h, 1)
    f = h * levels
    fr = f - np.floor(f)
    dist = np.minimum(fr, 1.0 - fr)
    line = (dist < width).astype(np.float32)
    band = np.floor(f) % 2
    T = 0.02 + 0.98 * line * (0.80 + 0.20 * band)
    c = 0.70 * norm01(h, 2, 98) + 0.30 * T
    return T, None, c


def style_orbit(rows, cols, rng, aspect, waves=5):
    X, Y = grid_coords(rows, cols, aspect)
    acc = np.zeros((rows, cols), np.float32)
    for _ in range(waves):
        cx = rng.uniform(-0.15, 1.0) * aspect
        cy = rng.uniform(-0.15, 1.0)
        k = rng.uniform(7.0, 15.0)
        ph = rng.uniform(0.0, 2.0 * np.pi)
        amp = rng.uniform(0.55, 1.0)
        d = np.sqrt((X - cx) ** 2 + (Y - cy) ** 2)
        acc += amp * np.cos(d * k + ph)
    acc /= waves
    T = norm01(0.5 + 0.5 * acc, 2, 98)
    T = smooth2d(T, 1)
    T = smoothstep(T) ** 1.2
    c = 0.55 * norm01(np.sqrt((X - aspect * 0.5) ** 2 + (Y - 0.5) ** 2), 5, 95) + 0.45 * T
    return T, None, c


def style_matrix(rows, cols, rng, aspect):
    v = (np.arange(rows, dtype=np.float32) + 0.5) / rows
    head = rng.uniform(-0.05, 1.12, cols).astype(np.float32)
    length = rng.uniform(0.18, 0.42, cols).astype(np.float32)
    gain = rng.uniform(0.55, 1.0, cols).astype(np.float32)
    tail = (head[None, :] - v[:, None]) / length[None, :]
    inten = np.exp(-np.clip(tail, 0.0, None) * 2.4) * 0.95
    inten = np.where((tail < 0.0) | (tail > 1.0), 0.0, inten)
    empty = rng.random(cols) < 0.15
    inten[:, empty] = 0.0
    spark = 1.15 * np.exp(-((v[:, None] - head[None, :]) / 0.035) ** 2)
    spark[:, empty] = 0.0
    T = np.clip(inten * gain[None, :] + spark, 0.0, 2.0)
    T = norm01(T, 0.2, 99.9) ** 1.05
    c = np.clip(0.03 + 1.05 * T ** 0.55
                + 0.05 * rng.random((rows, cols)).astype(np.float32), 0, 1)
    return T, None, c


def style_fire(rows, cols, rng, aspect):
    X, Y = grid_coords(rows, cols, aspect)
    Xn, Yn = X + 0.61, Y + 3.17
    warp = fbm(Xn * 1.3 + 1.0, Yn * 0.8 + 2.0, 3, 3, rng)
    q = fbm(Xn * 3.0 + 1.6 * (warp - 0.5), Yn * 0.6, 4, 5, rng)
    q = q + 0.35 * (fbm(Xn * 6.0 + 3.0, Yn * 1.2 + 5.0, 4, 4, rng) - 0.5)
    base = np.clip((Y - 0.12) / 0.88, 0, 1) ** 0.95
    T = np.clip((q * 2.4 * base - 0.52) / 0.42, 0, 1) ** 0.85
    specks = (rng.random((rows, cols)) > 0.9975).astype(np.float32)
    T = np.clip(T + specks * 0.9 * (1.0 - T) * (1.0 - Y), 0, 1)
    c = np.clip(0.14 + 0.94 * T, 0, 1)
    return T, None, c


def style_field(rows, cols, rng, aspect):
    X, Y = grid_coords(rows, cols, aspect)
    X = X + 0.71
    Y = Y + 2.13
    a = fbm(X * 1.25, Y * 1.25, 3, 5, rng)
    b = fbm(X * 3.1 + 9.4, Y * 3.1 + 1.7, 3, 3, rng)
    angle = (a * 2.0 + 0.45 * b) * np.pi * 2.0
    dens = fbm(X * 0.85 + 4.4, Y * 0.85 + 2.2, 2, 3, rng)
    dens = norm01(dens, 3, 97) ** 1.45
    idx = np.floor((angle % (2.0 * np.pi)) / (np.pi / 2.0)).astype(np.int32) % 4
    c = 0.55 * dens + 0.45 * norm01(a, 3, 97)
    return dens, idx, c


STYLES = {
    "flow": style_flow,
    "contour": style_contour,
    "orbit": style_orbit,
    "matrix": style_matrix,
    "field": style_field,
    "fire": style_fire,
}

# --------------------------------------------------------------------------
# шрифт и глифы
# --------------------------------------------------------------------------


def fit_font(ch, mode):
    path, index = FONTS["cjk" if mode == "cjk" else "mono"]
    if mode == "cjk":
        size = int(round(ch * 0.98))
    else:
        size = int(round(ch * 1.08))
    size = max(6, size)
    font = ImageFont.truetype(path, size, index=index)
    asc, desc = font.getmetrics()
    baseline = ch - desc
    if baseline < int(ch * 0.62):
        size = max(6, int(size * 0.9))
        font = ImageFont.truetype(path, size, index=index)
        asc, desc = font.getmetrics()
        baseline = ch - desc
    if baseline < 1:
        baseline = max(1, int(ch * 0.82))
    probe = "ア" if mode == "cjk" else "M"
    cw = max(3, int(round(font.getlength(probe))))
    return dict(path=path, index=index, size=size, baseline=baseline,
                cw=cw, ch=ch, font=font, mode=mode)


def glyph_mask(char, spec, ss=2):
    cw, ch = spec["cw"], spec["ch"]
    big = ImageFont.truetype(spec["path"], spec["size"] * ss, index=spec["index"])
    img = Image.new("L", (cw * ss, ch * ss), 0)
    d = ImageDraw.Draw(img)
    d.text((0, spec["baseline"] * ss), char, font=big, fill=255, anchor="ls")
    img = img.resize((cw, ch), Image.LANCZOS)
    return np.asarray(img, np.float32) / 255.0


def build_ramp(charset, spec, sort=True):
    """Раскладываем символы по фактической плотности краски."""
    masks = {}
    items = []
    for c in charset:
        if c == " ":
            items.append((c, 0.0))
            continue
        m = glyph_mask(c, spec)
        cov = float(m.mean())
        if cov < 1e-4:
            continue
        masks[c] = m
        items.append((c, cov))
    if " " not in masks:
        masks[" "] = np.zeros((spec["ch"], spec["cw"]), np.float32)
    if sort:
        items.sort(key=lambda it: it[1])
    ramp = [it[0] for it in items]
    return ramp, masks

# --------------------------------------------------------------------------
# рендер
# --------------------------------------------------------------------------


def blur(a, radius):
    if radius < 0.6:
        return a
    u8 = (np.clip(a, 0.0, 1.0) * 255.0 + 0.5).astype(np.uint8)
    img = Image.fromarray(u8, "L").filter(ImageFilter.GaussianBlur(radius))
    return np.asarray(img, np.float32) / 255.0


def resize_f(a, W, H):
    return np.asarray(Image.fromarray(a, "F").resize((W, H), Image.BICUBIC), np.float32)


def render(style, palette, seed, W, H, cell, out_path=None, verbose=True,
           ramp_name="dense", tweak=None):
    t0 = time.time()
    pal = PALETTES[palette]
    lut = gradient_lut(pal["stops"])
    bg = hex2rgb(pal["bg"])

    cell = float(cell)
    ch = max(7, int(round(cell * H / 2160.0)))
    mode = "cjk" if style == "matrix" else "mono"
    spec = fit_font(ch, mode)
    cw = spec["cw"]
    rows = int(math.ceil(H / ch))
    cols = int(math.ceil(W / cw))

    rng = np.random.default_rng(seed)
    tweak = tweak or {}
    aspect = W / H
    T, idx_override, cfield = STYLES[style](rows, cols, rng, aspect)

    ramp, masks = build_ramp(RAMPS["katakana" if mode == "cjk" else ramp_name],
                             spec, sort=(ramp_name != "dirs"))
    n = len(ramp)
    if idx_override is not None:
        idx = np.clip(idx_override, 0, n - 1).astype(np.int32)
    elif mode == "cjk":
        idx = rng.integers(0, n, size=(rows, cols))
    else:
        jitter = (rng.random((rows, cols)).astype(np.float32) - 0.5)
        idx = np.clip(np.floor(T * (n - 1) + jitter * 0.75), 0, n - 1).astype(np.int32)

    # слой «краски»
    ink = np.zeros((rows * ch, cols * cw), np.float32)
    for r in range(rows):
        y0 = r * ch
        row = idx[r]
        for c in range(cols):
            m = masks[ramp[row[c]]]
            x0 = c * cw
            ink[y0:y0 + ch, x0:x0 + cw] = m
    ink = ink[:H, :W]

    # цвет
    cfield = cfield * tweak.get("gain", 1.0)
    cfield = np.clip(cfield + 0.05 * (rng.random((rows, cols)).astype(np.float32) - 0.5), 0, 1)
    ci = np.clip((cfield * 1023.0).astype(np.int32), 0, 1023)
    col_low = lut[ci]
    col_full = np.empty((H, W, 3), np.float32)
    for k in range(3):
        col_full[:, :, k] = resize_f(col_low[:, :, k], W, H)

    bloom = blur(ink, max(1.0, ch * 1.5))
    haze = blur(ink, max(2.0, ch * 8.0))

    strength = pal["strength"] * tweak.get("strength", 1.0)
    kalpha = np.clip(ink * strength, 0.0, 1.0)[:, :, None]
    glow = (bloom * pal["bloom"] * tweak.get("bloom", 1.0)
            + haze * pal["haze"] * tweak.get("haze", 1.0))
    glow = glow.astype(np.float32)[:, :, None]

    out = bg[None, None, :] * (1.0 - kalpha)
    out += col_full * np.clip(kalpha + glow, 0.0, 1.35)

    if pal.get("scanlines"):
        s = pal["scanlines"]
        out[1::2, :, :] *= (1.0 - s)
    if pal.get("grain"):
        out += rng.normal(0.0, pal["grain"], (H, W, 1)).astype(np.float32)

    yy = ((np.arange(H, dtype=np.float32) + 0.5) / H - 0.5)[:, None]
    xx = ((np.arange(W, dtype=np.float32) + 0.5) / W - 0.5)[None, :]
    r2 = np.clip((xx * xx + yy * yy) / 0.5, 0.0, 1.0)
    vig = (1.0 - pal["vignette"] * r2 ** 1.6)[:, :, None]
    out *= vig

    img = Image.fromarray(np.clip(out, 0.0, 255.0).astype(np.uint8), "RGB")
    if out_path:
        os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
        img.save(out_path, optimize=True)
    if verbose:
        print(f"  {style}/{palette} seed={seed} cells={cols}x{rows} "
              f"font={spec['size']}px {time.time() - t0:.1f}s -> {out_path}")
    return img


def make_preview(img, width=1280):
    h = int(round(img.height * width / img.width))
    return img.resize((width, h), Image.LANCZOS)


def contact_sheet(items, path, tile=760, gap=18, cols=3, title=None):
    rows = int(math.ceil(len(items) / cols))
    th = int(round(tile * 9 / 16))
    W = cols * tile + (cols + 1) * gap
    H = rows * (th + gap + 34) + gap + (52 if title else 0)
    sheet = Image.new("RGB", (W, H), (12, 12, 14))
    d = ImageDraw.Draw(sheet)
    f = ImageFont.truetype(FONTS["mono"][0], 22)
    fb = ImageFont.truetype(FONTS["mono"][0], 30)
    y = gap
    if title:
        d.text((gap, 14), title, font=fb, fill=(235, 235, 235))
        y = 62
    for i, (label, img) in enumerate(items):
        r, c = divmod(i, cols)
        x = gap + c * (tile + gap)
        yy = y + r * (th + gap + 34)
        thumb = img.resize((tile, th), Image.LANCZOS)
        sheet.paste(thumb, (x, yy))
        d.text((x + 2, yy + th + 6), label, font=f, fill=(190, 190, 196))
    sheet.save(path, optimize=True)
    return sheet


def parse_size(s):
    w, h = s.lower().split("x")
    return int(w), int(h)


def main():
    ap = argparse.ArgumentParser(description="Процедурные обои из символов")
    ap.add_argument("--preset", choices=["all"], default=None)
    ap.add_argument("--style", default="flow", choices=list(STYLES))
    ap.add_argument("--palette", default="midnight-cyan", choices=list(PALETTES))
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--cell", type=float, default=None, help="высота ячейки в px при 2160p")
    ap.add_argument("--size", default="3840x2160")
    ap.add_argument("--out", default=None)
    ap.add_argument("--outdir", default=".")
    ap.add_argument("--ramp", default="dense", choices=list(RAMPS))
    ap.add_argument("--sizes", default="3840x2160,1920x1080")
    args = ap.parse_args()

    if args.preset == "all":
        sizes = [parse_size(s) for s in args.sizes.split(",")]
        manifest = []
        for size in sizes:
            for d in DESIGNS:
                W, H = size
                name = f"{d['key']}-{W}x{H}.png"
                out = os.path.join(args.outdir, name)
                img = render(d["style"], d["palette"], d["seed"], W, H,
                             d["cell"], out, ramp_name=d.get("ramp", "dense"),
                             tweak=d.get("tweak"))
                make_preview(img).save(
                    os.path.join(args.outdir, "preview", f"{d['key']}-preview.png"),
                    optimize=True)
                manifest.append(dict(d, width=W, height=H, file=name))
        with open(os.path.join(args.outdir, "manifest.json"), "w", encoding="utf-8") as fh:
            json.dump(manifest, fh, ensure_ascii=False, indent=2)
        return

    W, H = parse_size(args.size)
    cell = args.cell or 20
    out = args.out or os.path.join(args.outdir, f"{args.style}-{args.palette}-{W}x{H}.png")
    render(args.style, args.palette, args.seed, W, H, cell, out, ramp_name=args.ramp)


if __name__ == "__main__":
    main()
