"""Game of Life
Conway's cellular automaton. Cells live or die by counting their
neighbours; new cells are white and cool through the palette as they age.
Restarts by itself when it settles down.
"""

params = {
    "cell": slider(2, 1, 8, step=1, label="cell size (px)"),
    "steps": slider(12, 2, 40, step=1, label="steps per second"),
    "colors": dropdown("rainbow", "neon", "ocean", "fire", "candy"),
}


def seed(c):
    c.alive = np.random.random((c.rows, c.cols)) < 0.3
    c.age = np.zeros((c.rows, c.cols), np.float32)
    c.history = []


def setup(c):
    c.cols, c.rows = max(4, c.w // cell), max(4, c.h // cell)
    c.carry = 0.0
    seed(c)


def frame(c, t):
    c.carry += c.dt * steps
    while c.carry >= 1:
        c.carry -= 1
        a = c.alive
        n = sum(np.roll(np.roll(a, dy, 0), dx, 1) for dy in (-1, 0, 1) for dx in (-1, 0, 1)
                if dy or dx).astype(np.int8)
        c.alive = (n == 3) | (a & (n == 2))
        c.age = np.where(c.alive, c.age + 1, 0)
        # stuck or repeating? start a new world
        key = c.alive.tobytes()
        if key in c.history or c.alive.sum() < 4:
            seed(c)
        c.history = (c.history + [key])[-6:]
    col = palette(colors, c.age * 0.04)
    young = np.clip(1 - c.age * 0.5, 0, 1)                  # just born: white
    k = c.alive.astype(np.float32)
    rgb_ = np.stack([(ch * (1 - young) + young) * k for ch in col], -1)
    big = np.kron(rgb_, np.ones((cell, cell, 1), np.float32))   # cells -> pixels
    c.clear()
    c.img[:big.shape[0], :big.shape[1]] = big[:c.h, :c.w]
