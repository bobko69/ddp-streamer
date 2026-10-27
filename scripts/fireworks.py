"""Fireworks
Rockets shoot up and burst into hundreds of sparks that fall with gravity
and fade. All sparks are drawn at once with c.points(), so it stays fast.
"""

params = {
    "rockets": slider(1.2, 0.2, 4.0, label="rockets per second"),
    "sparks": slider(160, 40, 400, step=1),
    "gravity": slider(1.0, 0.2, 2.0),
}


def setup(c):
    # every spark is one entry in these arrays
    c.px = np.zeros(0, np.float32)
    c.py = np.zeros(0, np.float32)
    c.vx = np.zeros(0, np.float32)
    c.vy = np.zeros(0, np.float32)
    c.life = np.zeros(0, np.float32)
    c.col = np.zeros((0, 3), np.float32)
    c.rockets = []                         # [x, y, vy, burst height, hue]
    c.next_rocket = 0.0


def burst(c, x, y, hue):
    n = sparks
    a = rand(0, 2 * pi, n=n)
    s = rand(0.2, 1.0, n=n) ** 0.5 * c.h * 0.9           # sqrt: even round shell
    col = np.array(hsv(hue + rand(-0.04, 0.04), rand(0.5, 1), 1), np.float32)
    c.px = np.concatenate([c.px, np.full(n, x, np.float32)])
    c.py = np.concatenate([c.py, np.full(n, y, np.float32)])
    c.vx = np.concatenate([c.vx, np.cos(a) * s])
    c.vy = np.concatenate([c.vy, np.sin(a) * s])
    c.life = np.concatenate([c.life, rand(0.8, 1.6, n=n)])
    c.col = np.concatenate([c.col, np.tile(col, (n, 1))])


def frame(c, t):
    dt = c.dt
    c.fade(0.8)
    # launch new rockets
    c.next_rocket -= dt
    if c.next_rocket <= 0:
        c.next_rocket = rand(0.3, 1.7) / rockets
        c.rockets.append([rand(c.w * 0.15, c.w * 0.85), c.h, -c.h * rand(1.1, 1.5), c.h * rand(0.15, 0.45), rand()])
    for r in c.rockets[:]:
        r[1] += r[2] * dt
        c.dot(r[0], r[1], (1, 0.8, 0.5))
        if r[1] <= r[3]:
            burst(c, r[0], r[1], r[4])
            c.rockets.remove(r)
    # move the sparks
    c.vy += c.h * 0.6 * gravity * dt
    c.vx *= 0.985
    c.vy *= 0.985
    c.px += c.vx * dt
    c.py += c.vy * dt
    c.life -= dt
    keep = c.life > 0
    c.px, c.py, c.vx, c.vy, c.life, c.col = (a[keep] for a in (c.px, c.py, c.vx, c.vy, c.life, c.col))
    fade = np.clip(c.life, 0, 1)[:, None]
    sparkle = 0.7 + 0.3 * (np.random.random(len(c.life)) > 0.3)[:, None]
    c.points(c.px, c.py, c.col * fade * sparkle)
    c.glow(0.8)
