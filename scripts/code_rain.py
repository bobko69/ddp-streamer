"""Code rain
Green glyphs falling down the screen like in The Matrix. Each column has a
bright head and a fading tail; the characters flicker as they fall.
"""

params = {
    "speed": slider(1.0, 0.3, 3.0),
    "tint": color_picker("#20ff60"),
    "cell": slider(8, 5, 16, step=1),
}

GLYPHS = "01ABCDEF<>/\\|=+*:;%$#@"


def setup(c):
    c.cols = max(1, c.w // cell)
    c.rows = max(1, c.h // cell + 1)
    c.head = rand(-c.rows, c.rows, n=c.cols)          # row of each falling head
    c.rate = rand(6, 14, n=c.cols)                    # rows per second
    c.chars = [[GLYPHS[randint(len(GLYPHS))] for _ in range(c.rows)] for _ in range(c.cols)]


def frame(c, t):
    c.clear()
    c.head += c.rate * c.dt * speed
    for i in range(c.cols):
        if c.head[i] - 18 > c.rows:                   # tail has left: start again on top
            c.head[i] = rand(-8, 0)
            c.rate[i] = rand(6, 14)
        for r in range(c.rows):
            age = c.head[i] - r                       # 0 = the head, bigger = further up the tail
            if age < 0 or age > 18:
                continue
            if rand() < 0.03:                         # flicker: swap a character now and then
                c.chars[i][r] = GLYPHS[randint(len(GLYPHS))]
            fade = 1 - age / 18
            col = white if age < 1 else tuple(k * fade * fade for k in tint)
            c.text(c.chars[i][r], i * cell + cell / 2, r * cell + cell / 2, col, size=cell * 1.1,
                   font="JetBrains Mono")
    c.glow(0.5)
