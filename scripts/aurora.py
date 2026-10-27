"""Aurora
Northern lights: curtains of green and violet light that ripple slowly
over a starry sky. Uses noise() for the soft, natural movement.
"""

params = {
    "speed": slider(0.4, 0.05, 1.5),
    "brightness": slider(1.0, 0.3, 1.5),
    "stars": checkbox(True),
}


def setup(c):
    # a fixed set of stars: (x, y) in pixels and a twinkle phase each
    n = int(c.w * c.h / 100)
    c.sx, c.sy, c.sp = rand(0, c.w, n=n), rand(0, c.h, n=n), rand(0, 6.28, n=n)
    c.row_x = c.x[0] * aspect           # x of each column, 0..aspect


def frame(c, t):
    t = t * speed
    x = c.row_x
    # Things that only depend on x are worked out once per column (fast),
    # then spread down the whole height.
    edge = 0.35 + 0.35 * noise(x * 1.2, t * 0.3)          # lower edge of the curtain
    rays = 0.3 + 0.9 * noise(x * 9, t * 1.1, 4.0) ** 2    # bright vertical rays
    tone = noise(x * 0.7 + 5, t * 0.25)                   # green <-> violet
    y = c.y
    above = edge[None, :] - y                             # > 0 above the lower edge
    curtain = np.where(above > 0, np.exp(-above * 3.0), np.exp(above * 25.0))
    k = curtain * rays[None, :] * brightness
    r = k * (0.1 + 0.8 * tone[None, :] * (1 - curtain * 0.5))
    g = k * (1.0 - 0.7 * tone[None, :])
    b = k * (0.3 + 0.6 * tone[None, :]) + 0.03 * (1 - y)   # night-blue sky
    c.set((r, g, b))
    if stars:
        twinkle = 0.2 + 0.2 * np.sin(t * 8 + c.sp)
        c.points(c.sx, c.sy, np.stack([twinkle] * 3, 1))
    c.glow(0.4)
