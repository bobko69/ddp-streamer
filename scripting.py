"""Animation scripts for the DDP Streamer's script item.

A script is a normal Python file. It can have:
  pixel(x, y, t)  -> a color for every pixel at once (x, y run 0..1, t = seconds)
  setup(c)        -> runs once when the item starts
  frame(c, t)     -> draws each frame on the canvas c
  params = {...}  -> settings shown on the item (slider, color_picker, dropdown,
                     checkbox, textbox); their values are plain variables
Live data (weather, stock, youtube, headlines, fetch) comes from datasources.py.

Colors are (r, g, b) with 0..1 values, "#rrggbb", a name like "red", or
rgb(255, 0, 0). The manual is docs/scripting.html.

ScriptGen(d, W, H).frame(t) -> HxWx3 uint8 BGR, like the text and clock items.
"""
import hashlib, json, math, os, random, shutil, sys, time, traceback
import numpy as np
import cv2

import datasources

HERE = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
EXAMPLES_DIR = os.path.join(HERE, "scripts")
USER_DIR = os.path.join(datasources.DATA_DIR, "scripts")
FONTS_DIR = os.path.join(HERE, "fonts")
MANUAL = os.path.join(HERE, "docs", "scripting.html")
ERRORS = {}          # script path -> last error text ("" when fine), read by the editor
COPIES = os.path.join(USER_DIR, ".copied.json")   # {file name: hash} of examples copied to My scripts,
                                                  # as copied - an unedited copy follows the example
TEMPLATE = '''"""My animation
One line about what it shows.
"""

params = {
    "speed": slider(1.0, 0.1, 4.0),
    "tint": color_picker("#ff2080"),
}


def setup(c):
    c.x0 = c.w / 2          # keep anything you need between frames on c


def frame(c, t):
    c.fade(0.85)            # leave short trails
    x = c.w / 2 + math.sin(t * speed * 2) * c.w * 0.4
    y = c.h / 2 + math.cos(t * speed * 3) * c.h * 0.35
    c.circle(x, y, c.h * 0.12, tint)
    c.glow()
'''


# ---------------------------------------------------------------------------
# Colors
# ---------------------------------------------------------------------------
NAMED = {"black": (0, 0, 0), "white": (1, 1, 1), "red": (1, 0, 0), "green": (0, 1, 0),
         "blue": (0, 0, 1), "yellow": (1, 1, 0), "cyan": (0, 1, 1), "magenta": (1, 0, 1),
         "orange": (1, 0.5, 0), "purple": (0.5, 0, 1), "pink": (1, 0.3, 0.6),
         "warm_white": (1, 0.8, 0.55), "gray": (0.5, 0.5, 0.5), "grey": (0.5, 0.5, 0.5)}


def rgb(r, g, b):
    """Color from 0-255 values."""
    return (r / 255.0, g / 255.0, b / 255.0)


def _hex(s):
    s = s.strip().lstrip("#")
    if len(s) == 3:
        s = "".join(ch * 2 for ch in s)
    return (int(s[0:2], 16) / 255.0, int(s[2:4], 16) / 255.0, int(s[4:6], 16) / 255.0)


def to_color(c):
    """Any color form -> float32 array (3,), 0..1 (more for extra-bright add)."""
    if isinstance(c, str):
        key = c.strip().lower().replace(" ", "_")
        return np.array(NAMED[key] if key in NAMED else _hex(c), np.float32)
    if isinstance(c, (int, float, np.number)):
        return np.array((c, c, c), np.float32)
    a = np.asarray(c, np.float32).reshape(-1)[:3]
    if a.size == 1:
        a = np.repeat(a, 3)
    if a.max(initial=0) > 1.0 and a.max() <= 255.0 and float(a.max()).is_integer():
        a = a / 255.0                       # (255, 0, 0) style
    return a


def hsv(h, s=1.0, v=1.0):
    """Hue 0..1 (wraps round), saturation 0..1, brightness 0..1 -> (r, g, b).
    Works on single numbers and on whole arrays."""
    h = np.mod(h, 1.0) * 6.0
    i = np.floor(h)
    f = h - i
    p = v * (1 - s)
    q = v * (1 - s * f)
    u = v * (1 - s * (1 - f))
    i = np.mod(i, 6)
    r = np.select([i == 0, i == 1, i == 2, i == 3, i == 4], [v, q, p, p, u], v)
    g = np.select([i == 0, i == 1, i == 2, i == 3, i == 4], [u, v, v, q, p], p)
    b = np.select([i == 0, i == 1, i == 2, i == 3, i == 4], [p, p, u, v, v], q)
    if np.ndim(r) == 0:
        return (float(r), float(g), float(b))
    return (r, g, b)


PALETTES = {   # stops (position 0..1, color); palettes wrap round
    "rainbow": [(0, "#ff0000"), (1 / 6, "#ffff00"), (2 / 6, "#00ff00"), (3 / 6, "#00ffff"),
                (4 / 6, "#0000ff"), (5 / 6, "#ff00ff"), (1, "#ff0000")],
    "fire": [(0, "#000000"), (0.25, "#800000"), (0.5, "#ff3000"), (0.75, "#ffb000"), (0.92, "#ffff80"),
             (1, "#000000")],
    "lava": [(0, "#000000"), (0.2, "#5a0000"), (0.45, "#ff2000"), (0.6, "#ff8000"), (0.75, "#ff2000"),
             (0.9, "#5a0000"), (1, "#000000")],
    "ocean": [(0, "#000030"), (0.3, "#0030a0"), (0.55, "#00a0c0"), (0.75, "#80ffe0"), (1, "#000030")],
    "forest": [(0, "#002000"), (0.35, "#106000"), (0.6, "#60b000"), (0.8, "#d0e060"), (1, "#002000")],
    "party": [(0, "#5500ab"), (0.2, "#c4007a"), (0.4, "#ff2a00"), (0.55, "#ffaa00"), (0.7, "#ff0060"),
              (0.85, "#2000ff"), (1, "#5500ab")],
    "sunset": [(0, "#200040"), (0.3, "#a01060"), (0.55, "#ff5020"), (0.75, "#ffc040"), (1, "#200040")],
    "ice": [(0, "#000010"), (0.35, "#0040ff"), (0.65, "#a0e0ff"), (0.85, "#ffffff"), (1, "#000010")],
    "neon": [(0, "#ff00ff"), (0.33, "#00ffff"), (0.66, "#80ff00"), (1, "#ff00ff")],
    "candy": [(0, "#ff60a0"), (0.33, "#ffe060"), (0.66, "#60c0ff"), (1, "#ff60a0")],
}
_PAL_CACHE = {}


def _palette_table(name):
    if name not in _PAL_CACHE:
        stops = PALETTES[name]
        pos = np.array([p for p, _ in stops])
        cols = np.array([_hex(c) for _, c in stops])
        xs = np.linspace(0, 1, 1024)
        _PAL_CACHE[name] = np.stack([np.interp(xs, pos, cols[:, k]) for k in range(3)], 1).astype(np.float32)
    return _PAL_CACHE[name]


def palette(name, x):
    """Color from a named palette at position x (0..1, wraps round).
    Works on single numbers and arrays. Names: see PALETTES."""
    tab = _palette_table(name)
    i = (np.mod(x, 1.0) * 1023).astype(np.int32) if np.ndim(x) else int(math.fmod(x, 1.0) % 1.0 * 1023)
    c = tab[i]
    if np.ndim(x) == 0:
        return (float(c[0]), float(c[1]), float(c[2]))
    return (c[..., 0], c[..., 1], c[..., 2])


def mix(a, b, k):
    """Blend from a (k=0) to b (k=1). Numbers, colors or arrays."""
    if isinstance(a, (str, tuple, list)) and isinstance(b, (str, tuple, list)):
        a, b = to_color(a), to_color(b)
        r = a + (b - a) * k
        return tuple(float(v) for v in r) if np.ndim(k) == 0 else tuple(r)
    return a + (b - a) * k


lerp = mix


# ---------------------------------------------------------------------------
# Math helpers (all work on numbers and numpy arrays)
# ---------------------------------------------------------------------------
def clamp(v, lo=0.0, hi=1.0):
    return np.clip(v, lo, hi) if np.ndim(v) else max(lo, min(hi, v))


def fract(v):
    return v - np.floor(v)


def smoothstep(e0, e1, v):
    k = clamp((v - e0) / (e1 - e0))
    return k * k * (3 - 2 * k)


def pingpong(v):
    """0 -> 1 -> 0 as v goes 0 -> 1 -> 2 ..."""
    return 1 - np.abs(np.mod(v, 2.0) - 1)


def wave(v):
    """Smooth 0..1 wave, one cycle per 1.0 of v."""
    return 0.5 - 0.5 * np.cos(v * 2 * np.pi)


def rand(a=1.0, b=None, n=None):
    """rand() 0..1, rand(5) 0..5, rand(2, 5) 2..5; n= for an array of n values."""
    lo, hi = (0.0, a) if b is None else (a, b)
    if n is None:
        return random.uniform(lo, hi)
    return np.random.uniform(lo, hi, n).astype(np.float32)


def randint(a, b=None):
    """randint(6) 0..5, randint(1, 6) 1..6"""
    return random.randrange(a) if b is None else random.randint(a, b)


_P = np.random.RandomState(1975).permutation(256)
_P = np.concatenate([_P, _P]).astype(np.int32)
_G = np.array([(1, 1, 0), (-1, 1, 0), (1, -1, 0), (-1, -1, 0), (1, 0, 1), (-1, 0, 1), (1, 0, -1), (-1, 0, -1),
               (0, 1, 1), (0, -1, 1), (0, 1, -1), (0, -1, -1), (1, 1, 0), (-1, 1, 0), (0, -1, 1), (0, -1, -1)],
              np.float32)


def noise(x, y=0.0, z=0.0):
    """Smooth Perlin noise, about 0..1, changes over ~1.0 of x, y or z.
    Works on numbers and arrays (use z = time for moving noise)."""
    x, y, z = (np.asarray(v, np.float32) for v in (x, y, z))
    xi, yi, zi = np.floor(x), np.floor(y), np.floor(z)
    xf, yf, zf = x - xi, y - yi, z - zi
    xi, yi, zi = (v.astype(np.int32) & 255 for v in (xi, yi, zi))
    u, v, w = (k * k * k * (k * (k * 6 - 15) + 10) for k in (xf, yf, zf))

    def g(ix, iy, iz, dx, dy, dz):
        gr = _G[_P[_P[_P[ix] + iy] + iz] & 15]
        return gr[..., 0] * dx + gr[..., 1] * dy + gr[..., 2] * dz
    x1, y1, z1 = (xi + 1) & 255, (yi + 1) & 255, (zi + 1) & 255
    n000 = g(xi, yi, zi, xf, yf, zf)
    n100 = g(x1, yi, zi, xf - 1, yf, zf)
    n010 = g(xi, y1, zi, xf, yf - 1, zf)
    n110 = g(x1, y1, zi, xf - 1, yf - 1, zf)
    n001 = g(xi, yi, z1, xf, yf, zf - 1)
    n101 = g(x1, yi, z1, xf - 1, yf, zf - 1)
    n011 = g(xi, y1, z1, xf, yf - 1, zf - 1)
    n111 = g(x1, y1, z1, xf - 1, yf - 1, zf - 1)
    nx00 = n000 + u * (n100 - n000)
    nx10 = n010 + u * (n110 - n010)
    nx01 = n001 + u * (n101 - n001)
    nx11 = n011 + u * (n111 - n011)
    nxy0 = nx00 + v * (nx10 - nx00)
    nxy1 = nx01 + v * (nx11 - nx01)
    r = np.clip(0.5 + 0.5 * (nxy0 + w * (nxy1 - nxy0)), 0, 1)
    return float(r) if r.ndim == 0 else r


def fbm(x, y=0.0, z=0.0, octaves=4):
    """Layered noise: more detail ("clouds", "smoke"). About 0..1."""
    total, amp, freq, norm = 0.0, 1.0, 1.0, 0.0
    for _ in range(int(octaves)):
        total = total + amp * noise(np.multiply(x, freq), np.multiply(y, freq), np.add(np.multiply(z, freq), _ * 7.31))
        norm += amp
        amp *= 0.5
        freq *= 2.0
    return total / norm


# ---------------------------------------------------------------------------
# Settings shown on the item: params = {"name": slider(...), ...}
# ---------------------------------------------------------------------------
class Param:
    kind = ""

    def value(self, v):
        return self.default if v is None else v


class slider(Param):
    """A number between lo and hi. step=1 gives whole numbers."""
    kind = "slider"

    def __init__(self, default, lo=0.0, hi=1.0, step=None, label=None):
        self.default, self.lo, self.hi, self.label = default, lo, hi, label
        self.step = step if step is not None else (1 if all(isinstance(v, int) for v in (default, lo, hi)) else None)

    def value(self, v):
        try:
            v = float(self.default if v is None else v)
        except (TypeError, ValueError):
            v = float(self.default)
        v = min(max(v, self.lo), self.hi)
        return int(round(v)) if self.step == 1 else v


class color_picker(Param):
    """A color; the variable holds (r, g, b) 0..1."""
    kind = "color"

    def __init__(self, default="#ffffff", label=None):
        self.default, self.label = default if isinstance(default, str) else _to_hex(default), label

    def value(self, v):
        try:
            return tuple(float(k) for k in to_color(self.default if v is None else v))
        except (ValueError, KeyError, TypeError):
            return tuple(float(k) for k in to_color(self.default))


class dropdown(Param):
    """One of a list of choices (text)."""
    kind = "dropdown"

    def __init__(self, *choices, default=None, label=None):
        if len(choices) == 1 and isinstance(choices[0], (list, tuple)):
            choices = tuple(choices[0])
        self.choices = [str(c) for c in choices]
        self.default = str(default) if default is not None else self.choices[0]
        self.label = label

    def value(self, v):
        return v if v in self.choices else self.default


class checkbox(Param):
    """On or off (True / False)."""
    kind = "checkbox"

    def __init__(self, default=False, label=None):
        self.default, self.label = bool(default), label

    def value(self, v):
        return self.default if v is None else bool(v)


class textbox(Param):
    """A line of text. secret=True hides what's typed (for API keys)."""
    kind = "text"

    def __init__(self, default="", label=None, secret=False):
        self.default, self.label, self.secret = str(default), label, bool(secret)

    def value(self, v):
        return self.default if v is None else str(v)


def _to_hex(c):
    c = to_color(c)
    return "#%02x%02x%02x" % tuple(int(round(min(max(float(k), 0), 1) * 255)) for k in c)


# ---------------------------------------------------------------------------
# The canvas
# ---------------------------------------------------------------------------
FONT_FILES = {"inter": "Inter-Bold.ttf", "archivo black": "ArchivoBlack-Regular.ttf", "anton": "Anton-Regular.ttf",
              "dejavu sans": "DejaVuSans-Bold.ttf", "noto sans": "NotoSans-Bold.ttf",
              "barlow semi condensed": "BarlowSemiCondensed-SemiBold.ttf", "jetbrains mono": "JetBrainsMono-Bold.ttf",
              "comic neue": "ComicNeue-Bold.ttf", "press start 2p": "PressStart2P-Regular.ttf"}
FONT_TITLES = ["Inter", "Archivo Black", "Anton", "DejaVu Sans", "Noto Sans", "Barlow Semi Condensed",
               "JetBrains Mono", "Comic Neue", "Press Start 2P"]
_TEXT_CACHE = {}


def _font_path(font):
    key = str(font).lower().replace("_", " ").replace(" bold", "").strip()
    f = FONT_FILES.get(key)
    return os.path.join(FONTS_DIR, f) if f else None


def font_names():
    return list(FONT_TITLES)


def _text_mask(s, size, font):
    key = (s, int(size), font)
    m = _TEXT_CACHE.get(key)
    if m is None:
        from PIL import Image, ImageDraw, ImageFont
        path = _font_path(font)
        try:
            f = ImageFont.truetype(path, max(4, int(size))) if path else ImageFont.load_default(max(4, int(size)))
        except (OSError, TypeError):
            f = ImageFont.load_default()
        x0, y0, x1, y1 = f.getbbox(s or " ")
        asc, desc = f.getmetrics()
        img = Image.new("L", (max(1, x1 - min(0, x0) + 2), max(1, asc + desc)), 0)
        ImageDraw.Draw(img).text((-min(0, x0) + 1, 0), s, fill=255, font=f)
        m = np.asarray(img, np.uint8)
        if len(_TEXT_CACHE) > 256:
            _TEXT_CACHE.clear()
        _TEXT_CACHE[key] = m
    return m


class Canvas:
    """What frame(c, t) draws on. Pixel coordinates: x 0..w (left to right),
    y 0..h (top to bottom); in-between values are drawn smoothly."""

    SHIFT = 4                       # sub-pixel bits for OpenCV drawing

    def __init__(self, W, H):
        self.w, self.h = W, H
        self.img = np.zeros((H, W, 3), np.float32)     # RGB, 0..1; draw on it directly if you like
        self.t = 0.0
        self.dt = 0.0
        self.frame = 0
        gy, gx = np.mgrid[0:H, 0:W].astype(np.float32)
        self.px, self.py = gx + 0.5, gy + 0.5          # pixel centres
        self.x, self.y = self.px / W, self.py / H      # 0..1 across and down
        self._post = []
        self._images = {}
        self.folder = HERE

    # ---- whole canvas --------------------------------------------------
    def clear(self, color="black"):
        self.img[:] = to_color(color)

    fill = clear

    def fade(self, keep=0.85):
        """Darken everything: keep=0.85 keeps 85% each frame (trails)."""
        self.img *= keep

    def set(self, color):
        """Whole canvas from a color made of arrays, e.g. c.set(hsv(c.x + t, 1, 1))."""
        if isinstance(color, np.ndarray) and color.ndim == 3:
            self.img[:] = color[..., :3]
            return
        r, g, b = color if isinstance(color, (tuple, list)) else (color, color, color)
        for k, ch in enumerate((r, g, b)):
            self.img[..., k] = ch

    def get(self, x, y):
        """Color at a pixel as (r, g, b)."""
        x, y = int(x) % self.w, int(y) % self.h
        return tuple(float(v) for v in self.img[y, x])

    # ---- shapes ----------------------------------------------------------
    def _stamp(self, x0, y0, x1, y1, draw, color, alpha, blend):
        x0, y0 = max(0, int(math.floor(x0)) - 1), max(0, int(math.floor(y0)) - 1)
        x1, y1 = min(self.w, int(math.ceil(x1)) + 2), min(self.h, int(math.ceil(y1)) + 2)
        if x1 <= x0 or y1 <= y0:
            return
        mask = np.zeros((y1 - y0, x1 - x0), np.uint8)
        draw(mask, x0, y0)
        self._apply(mask, x0, y0, color, alpha, blend)

    def _apply(self, mask, x0, y0, color, alpha, blend):
        roi = self.img[y0:y0 + mask.shape[0], x0:x0 + mask.shape[1]]
        m = mask.astype(np.float32)[..., None] * (alpha / 255.0)
        col = to_color(color)
        if blend == "add":
            roi += col * m
        elif blend == "max":
            np.maximum(roi, col * m, out=roi)
        else:
            roi += (col - roi) * m

    def _p(self, v, ox=0):
        return int(round((v - ox) * (1 << self.SHIFT)))

    def circle(self, x, y, r, color="white", width=None, alpha=1.0, blend="normal"):
        """Circle centred on x, y. width=None fills it; a number draws a ring."""
        s = self.SHIFT
        lw = -1 if width is None else max(1, int(round(width)))
        pad = r + (width or 0)

        def draw(m, ox, oy):
            cv2.circle(m, (self._p(x - 0.5, ox), self._p(y - 0.5, oy)), max(0, self._p(r)), 255, lw, cv2.LINE_AA, s)
        self._stamp(x - pad, y - pad, x + pad, y + pad, draw, color, alpha, blend)

    def line(self, x1, y1, x2, y2, color="white", width=1, alpha=1.0, blend="normal"):
        """Line from x1, y1 to x2, y2."""
        s, pad = self.SHIFT, width

        def draw(m, ox, oy):
            cv2.line(m, (self._p(x1 - 0.5, ox), self._p(y1 - 0.5, oy)), (self._p(x2 - 0.5, ox), self._p(y2 - 0.5, oy)),
                     255, max(1, int(round(width))), cv2.LINE_AA, s)
        self._stamp(min(x1, x2) - pad, min(y1, y2) - pad, max(x1, x2) + pad, max(y1, y2) + pad,
                    draw, color, alpha, blend)

    def rect(self, x, y, w, h, color="white", width=None, alpha=1.0, blend="normal"):
        """Rectangle with its top-left corner at x, y. width=None fills it."""
        self.polygon([(x, y), (x + w, y), (x + w, y + h), (x, y + h)], color, width, alpha, blend)

    def polygon(self, points, color="white", width=None, alpha=1.0, blend="normal"):
        """Shape through a list of (x, y) points. width=None fills it."""
        pts = np.asarray(points, np.float64).reshape(-1, 2)
        if len(pts) < 2:
            return
        s = self.SHIFT
        pad = width or 1

        def draw(m, ox, oy):
            p = np.round((pts - 0.5 - (ox, oy)) * (1 << s)).astype(np.int32)
            if width is None:
                cv2.fillPoly(m, [p], 255, cv2.LINE_AA, s)
            else:
                cv2.polylines(m, [p], True, 255, max(1, int(round(width))), cv2.LINE_AA, s)
        self._stamp(pts[:, 0].min() - pad, pts[:, 1].min() - pad, pts[:, 0].max() + pad, pts[:, 1].max() + pad,
                    draw, color, alpha, blend)

    def dot(self, x, y, color="white", alpha=1.0, blend="add"):
        """One smooth pixel (spread over its neighbours when between pixels)."""
        self.points([x], [y], color, alpha=alpha, blend=blend)

    def points(self, xs, ys, colors="white", size=1, alpha=1.0, blend="add"):
        """Lots of dots at once - fast enough for thousands of particles.
        colors: one color, or an array of (r, g, b) rows, one per dot.
        size > 1.5 draws small circles instead (slower)."""
        xs = np.asarray(xs, np.float32).reshape(-1) - 0.5
        ys = np.asarray(ys, np.float32).reshape(-1) - 0.5
        if xs.size == 0:
            return
        cols = np.asarray(colors, np.float32) if not isinstance(colors, str) else None
        if cols is None or cols.ndim == 1:
            cols = np.broadcast_to(to_color(colors), (xs.size, 3))
        elif cols.ndim == 2 and cols.shape[1] == 3 and cols.max(initial=0) > 1 and cols.max() <= 255 \
                and np.all(np.mod(cols, 1) == 0):
            cols = cols / 255.0
        if size > 1.5:
            for x, y, col in zip(xs, ys, cols):
                self.circle(x + 0.5, y + 0.5, size / 2, col, alpha=alpha, blend=blend)
            return
        x0, y0 = np.floor(xs).astype(np.int32), np.floor(ys).astype(np.int32)
        fx, fy = xs - x0, ys - y0
        acc = np.zeros_like(self.img)
        wsum = np.zeros(self.img.shape[:2], np.float32)
        for dx, dy, wgt in ((0, 0, (1 - fx) * (1 - fy)), (1, 0, fx * (1 - fy)), (0, 1, (1 - fx) * fy), (1, 1, fx * fy)):
            px, py = x0 + dx, y0 + dy
            ok = (px >= 0) & (px < self.w) & (py >= 0) & (py < self.h)
            wk = wgt[ok] * alpha
            np.add.at(acc, (py[ok], px[ok]), cols[ok] * wk[:, None])
            np.add.at(wsum, (py[ok], px[ok]), wk)
        if blend == "add":
            self.img += acc
        elif blend == "max":
            np.maximum(self.img, acc, out=self.img)
        else:
            k = np.minimum(wsum, 1.0)[..., None]
            avg = acc / np.maximum(wsum, 1e-6)[..., None]
            self.img += (avg - self.img) * k

    def text(self, s, x=None, y=None, color="white", size=None, font="Inter", align="center", valign="middle",
             alpha=1.0, blend="normal"):
        """Text at x, y (default: the middle). size = letter height in pixels
        (default 40% of the height). align: left / center / right;
        valign: top / middle / bottom. Fonts: see font_names()."""
        size = size or self.h * 0.4
        m = _text_mask(str(s), size, font)
        h, w = m.shape
        x = self.w / 2 if x is None else x
        y = self.h / 2 if y is None else y
        x0 = x - (w / 2 if align == "center" else w if align == "right" else 0)
        y0 = y - (h / 2 if valign == "middle" else h if valign == "bottom" else 0)
        self._blit_mask(m, int(round(x0)), int(round(y0)), color, alpha, blend)

    def text_width(self, s, size=None, font="Inter"):
        return _text_mask(str(s), size or self.h * 0.4, font).shape[1]

    def fit_size(self, s, max_w, max_h, font="Inter"):
        """Biggest text size (up to max_h) at which s fits in max_w pixels."""
        lo, hi = 4, max(4, int(max_h))
        if self.text_width(s, hi, font) <= max_w:
            return hi
        while hi - lo > 1:
            mid = (lo + hi) // 2
            lo, hi = (mid, hi) if self.text_width(s, mid, font) <= max_w else (lo, mid)
        return lo

    def _blit_mask(self, m, x0, y0, color, alpha, blend):
        h, w = m.shape
        sx0, sy0 = max(0, -x0), max(0, -y0)
        dx0, dy0 = max(0, x0), max(0, y0)
        dx1, dy1 = min(self.w, x0 + w), min(self.h, y0 + h)
        if dx1 <= dx0 or dy1 <= dy0:
            return
        self._apply(m[sy0:sy0 + dy1 - dy0, sx0:sx0 + dx1 - dx0], dx0, dy0, color, alpha, blend)

    def image(self, path, x=0, y=0, w=None, h=None, alpha=1.0):
        """Draw a picture file (path next to the script, or a full path).
        PNG transparency is kept. w/h scale it (one of them keeps the shape)."""
        full = path if os.path.isabs(path) else os.path.join(self.folder, path)
        key = (full, w, h)
        pic = self._images.get(key)
        if pic is None:
            raw = cv2.imdecode(np.fromfile(full, np.uint8), cv2.IMREAD_UNCHANGED)
            if raw is None:
                raise IOError(f"can't open picture {path}")
            if raw.ndim == 2:
                raw = cv2.cvtColor(raw, cv2.COLOR_GRAY2BGR)
            ih, iw = raw.shape[:2]
            if w or h:
                w2 = int(w or iw * h / ih)
                h2 = int(h or ih * w / iw)
                raw = cv2.resize(raw, (max(1, w2), max(1, h2)), interpolation=cv2.INTER_AREA)
            rgb_ = raw[..., 2::-1].astype(np.float32) / 255
            a = raw[..., 3].astype(np.float32) / 255 if raw.shape[2] == 4 else np.ones(raw.shape[:2], np.float32)
            pic = self._images[key] = (rgb_, a)
        rgb_, a = pic
        ph, pw = a.shape
        x0, y0 = int(round(x)), int(round(y))
        sx0, sy0 = max(0, -x0), max(0, -y0)
        dx0, dy0 = max(0, x0), max(0, y0)
        dx1, dy1 = min(self.w, x0 + pw), min(self.h, y0 + ph)
        if dx1 <= dx0 or dy1 <= dy0:
            return
        roi = self.img[dy0:dy1, dx0:dx1]
        k = a[sy0:sy0 + dy1 - dy0, sx0:sx0 + dx1 - dx0, None] * alpha
        roi += (rgb_[sy0:sy0 + dy1 - dy0, sx0:sx0 + dx1 - dx0] - roi) * k

    # ---- moving and changing the picture -------------------------------
    def scroll(self, dx, dy=0, wrap=True):
        """Move everything by dx, dy pixels (wrap=True brings it round the other side)."""
        if float(dx).is_integer() and float(dy).is_integer():
            dx, dy = int(dx), int(dy)
            if wrap:
                self.img[:] = np.roll(self.img, (dy, dx), (0, 1))
                return
            out = np.zeros_like(self.img)
            ys, yd = (slice(0, self.h - dy), slice(dy, self.h)) if dy >= 0 else (slice(-dy, self.h), slice(0, self.h + dy))
            xs, xd = (slice(0, self.w - dx), slice(dx, self.w)) if dx >= 0 else (slice(-dx, self.w), slice(0, self.w + dx))
            out[yd, xd] = self.img[ys, xs]
            self.img[:] = out
            return
        m = np.float32([[1, 0, dx], [0, 1, dy]])
        self.img[:] = cv2.warpAffine(self.img, m, (self.w, self.h), flags=cv2.INTER_LINEAR,
                                     borderMode=cv2.BORDER_WRAP if wrap else cv2.BORDER_CONSTANT)

    def zoom(self, scale=1.02, angle=0.0, x=None, y=None):
        """Grow (scale > 1) or shrink and turn (angle in degrees) the picture
        around x, y (default: the middle). Called every frame with fade() it
        makes tunnels and spirals."""
        x = self.w / 2 if x is None else x
        y = self.h / 2 if y is None else y
        m = cv2.getRotationMatrix2D((x - 0.5, y - 0.5), angle, scale)
        self.img[:] = cv2.warpAffine(self.img, m, (self.w, self.h), flags=cv2.INTER_LINEAR,
                                     borderMode=cv2.BORDER_CONSTANT)

    def blur(self, radius=1.0):
        """Soften the picture."""
        if radius > 0:
            cv2.GaussianBlur(self.img, (0, 0), radius, dst=self.img)

    def mirror(self, how="x"):
        """Copy the left half onto the right, flipped ("x"), the top half onto
        the bottom ("y"), or both ("both")."""
        if how in ("x", "both"):
            half = self.w // 2
            self.img[:, self.w - half:] = self.img[:, :half][:, ::-1]
        if how in ("y", "both"):
            half = self.h // 2
            self.img[self.h - half:] = self.img[:half][::-1]

    def kaleido(self, segments=6, angle=0.0):
        """Kaleidoscope: the wedge to the right of the middle is repeated
        round the circle. angle (degrees) turns the wedge."""
        cx, cy = self.w / 2, self.h / 2
        dx, dy = self.px - cx, self.py - cy
        r = np.hypot(dx, dy)
        a = np.arctan2(dy, dx) - math.radians(angle)
        seg = 2 * np.pi / max(1, int(segments))
        a = np.mod(a, seg)
        a = np.where(a > seg / 2, seg - a, a) + math.radians(angle)
        mx = (cx + r * np.cos(a) - 0.5).astype(np.float32)
        my = (cy + r * np.sin(a) - 0.5).astype(np.float32)
        self.img[:] = cv2.remap(self.img, mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)

    def glow(self, strength=0.6, radius=None):
        """Bloom around bright things. Only changes what is shown, not the
        canvas, so trails don't build up."""
        self._post.append(("glow", strength, radius or max(1.0, min(self.w, self.h) / 40)))

    # ---- output ------------------------------------------------------------
    def output(self):
        img = self.img
        for name, strength, radius in self._post:
            if name == "glow":
                img = img + cv2.GaussianBlur(img, (0, 0), radius) * strength
        self._post = []
        out = np.clip(img[..., ::-1] * 255.0, 0, 255).astype(np.uint8)
        return out


# ---------------------------------------------------------------------------
# Loading and running a script
# ---------------------------------------------------------------------------
HELPERS = dict(
    math=math, np=np, numpy=np, random=random, cv2=cv2,
    sin=np.sin, cos=np.cos, tan=np.tan, atan2=np.arctan2, sqrt=np.sqrt, exp=np.exp, log=np.log,
    floor=np.floor, ceil=np.ceil, hypot=np.hypot, pi=math.pi, tau=2 * math.pi,
    clamp=clamp, fract=fract, smoothstep=smoothstep, pingpong=pingpong, wave=wave, mix=mix, lerp=lerp,
    rand=rand, randint=randint, noise=noise, fbm=fbm,
    rgb=rgb, hsv=hsv, palette=palette, to_color=to_color, PALETTES=PALETTES,
    slider=slider, color_picker=color_picker, dropdown=dropdown, checkbox=checkbox, textbox=textbox,
    **{k: v for k, v in NAMED.items()},
    **datasources.HELPERS,
)


def _hash(path):
    try:
        with open(path, "rb") as f:
            return hashlib.sha1(f.read()).hexdigest()
    except OSError:
        return None


def _copies():
    try:
        with open(COPIES, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def unedited_copy(path):
    """True if path is an example copied to My scripts and not changed since."""
    h = _copies().get(os.path.basename(path))
    return h is not None and h == _hash(path)


def copy_example(path):
    """Path of the user's own copy of a script, copying an example there first
    (again if the old copy was never edited, so it's the current version)."""
    if os.path.normcase(os.path.dirname(path)) == os.path.normcase(USER_DIR):
        return path
    os.makedirs(USER_DIR, exist_ok=True)
    dst = os.path.join(USER_DIR, os.path.basename(path))
    if not os.path.exists(dst) or unedited_copy(dst):
        shutil.copyfile(path, dst)
        d = _copies()
        d[os.path.basename(dst)] = _hash(dst)
        try:
            with open(COPIES, "w", encoding="utf-8") as f:
                json.dump(d, f, indent=1)
        except OSError:
            pass
    return dst


def resolve(name):
    """Script path from what an item stores (full path or just a file name).
    My scripts wins over the examples, except a copy of an example that was
    never edited: that one gives the example, so updates to it show up."""
    if os.path.isabs(name) and os.path.isfile(name):
        return name
    base = os.path.basename(name)
    mine, example = os.path.join(USER_DIR, base), os.path.join(EXAMPLES_DIR, base)
    if os.path.isfile(mine) and not (os.path.isfile(example) and unedited_copy(mine)):
        return mine
    return example if os.path.isfile(example) else name

def list_scripts():
    """[(group, path)] - examples first, then the user's own."""
    out = []
    for group, d in (("Examples", EXAMPLES_DIR), ("My scripts", USER_DIR)):
        if os.path.isdir(d):
            for f in sorted(os.listdir(d), key=str.lower):
                if f.endswith(".py") and not f.startswith("_"):
                    out.append((group, os.path.join(d, f)))
    return out


def _error_text(path, e):
    name = os.path.basename(path)
    if isinstance(e, SyntaxError):
        return f"{name} line {e.lineno}: {e.msg}"
    line = None
    for fr in traceback.extract_tb(e.__traceback__):
        if os.path.normcase(fr.filename) == os.path.normcase(path):
            line = fr.lineno
    where = f" line {line}" if line else ""
    return f"{name}{where}: {type(e).__name__}: {e}"


def error_line(text):
    """Line number in an error text from _error_text, or None."""
    import re
    m = re.search(r" line (\d+)", text or "")
    return int(m.group(1)) if m else None


def load(path):
    """Run the script file's top level; returns its namespace (raises on errors)."""
    with open(path, encoding="utf-8") as f:
        code = f.read()
    ns = dict(HELPERS)
    ns["__file__"] = path
    ns["__name__"] = "ddp_script"
    exec(compile(code, path, "exec"), ns)
    return ns


def info(path):
    """dict(title, about, params, error) for the script picker."""
    out = dict(title=os.path.splitext(os.path.basename(path))[0].replace("_", " ").capitalize(),
               about="", params={}, error="")
    try:
        ns = load(path)
    except Exception as e:
        out["error"] = _error_text(path, e)
        return out
    doc = (ns.get("__doc__") or "").strip()
    if doc:
        lines = doc.splitlines()
        out["title"] = lines[0].strip()
        paras = "\n".join(l.strip() for l in lines[1:]).strip().split("\n\n")
        out["about"] = "\n\n".join(" ".join(p.split()) for p in paras)     # one paragraph per blank line
    p = ns.get("params")
    if isinstance(p, dict):
        out["params"] = {k: v for k, v in p.items() if isinstance(v, Param)}
    return out


class ScriptGen:
    """Runs a script item: d = {"file": ..., "params": {...}}."""

    RELOAD_EVERY = 0.5          # s between checks for a saved change

    def __init__(self, d, W, H):
        self.W, self.H = W, H
        self.name = d.get("file", "")
        self.path = resolve(self.name)
        self.values = d.get("params") or {}
        self.error = ""
        self.ns = None
        self.mtime = None
        self.checked = 0.0
        self.last = np.zeros((H, W, 3), np.uint8)
        self._start()

    def _set_error(self, text):
        self.error = text
        ERRORS[self.path] = text

    def _start(self):
        """(Re)load the file and run setup. On an error the item shows the last
        good picture, dimmed with a red edge, until a fixed version is saved."""
        try:
            self.mtime = os.path.getmtime(self.path)
        except OSError:
            self._set_error(f"Script not found: {os.path.basename(self.path)}")
            return
        try:
            ns = load(self.path)
            params = ns.get("params") if isinstance(ns.get("params"), dict) else {}
            for k, p in params.items():
                if isinstance(p, Param):
                    ns[k] = p.value(self.values.get(k))
            ns["W"], ns["H"], ns["aspect"] = self.W, self.H, self.W / self.H
            c = Canvas(self.W, self.H)
            c.folder = os.path.dirname(self.path)
            if not callable(ns.get("pixel")) and not callable(ns.get("frame")):
                raise NameError("the script needs a pixel(x, y, t) or a frame(c, t) function")
            if callable(ns.get("setup")):
                ns["setup"](c)
            self.ns, self.c, self.prev_t = ns, c, None
            self._set_error("")
        except Exception as e:
            self.ns = None                      # show the error until the file is fixed
            self._set_error(_error_text(self.path, e))

    def frame(self, t):
        now = time.monotonic()
        if now - self.checked > self.RELOAD_EVERY:
            self.checked = now
            path = resolve(self.name)           # e.g. an example's copy was just edited: use it
            try:
                if path != self.path or os.path.getmtime(path) != self.mtime:
                    self.path = path
                    self._start()
            except OSError:
                pass
        if self.ns is None:
            return self._error_frame()
        c, ns = self.c, self.ns
        dt = 0.0 if self.prev_t is None else t - self.prev_t
        self.prev_t = t
        c.t, c.dt = t, min(max(dt, 0.0), 0.25)
        try:
            if callable(ns.get("pixel")):
                c.set(ns["pixel"](c.x, c.y, t))
            if callable(ns.get("frame")):
                ns["frame"](c, t)
            c.frame += 1
            self.last = c.output()
            if self.error:
                self._set_error("")
        except Exception as e:
            self._set_error(_error_text(self.path, e))
            c._post = []
            return self._error_frame()
        return self.last

    def _error_frame(self):
        """Last good picture, dimmed, with a red edge so the wall shows something is wrong."""
        out = (self.last * 0.35).astype(np.uint8)
        k = max(1, min(self.W, self.H) // 32)
        out[:k], out[-k:], out[:, :k], out[:, -k:] = (0, 0, 200), (0, 0, 200), (0, 0, 200), (0, 0, 200)
        return out


def render_test(path, W=128, H=64, seconds=3.0, fps=30, params=None):
    """Run a script without the app: (frames, seconds per frame, error)."""
    g = ScriptGen({"file": path, "params": params or {}}, W, H)
    frames, t0 = [], time.perf_counter()
    n = int(seconds * fps)
    for i in range(n):
        frames.append(g.frame(i / fps))
        if g.error:
            break
    return frames, (time.perf_counter() - t0) / max(1, len(frames)), g.error
