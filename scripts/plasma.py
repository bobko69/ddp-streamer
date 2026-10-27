"""Plasma
The classic demo-scene effect: a few sine waves added together and turned
into color with a palette.
"""

params = {
    "colors": dropdown(*PALETTES, default="party"),
    "speed": slider(1.0, 0.1, 3.0),
    "size": slider(1.0, 0.3, 3.0),
}


def pixel(x, y, t):
    t = t * speed
    x = x * aspect / size          # aspect = width / height, keeps blobs round
    y = y / size
    v = sin(x * 6 + t)
    v += sin((y * 5 + t) * 0.8)
    v += sin((x * 4 + y * 4 + t) * 0.7)
    cx, cy = x + 0.5 * sin(t / 3), y + 0.5 * cos(t / 2)
    v += sin(sqrt(cx * cx + cy * cy + 1) * 8 - t)
    return palette(colors, v / 8 + t * 0.05)     # v is about -4..4
