"""YouTube subscribers
A channel's subscriber count next to the YouTube logo, with a burst of
confetti whenever it goes up. No account needed. YouTube rounds public
counts to three digits (30.5M, 12.3K), so a big channel's number moves in
steps; the default, MrBeast, steps up every day or two.
"""

params = {
    "channel": textbox("@MrBeast", label="channel (@handle or link)"),
    "label": textbox("SUBSCRIBERS"),
}


def setup(c):
    c.shown = None              # number on screen, rolls toward the real one
    c.last = None
    c.bits = []                 # confetti: [x, y, vx, vy, color, life]


def confetti(c, n):
    for _ in range(n):
        c.bits.append([c.w * 0.25, c.h * 0.45, rand(-1, 1) * c.h, rand(-1.6, -0.4) * c.h,
                       hsv(rand()), rand(1, 2)])


def logo(c, x, y, s):
    c.rect(x - s * 0.7, y - s * 0.48, s * 1.4, s * 0.96, "#ff0000")
    for cx, cy in ((-0.55, -0.33), (0.55, -0.33), (-0.55, 0.33), (0.55, 0.33)):   # round the corners
        c.circle(x + cx * s, y + cy * s, s * 0.15, "#ff0000")
    c.polygon([(x - s * 0.2, y - s * 0.26), (x - s * 0.2, y + s * 0.26), (x + s * 0.27, y)], white)


def frame(c, t):
    c.fade(0.0)
    yt = youtube(channel)
    logo(c, c.w * 0.25 if yt else c.w / 2, c.h * 0.45, min(c.h * 0.5, c.w * 0.3))
    if yt is None:
        err = data_error("youtube")
        msg = err or "Loading..."
        c.text(msg, c.w / 2, c.h * 0.88, "#ff6060" if err else (0.6, 0.6, 0.7), c.fit_size(msg, c.w * 0.95, c.h * 0.14))
        return
    n = yt["subscribers"]
    if c.last is not None and n > c.last:
        confetti(c, 150)                               # celebrate!
    c.last = n
    if c.shown is None:
        c.shown = n
    c.shown += (n - c.shown) * min(1, c.dt * 2)        # roll toward the real number
    text = f"{round(c.shown):,}" if yt["exact"] else yt["text"]       # small channels: every subscriber
    x0, x1 = c.w * 0.48, c.w * 0.98
    size = c.fit_size(text, x1 - x0, c.h * 0.55, "Archivo Black")
    c.text(text, (x0 + x1) / 2, c.h * 0.42, white, size, "Archivo Black")
    c.text(label, (x0 + x1) / 2, c.h * 0.8, (0.7, 0.7, 0.75), c.fit_size(label, x1 - x0, c.h * 0.16))
    for b in c.bits:                                     # confetti falls and fades
        b[3] += c.h * 1.5 * c.dt
        b[0] += b[2] * c.dt
        b[1] += b[3] * c.dt
        b[5] -= c.dt
        c.rect(b[0], b[1], 2, 2, b[4], alpha=clamp(b[5]))
    c.bits = [b for b in c.bits if b[5] > 0 and b[1] < c.h]
