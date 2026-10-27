"""Fire
Flames rising from the bottom edge. Layered noise (fbm) scrolls upward and
gets weaker with height, so it breaks up into licking flame tongues.
"""

params = {
    "colors": dropdown("fire", "lava", "ice", "forest", "neon"),
    "height": slider(1.0, 0.4, 1.6),
    "speed": slider(1.0, 0.3, 2.5),
    "wind": slider(0.0, -1.0, 1.0),
}


def frame(c, t):
    # noise is the slow part: work it out on fewer pixels (every 2nd on a
    # 256-wide wall, every 4th on 512), then stretch it up - it's smooth anyway
    s = max(1, c.w // 128)
    x, y = c.x[::s, ::s] * aspect, c.y[::s, ::s]
    up = 1 - y                                         # 0 at the bottom, 1 at the top
    t = t * speed
    lean = wind * up * up * 0.5                        # wind bends the tops of the flames
    n = fbm(x * 3 - lean, y * 2.5 + t * 1.6, t * 0.3, octaves=3)
    heat = n * 1.7 - up * (1.25 / height) + 0.1        # strong at the bottom, gone near the top
    heat = cv2.resize(np.clip(heat, 0, 1), (c.w, c.h))
    c.set(palette(colors, heat * 0.9))                 # 0.9: stop before the palette wraps to black
    c.glow(0.5)
