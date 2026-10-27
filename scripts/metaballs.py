"""Lava lamp
Soft blobs that drift around and melt into each other when they meet
("metaballs"), with a glowing edge.
"""

params = {
    "blobs": slider(6, 2, 12, step=1),
    "colors": dropdown("lava", "ocean", "party", "neon", "sunset", "candy"),
    "speed": slider(0.6, 0.1, 2.0),
}


def pixel(x, y, t):
    t = t * speed
    x = x * aspect
    field = 0
    for i in range(blobs):
        # each blob wanders on its own slow path
        bx = (0.5 + 0.42 * sin(t * (0.3 + i * 0.07) + i * 1.7)) * aspect
        by = 0.5 + 0.4 * cos(t * (0.4 + i * 0.05) + i * 2.3)
        field = field + 0.012 / ((x - bx) ** 2 + (y - by) ** 2 + 0.0005)
    inside = smoothstep(0.9, 1.3, field)
    edge = smoothstep(0.6, 0.9, field) - inside * 0.7
    col = palette(colors, 0.3 + clamp(field * 0.06, 0, 0.25))
    k = inside * 0.9 + edge
    return (col[0] * k, col[1] * k, col[2] * k)
