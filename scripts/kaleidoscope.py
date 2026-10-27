"""Kaleidoscope
Colored noise mirrored round the middle like a kaleidoscope. Draw anything,
then c.kaleido() repeats one wedge of it round the circle.
"""

params = {
    "mirrors": slider(6, 2, 12, step=1),
    "colors": dropdown("party", "rainbow", "neon", "candy", "sunset", "ocean"),
    "speed": slider(0.5, 0.1, 2.0),
}


def frame(c, t):
    t = t * speed
    # noise is the slow part: work it out on fewer pixels (every 2nd on a
    # 256-wide wall, every 4th on 512), then stretch it up - it's smooth anyway
    s = max(1, c.w // 128)
    x, y = c.x[::s, ::s], c.y[::s, ::s]
    n = cv2.resize(fbm(x * 3 * aspect, y * 3, t * 0.4, octaves=3), (c.w, c.h))
    col = palette(colors, n * 1.6 + t * 0.1)
    k = smoothstep(0.3, 0.6, n) * 1.3
    c.set((col[0] * k, col[1] * k, col[2] * k))
    c.kaleido(mirrors, angle=t * 15)
    c.glow(0.3)
