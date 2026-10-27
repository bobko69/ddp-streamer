"""Tunnel
Flying down an endless tunnel. Every pixel works out its distance and
angle from the middle and uses them to look up a moving checker pattern.
"""

params = {
    "speed": slider(1.0, 0.2, 3.0),
    "twist": slider(0.3, -1.0, 1.0),
    "colors": dropdown("neon", "rainbow", "party", "ice", "fire"),
}


def pixel(x, y, t):
    dx = (x - 0.5 - 0.15 * sin(t * 0.7)) * aspect      # the middle wanders a little
    dy = y - 0.5 - 0.1 * cos(t * 0.5)
    dist = sqrt(dx * dx + dy * dy) + 0.001
    angle = atan2(dy, dx) / (2 * pi)                   # -0.5 .. 0.5 round the circle
    depth = 0.25 / dist + t * speed                    # far away = big depth
    around = angle * 8 + depth * twist
    checker = (floor(depth * 2) + floor(around * 2)) % 2
    shade = clamp(dist * 2.5)                          # darker in the distance
    col = palette(colors, depth * 0.1)
    k = shade * (0.25 + 0.75 * checker)
    return (col[0] * k, col[1] * k, col[2] * k)
