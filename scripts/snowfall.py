"""Snowfall
Snowflakes drift down on a gentle, changing wind and pile up along the
bottom. Near flakes are bigger and faster than far ones.
"""

params = {
    "flakes": slider(250, 50, 1000, step=1),
    "wind": slider(0.3, 0.0, 1.0),
    "pile_up": checkbox(True),
    "sky": color_picker("#000818"),
}


def setup(c):
    n = flakes
    c.fx, c.fy = rand(0, c.w, n=n), rand(0, c.h, n=n)
    c.depth = rand(0.3, 1.0, n=n)                    # 1 = close to us
    c.ground = np.zeros(c.w, np.float32)             # snow height per column


def frame(c, t):
    c.clear(sky)
    gust = (noise(t * 0.2) - 0.5) * 2 * wind
    c.fx += (gust * c.h * 0.6 + np.sin(t * 2 + c.fy * 0.1) * c.h * 0.05) * c.depth * c.dt
    c.fy += c.h * 0.25 * c.depth * c.dt
    c.fx %= c.w
    col = np.clip(c.fx.astype(np.int32), 0, c.w - 1)
    landed = c.fy >= c.h - c.ground[col]
    if pile_up and landed.any():
        np.add.at(c.ground, col[landed], 0.25)
        c.ground = np.minimum(c.ground, c.h * 0.35)
        c.ground[:] = np.convolve(c.ground, [0.25, 0.5, 0.25], "same")    # settle into drifts
    c.fy[landed] = rand(-5, 0, n=int(landed.sum()))
    c.fx[landed] = rand(0, c.w, n=int(landed.sum()))
    shade = (0.35 + 0.65 * c.depth)[:, None] * np.array([0.9, 0.95, 1.0], np.float32)
    c.points(c.fx, c.fy, shade, blend="max")
    if pile_up:
        top = c.h - c.ground
        c.img[c.py > top[None, :]] = (0.75, 0.8, 0.9)
