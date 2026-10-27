"""Swirl
A spiral of dots that winds up and unwinds. Based on an older "Swirl"
(2015), with trails, a rainbow and glow added.
"""

params = {
    "dots": slider(120, 20, 400, step=1),
    "dot_size": slider(2.0, 0.5, 5.0),
    "speed": slider(1.0, 0.2, 3.0),
    "trails": slider(0.7, 0.0, 0.95),
}


def frame(c, t):
    c.fade(trails)
    cx, cy = c.w / 2, c.h / 2
    turn = (2 + 30 * pingpong(t * speed / 12)) * pi / 180     # winds up, then back
    outer = min(c.w, c.h * aspect) * 0.45
    for i in range(1, dots + 1):
        k = i / dots
        a = i * turn + t * speed * 0.3
        x = cx + cos(a) * k * outer
        y = cy + sin(a) * k * outer * min(1.0, c.h / c.w * 2)   # a bit flatter on wide walls
        c.circle(x, y, dot_size * (0.4 + k), hsv(k * 0.8 + t * 0.1), blend="max")
    c.glow(0.5)
