"""Raindrops
Drops land on a pond and their rings spread, bounce off the edges and run
through each other. A tiny water simulation: each point is pulled toward
the average of its neighbours.
"""

params = {
    "drops": slider(2.0, 0.2, 8.0, label="drops per second"),
    "colors": dropdown("ocean", "ice", "neon", "party", "sunset"),
    "calm": slider(0.985, 0.95, 0.998, label="how long rings last"),
}


def setup(c):
    c.now = np.zeros((c.h, c.w), np.float32)
    c.old = np.zeros((c.h, c.w), np.float32)
    c.until_drop = 0.0
    c.carry = 0.0


def frame(c, t):
    c.until_drop -= c.dt
    if c.until_drop <= 0:
        c.until_drop = rand(0.2, 1.8) / drops
        x, y = randint(2, c.w - 3), randint(2, c.h - 3)
        c.now[y - 1:y + 2, x - 1:x + 2] -= 14.0
    c.carry += c.dt * 50                       # 50 simulation steps per second
    for _ in range(int(c.carry)):
        n = c.now
        avg = (np.roll(n, 1, 0) + np.roll(n, -1, 0) + np.roll(n, 1, 1) + np.roll(n, -1, 1)) / 2
        c.old, c.now = c.now, (avg - c.old) * calm
    c.carry -= int(c.carry)
    # light the water: the slope of the surface catches the light
    slope = np.roll(c.now, -1, 1) - c.now + np.roll(c.now, -1, 0) - c.now
    col = palette(colors, 0.45 + clamp(c.now * 0.05, -0.2, 0.2))
    k = clamp(0.3 + slope * 1.5, 0, 2.0)
    c.set((col[0] * k, col[1] * k, col[2] * k))
