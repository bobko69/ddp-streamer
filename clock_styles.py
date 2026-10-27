"""Animated clock looks for the DDP Streamer's clock item (BGR numpy frames).
Each look: Style(W, H, color).frame(t, hhmm, ss) -> HxWx3 uint8, where hhmm is
"HH:MM" (a leading space hides a digit) and t is a steadily rising time in
seconds. Digits are a 5x7 pixel font (or 7 segments) scaled to fit; a digit
that changes gets the look's own transition. color: None = the look's own
colors, a BGR tuple, or "rainbow"."""
import math
import numpy as np
import cv2

FONT = {  # 5x7, rows top to bottom
    "0": ["01110", "10001", "10001", "10001", "10001", "10001", "01110"],
    "1": ["00100", "01100", "00100", "00100", "00100", "00100", "01110"],
    "2": ["01110", "10001", "00001", "00010", "00100", "01000", "11111"],
    "3": ["11111", "00010", "00100", "00010", "00001", "10001", "01110"],
    "4": ["00010", "00110", "01010", "10010", "11111", "00010", "00010"],
    "5": ["11111", "10000", "11110", "00001", "00001", "10001", "01110"],
    "6": ["00110", "01000", "10000", "11110", "10001", "10001", "01110"],
    "7": ["11111", "00001", "00010", "00100", "01000", "01000", "01000"],
    "8": ["01110", "10001", "10001", "01110", "10001", "10001", "01110"],
    "9": ["01110", "10001", "10001", "01111", "00001", "00010", "01100"],
    " ": ["00000"] * 7,
}
GLYPH = {k: np.array([[c == "1" for c in r] for r in v], bool) for k, v in FONT.items()}


class Layout:
    """Where the four digits and the colon sit, in blocks of s x s pixels."""

    def __init__(self, W, H):
        self.W, self.H = W, H
        # 4 digits of 5 + 1 gap each + colon 1 + 2 gaps -> 4*5 + 3 + 1 + 2 = 26 block columns
        self.s = s = max(1, min(int(W * 0.9) // 26, int(H * 0.62) // 7))
        total = 26 * s
        self.x0 = (W - total) // 2
        self.y0 = (H - 7 * s) // 2
        xs, x = [], self.x0
        for i in range(4):
            xs.append(x)
            x += 6 * s
            if i == 1:
                self.colon_x = x
                x += 2 * s
        self.dx = xs

    def blocks(self, i, ch):
        """(x, y) top-left pixel of each lit block of digit i showing ch."""
        g = GLYPH[ch]
        ys, xs = np.nonzero(g)
        return [(self.dx[i] + c * self.s, self.y0 + r * self.s) for r, c in zip(ys, xs)]

    def colon(self):
        s = self.s
        return [(self.colon_x, self.y0 + 2 * s), (self.colon_x, self.y0 + 4 * s)]


def rect(img, x, y, s, color, inset=0):
    x, y = int(x), int(y)
    cv2.rectangle(img, (x + inset, y + inset), (x + s - 1 - inset, y + s - 1 - inset), color, -1)


def lighten(col, k):
    """Mix a BGR color k of the way toward white."""
    return tuple(int(v + (255 - v) * k) for v in col)


def dim(col, k):
    return tuple(int(v * k) for v in col)


def digits_of(hhmm):
    return [hhmm[0], hhmm[1], hhmm[3], hhmm[4]]


class Base:
    TRANS = 1.2   # seconds a digit change takes

    def __init__(self, W, H, color=None):
        """color: None = the style's own colors, a BGR tuple, or "rainbow"."""
        self.W, self.H = W, H
        self.color = color
        self.L = Layout(W, H)
        self.shown = None          # digits currently settled
        self.prev = [" "] * 4
        self.changed_at = [-99.0] * 4

    def track(self, t, hhmm):
        d = digits_of(hhmm)
        if self.shown is None:
            self.shown = d
        for i in range(4):
            if d[i] != self.shown[i]:
                self.prev[i] = self.shown[i]
                self.shown[i] = d[i]
                self.changed_at[i] = t + i * 0.12      # ripple left to right
        return d

    def c(self, t, i, default):
        """Color for digit i: the chosen color, a slowly cycling rainbow, or the style's default."""
        if isinstance(self.color, tuple):
            return self.color
        if self.color == "rainbow":
            h = int(t * 20 + i * 25) % 180
            return tuple(int(v) for v in cv2.cvtColor(np.uint8([[[h, 210, 255]]]), cv2.COLOR_HSV2BGR)[0, 0])
        return default

    def phase(self, t, i):
        """0..1 progress of digit i's change, or None when settled."""
        p = (t - self.changed_at[i]) / self.TRANS
        return p if 0 <= p < 1 else (None if p >= 1 else 0.0)


# ---------------------------------------------------------------- Code Rain
class CodeRain(Base):
    TRANS = 1.6

    def __init__(self, W, H, color=None):
        super().__init__(W, H, color)
        rng = np.random.default_rng(1)
        self.cols = W // 4
        self.head = rng.uniform(-H, H, self.cols)
        self.speed = rng.uniform(25, 70, self.cols)
        self.rng = rng
        self.last = None
        self.trail = np.zeros((H, W), np.float32)
        self.order = {}

    def frame(self, t, hhmm, ss):
        d = self.track(t, hhmm)
        dt = 0 if self.last is None else t - self.last
        self.last = t
        self.trail *= math.exp(-dt * 3.0)
        self.head += self.speed * dt
        wrap = self.head > self.H + 30
        self.head[wrap] = self.rng.uniform(-40, 0, wrap.sum())
        for c in range(self.cols):
            y = int(self.head[c])
            if 0 <= y < self.H:
                self.trail[y:y + 3, c * 4:c * 4 + 2] = 1.0
        base = self.c(t, 0, (110, 220, 60))
        rain = np.array(dim(base, 0.7), np.float32)
        img = (self.trail[..., None] * rain).astype(np.uint8)
        bright, mid, low = lighten(base, 0.55), lighten(base, 0.25), base
        s = self.L.s
        for i in range(4):
            p = self.phase(t, i)
            new = self.L.blocks(i, d[i])
            if p is None:
                for x, y in new:
                    rect(img, x, y, s, bright, 1)
                continue
            old = self.L.blocks(i, self.prev[i])
            key = (i, self.changed_at[i])
            if key not in self.order:
                r = np.random.default_rng(int(self.changed_at[i] * 10) + i)
                self.order[key] = (r.random(len(old)), r.random(len(new)))
            ro, rn = self.order[key]
            for (x, y), k in zip(old, ro):           # old blocks fall into the rain
                if k > p * 2:
                    rect(img, x, y, s, mid, 1)
                else:
                    fy = y + (p * 2 - k) * 60
                    if fy < self.H:
                        rect(img, x + s // 3, fy, max(1, s // 3), low)
            for (x, y), k in zip(new, rn):           # new blocks decode out of it
                if k < p * 1.6 - 0.4:
                    flick = 1.0 if (int(t * 20) + int(k * 9)) % 3 else 0.75
                    rect(img, x, y, s, dim(bright, flick), 1)
        if int(t * 2) % 2 == 0:
            for x, y in self.L.colon():
                rect(img, x, y, s, mid, 1)
        return img


# ---------------------------------------------------------------- Fire
class Fire(Base):
    TRANS = 1.5

    def __init__(self, W, H, color=None):
        super().__init__(W, H, color)
        self.h = H // 2
        self.w = W // 2
        self.heat = np.zeros((self.h + 2, self.w), np.float32)
        self.rng = np.random.default_rng(2)
        pal = np.zeros((256, 3), np.uint8)       # black -> red -> orange -> yellow -> white (BGR)
        for i in range(256):
            v = i / 255
            r = min(1, v * 3); g = min(1, max(0, v * 3 - 1)); b = min(1, max(0, v * 3 - 2))
            pal[i] = (int(b * 255), int(g * 255), int(r * 255))
        self.pal = pal

    @staticmethod
    def palette(col):
        """black -> the color -> white, for colored flames."""
        v = np.linspace(0, 1, 256)[:, None]
        c = np.array(col, np.float32)[None, :]
        lo = c * np.clip(v / 0.6, 0, 1)
        hi = c + (255 - c) * np.clip((v - 0.6) / 0.4, 0, 1)
        return np.where(v < 0.6, lo, hi).astype(np.uint8)

    def frame(self, t, hhmm, ss):
        d = self.track(t, hhmm)
        s = self.L.s
        pal = self.pal if self.color is None else self.palette(self.c(t, 0, None))
        src = np.zeros((self.H, self.W), np.float32)
        for i in range(4):
            p = self.phase(t, i)
            if p is None:
                cells, a = self.L.blocks(i, d[i]), 1.0
            elif p < 0.5:
                cells, a = self.L.blocks(i, self.prev[i]), 1 - p * 2       # old digit burns down
            else:
                cells, a = self.L.blocks(i, d[i]), (p - 0.5) * 2            # new digit ignites
            for x, y in cells:
                src[y:y + s, x:x + s] = a
        for x, y in self.L.colon():
            src[y:y + s, x:x + s] = 0.8
        small = cv2.resize(src, (self.w, self.h), interpolation=cv2.INTER_AREA)
        hm = self.heat
        hm[-1] = self.rng.uniform(0.3, 1.0, self.w) * 0.9            # glowing floor
        hm[:self.h] = np.maximum(hm[:self.h], small * self.rng.uniform(0.7, 1.0, small.shape))
        for _ in range(2):                                          # rise, spread, cool
            up = np.roll(hm, -1, axis=0)
            hm[:] = (up + np.roll(up, 1, axis=1) + np.roll(up, -1, axis=1) + hm) / 4.02 - 0.012
            np.clip(hm, 0, 1, out=hm)
        fire = pal[(np.clip(hm[:self.h], 0, 1) * 255).astype(np.uint8)]
        img = cv2.resize(fire, (self.W, self.H), interpolation=cv2.INTER_NEAREST)
        # digits stay readable: a dark halo cuts the flames around them, then the digits
        # are drawn on top, white-hot at the top fading to yellow at the bottom
        digit = (src > 0.5).astype(np.uint8)
        halo = cv2.dilate(digit, np.ones((2 * max(1, s // 2) + 1,) * 2, np.uint8))
        img[halo > 0] = (img[halo > 0] * 0.18).astype(np.uint8)
        ys = np.linspace(0, 1, self.H)[:, None]
        top = np.array([235, 250, 255], np.float32)
        bot = np.array([40, 200, 255] if self.color is None else lighten(self.c(t, 0, None), 0.35), np.float32)
        y0, y1 = self.L.y0, self.L.y0 + 7 * s
        k = np.clip((ys - y0) / max(1, y1 - y0), 0, 1)[..., None]
        col = (top * (1 - k) + bot * k).astype(np.uint8)
        a = np.clip(src, 0, 1)[..., None]
        img = np.where(digit[..., None] > 0, (col * a + img * (1 - a)).astype(np.uint8), img)
        return img


# ---------------------------------------------------------------- Block Drop
class BlockDrop(Base):
    TRANS = 1.4
    COLS = [(0x3a, 0xbd, 0xf7)[::-1], (0x81, 0x8c, 0xf8)[::-1], (0x2f, 0xd4, 0xbf)[::-1], (0xf4, 0xbf, 0x51)[::-1]]

    def frame(self, t, hhmm, ss):
        d = self.track(t, hhmm)
        img = np.zeros((self.H, self.W, 3), np.uint8)
        s = self.L.s
        for i in range(4):
            col = self.c(t, i, self.COLS[i])
            p = self.phase(t, i)
            new = self.L.blocks(i, d[i])
            if p is None:
                for x, y in new:
                    rect(img, x, y, s, col, 1)
                continue
            old = self.L.blocks(i, self.prev[i])
            n = len(new)
            for k, (x, y) in enumerate(sorted(old, key=lambda b: -b[1])):   # old falls away, bottom first
                start = k / max(1, len(old)) * 0.35
                q = max(0, p - start)
                fy = y + 400 * q * q
                if fy < self.H:
                    rect(img, x, fy, s, col, 1)
            for k, (x, y) in enumerate(sorted(new, key=lambda b: -b[1])):   # new lands bottom row first
                start = 0.3 + k / max(1, n) * 0.5
                q = (p - start) / 0.2
                if q >= 1:
                    rect(img, x, y, s, col, 1)
                elif q > 0:
                    rect(img, x, y - (1 - q) ** 2 * (y + s), s, col, 1)
        if int(t * 2) % 2 == 0:
            for x, y in self.L.colon():
                rect(img, x, y, s, (220, 220, 220), 1)
        return img


# ---------------------------------------------------------------- Flip
class Flip(Base):
    TRANS = 0.6

    def tile(self, ch, s, col=(235, 235, 235)):
        L = self.L
        w, h = 6 * s, 9 * s
        img = np.zeros((h, w, 3), np.uint8)
        cv2.rectangle(img, (0, 0), (w - 2, h - 1), (48, 34, 30), -1)             # card (BGR navy)
        for r, c in zip(*np.nonzero(GLYPH[ch])):
            rect(img, c * s + s // 2, r * s + s, s, col)
        cv2.line(img, (0, h // 2), (w - 2, h // 2), (10, 8, 8), max(1, s // 3))  # split line
        return img

    def frame(self, t, hhmm, ss):
        d = self.track(t, hhmm)
        L = self.L
        s = max(1, int(L.s * 0.8))
        w, h = 6 * s, 9 * s
        gap = s
        total = 4 * w + 3 * gap + 2 * s
        x0, y0 = (self.W - total) // 2, (self.H - h) // 2
        img = np.zeros((self.H, self.W, 3), np.uint8)
        x = x0
        for i in range(4):
            col = self.c(t, i, (235, 235, 235))
            new, p = self.tile(d[i], s, col), self.phase(t, i)
            if p is None:
                img[y0:y0 + h, x:x + w] = new
            else:
                old = self.tile(self.prev[i], s, col)
                half = h // 2
                img[y0:y0 + half, x:x + w] = new[:half]           # new top behind
                img[y0 + half:y0 + h, x:x + w] = old[half:]       # old bottom until covered
                if p < 0.5:                                        # old top folds down
                    k = math.cos(p * math.pi)
                    fh = max(1, int(half * k))
                    img[y0 + half - fh:y0 + half, x:x + w] = cv2.resize(old[:half], (w, fh)) // (1 + (1 - k))
                else:                                              # new bottom unfolds
                    k = -math.cos(p * math.pi)
                    fh = max(1, int(half * k))
                    img[y0 + half:y0 + half + fh, x:x + w] = cv2.resize(new[half:], (w, fh))
            x += w + gap
            if i == 1:
                for cy in (y0 + h // 3, y0 + 2 * h // 3):
                    rect(img, x, cy - s // 2, s, (200, 200, 200))
                x += 2 * s
        return img


# ---------------------------------------------------------------- Shatter
class Shatter(Base):
    TRANS = 2.0

    def frame(self, t, hhmm, ss):
        d = self.track(t, hhmm)
        img = np.zeros((self.H, self.W, 3), np.uint8)
        s = self.L.s
        hue = int(t * 20) % 180
        for i in range(4):
            col = self.c(t, i, tuple(cv2.cvtColor(np.uint8([[[(hue + i * 25) % 180, 200, 255]]]), cv2.COLOR_HSV2BGR)[0, 0].tolist()))
            p = self.phase(t, i)
            new = self.L.blocks(i, d[i])
            if p is None:
                for x, y in new:
                    rect(img, x, y, s, col, 1)
                continue
            rng = np.random.default_rng(int(self.changed_at[i] * 10) + i)
            old = self.L.blocks(i, self.prev[i])
            if p < 0.55:                                      # burst + gravity + floor bounce
                q = p / 0.55 * 1.6
                for x, y in old:
                    vx, vy = rng.uniform(-90, 90), rng.uniform(-140, -30)
                    px, py = x + vx * q, y + vy * q + 260 * q * q
                    floor = self.H - s // 2
                    if py > floor:
                        py = floor - (py - floor) * 0.35
                    rect(img, px, py, max(2, s // 2), col)
            else:                                             # pieces fly back as the new digit
                q = (p - 0.55) / 0.45
                e = 1 - (1 - q) ** 3
                for x, y in new:
                    sx, sy = rng.uniform(0, self.W), self.H - s
                    rect(img, sx + (x - sx) * e, sy + (y - sy) * e, s if q > 0.9 else max(2, s // 2), col, 1 if q > 0.9 else 0)
        for x, y in self.L.colon():
            rect(img, x, y, s, (200, 200, 200), 1)
        return img


# ---------------------------------------------------------------- Neon
class Neon(Base):
    TRANS = 0.8

    def frame(self, t, hhmm, ss):
        d = self.track(t, hhmm)
        s = self.L.s
        mask = np.zeros((self.H, self.W), np.float32)
        for i in range(4):
            p = self.phase(t, i)
            if p is None or p > 0.5:
                a = 1.0 if p is None else (p - 0.5) * 2
                cells = self.L.blocks(i, d[i])
            else:
                a, cells = 1 - p * 2, self.L.blocks(i, self.prev[i])
            if p is not None and 0.35 < p < 0.65 and int(t * 30) % 2:   # neon flicker on change
                a *= 0.3
            for x, y in cells:
                mask[y:y + s, x:x + s] = a
        for x, y in self.L.colon():
            mask[y:y + s, x:x + s] = 0.6 + 0.4 * math.sin(t * 6)
        # outline tube: dilate minus erode, then glow
        tube = cv2.morphologyEx(mask, cv2.MORPH_GRADIENT, np.ones((3, 3), np.uint8))
        xs = np.linspace(0, 1, self.W)[None, :]
        if self.color is None:
            pink = np.array([180, 60, 255], np.float32); cyan = np.array([255, 220, 60], np.float32)
            color = (pink * (1 - xs[..., None]) + cyan * xs[..., None])
        else:
            color = np.zeros((1, self.W, 3), np.float32)
            for i in range(4):                       # each digit's tube in its own (or the chosen) color
                x0 = self.L.dx[i] - self.L.s; x1 = x0 + 7 * self.L.s
                color[0, max(0, x0):x1] = self.c(t, i, None)
            color[0, self.L.colon_x - self.L.s:self.L.colon_x + 2 * self.L.s] = self.c(t, 1, None)
        glow = cv2.GaussianBlur(tube, (0, 0), s * 0.9)
        light = np.clip(tube * 1.0 + glow * 1.6, 0, 1)
        img = (light[..., None] * color).clip(0, 255)
        img = np.maximum(img, (tube[..., None] > 0.5) * 255 * 0.85)                 # white-hot tube core
        # seconds bar along the bottom
        bw = int(self.W * (ss + (t % 1)) / 60)
        cv2.rectangle(img, (0, self.H - 3), (bw, self.H - 1), (255, 220, 60), -1)
        return img.astype(np.uint8)


# ---------------------------------------------------------------- 7-Segment
SEGS = {"0": "abcdef", "1": "bc", "2": "abged", "3": "abgcd", "4": "fgbc", "5": "afgcd",
        "6": "afgedc", "7": "abc", "8": "abcdefg", "9": "abcdfg", " ": ""}


class SevenSeg(Base):
    """Classic LED/LCD alarm-clock digits: slanted segments, unlit ones faintly
    visible, segments fade on and off when a digit changes, soft glow."""
    TRANS = 0.35
    SKEW = 0.12            # slant, as a fraction of the digit height

    def __init__(self, W, H, color=None):
        super().__init__(W, H, color)
        dh = int(min(H * 0.78, W * 0.92 / (4 * 0.5 + 3 * 0.16 + 0.3 + self.SKEW)))
        self.dh, self.dw = dh, int(dh * 0.5)
        self.th = max(2, int(dh * 0.13))                      # segment thickness
        gap, colon = int(dh * 0.16), int(dh * 0.3)
        total = 4 * self.dw + 3 * gap + colon + int(dh * self.SKEW)
        x = (W - total) // 2 + int(dh * self.SKEW)
        self.y0 = (H - dh) // 2
        self.xs = []
        for i in range(4):
            self.xs.append(x)
            x += self.dw + gap
            if i == 1:
                self.cx = x - gap // 2 + colon // 2 - gap // 4
                x += colon
        self.polys = {}

    def seg_polys(self, x0):
        """Polygon (x, y points) of each segment of a digit whose top-left is x0, y0."""
        w, h, t, y0 = self.dw, self.dh, self.th, self.y0
        m = t // 2
        def hseg(y):   # horizontal: pointed ends
            return [(m, y), (t, y - m), (w - t, y - m), (w - m, y), (w - t, y + m), (t, y + m)]
        def vseg(x, ya, yb):
            return [(x, ya + m), (x + m, ya + t), (x + m, yb - t), (x, yb - m), (x - m, yb - t), (x - m, ya + t)]
        pts = {"a": hseg(m), "g": hseg(h // 2), "d": hseg(h - m - 1),
               "f": vseg(m, m, h // 2), "b": vseg(w - m - 1, m, h // 2),
               "e": vseg(m, h // 2, h - m - 1), "c": vseg(w - m - 1, h // 2, h - m - 1)}
        out = {}
        for k, poly in pts.items():
            out[k] = np.array([(x0 + px + self.SKEW * (h - py), y0 + py) for px, py in poly], np.int32)
        return out

    def frame(self, t, hhmm, ss):
        d = self.track(t, hhmm)
        lit = np.zeros((self.H, self.W), np.float32)
        ghost = np.zeros((self.H, self.W), np.uint8)
        col = np.zeros((self.H, self.W, 3), np.float32)
        for i in range(4):
            if i not in self.polys:
                self.polys[i] = self.seg_polys(self.xs[i])
            c = np.array(self.c(t, i, (40, 50, 255)), np.float32)       # default: classic red LED
            p = self.phase(t, i)
            for k, poly in self.polys[i].items():
                on_new, on_old = k in SEGS[d[i]], k in SEGS[self.prev[i]]
                a = 1.0 if on_new else 0.0
                if p is not None and on_new != on_old:
                    a = p if on_new else 1 - p
                cv2.fillPoly(ghost, [poly], 1)
                if a > 0:
                    m = np.zeros((self.H, self.W), np.uint8)
                    cv2.fillPoly(m, [poly], 255)
                    lit = np.where(m > 0, a, lit)
                cv2.fillPoly(col, [poly], c.tolist())
        # colon: two slanted dots, blinking each second
        r = max(2, self.th // 2 + 1)
        cc = np.array(self.c(t, 1, (40, 50, 255)), np.float32)
        for fy in (0.32, 0.68):
            y = int(self.y0 + self.dh * fy)
            x = int(self.cx + self.SKEW * (self.dh - self.dh * fy))
            cv2.circle(ghost, (x, y), r, 1, -1)
            cv2.circle(col, (x, y), r, cc.tolist(), -1)
            if (t % 1.0) < 0.5:
                cv2.circle(lit, (x, y), r, 1.0, -1)
        glow = cv2.GaussianBlur(lit, (0, 0), self.th * 0.7)
        img = col * (lit[..., None] + ghost[..., None] * 0.07 * (1 - lit[..., None]))
        img += col * glow[..., None] * 0.45
        return np.clip(img, 0, 255).astype(np.uint8)


STYLES = {"Code Rain": CodeRain, "Fire": Fire, "Block Drop": BlockDrop, "Flip": Flip,
          "Shatter": Shatter, "Neon": Neon, "7-Segment": SevenSeg}
