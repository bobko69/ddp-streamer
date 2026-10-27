"""Hyperspace
A feedback effect: each frame the picture is zoomed in and turned a little,
then something new is drawn in the middle. The old frames stream outward
into a spiral tunnel. Try changing zoom and turn.
"""

params = {
    "zoom": slider(1.08, 1.01, 1.2),
    "turn": slider(2.0, -10.0, 10.0, label="turn (degrees)"),
    "colors": dropdown("rainbow", "neon", "fire", "ice", "party"),
    "shape": dropdown("ring", "star", "dots"),
}


def setup(c):
    c.last_beat = -1


def frame(c, t):
    c.zoom(zoom, turn)
    c.fade(0.95)
    beat = int(t * 5)                    # a new shape 5 times a second
    if beat == c.last_beat:
        c.glow(0.3)
        return
    c.last_beat = beat
    cx = c.w / 2 + sin(t * 0.9) * c.w * 0.05
    cy = c.h / 2 + cos(t * 0.7) * c.h * 0.08
    col = palette(colors, t * 0.15)
    r = c.h * 0.08
    if shape == "ring":
        c.circle(cx, cy, r, col, width=max(1, r / 4))
    elif shape == "star":
        pts = []
        for i in range(10):
            a = t + i * pi / 5
            k = r * (1.3 if i % 2 == 0 else 0.55)
            pts.append((cx + cos(a) * k, cy + sin(a) * k))
        c.polygon(pts, col)
    else:
        for i in range(5):
            a = t * 2 + i * 2 * pi / 5
            c.circle(cx + cos(a) * r, cy + sin(a) * r, r * 0.3, palette(colors, t * 0.15 + i * 0.2))
    c.glow(0.3)
