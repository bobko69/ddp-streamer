"""Starfield
Flying through space. Stars have a 3D position and are drawn as short
streaks that stretch as they rush past.
"""

params = {
    "speed": slider(1.0, 0.1, 4.0),
    "stars": slider(300, 50, 1500, step=1),
    "tint": color_picker("#c0d8ff"),
}


def new_stars(c, n, far=True):
    x = rand(-1, 1, n=n)
    y = rand(-1, 1, n=n)
    z = rand(0.2, 1, n=n) if not far else np.ones(n, np.float32)
    return x, y, z


def setup(c):
    c.x3, c.y3, c.z3 = new_stars(c, stars, far=False)


def frame(c, t):
    c.clear()
    cx, cy, scale = c.w / 2, c.h / 2, c.w / 2
    old_z = c.z3.copy()
    c.z3 -= c.dt * 0.4 * speed
    gone = c.z3 < 0.02                           # passed us: start again far away
    if gone.any():
        c.x3[gone], c.y3[gone], c.z3[gone] = new_stars(c, int(gone.sum()))
        old_z[gone] = c.z3[gone]
    # 3D -> screen: things further away are closer to the middle
    sx, sy = cx + c.x3 / c.z3 * scale, cy + c.y3 / c.z3 * scale
    ox, oy = cx + c.x3 / old_z * scale, cy + c.y3 / old_z * scale
    bright = np.clip(1.2 - c.z3, 0, 1)
    for x1, y1, x2, y2, b in zip(ox, oy, sx, sy, bright):
        if -5 < x2 < c.w + 5 and -5 < y2 < c.h + 5:
            c.line(x1, y1, x2, y2, tuple(k * b for k in tint), width=1, blend="add")
