"""Countdown
Days, hours, minutes and seconds until a date - Christmas, New Year, a
show, a wedding. Snow drifts behind it, and when the moment comes it
switches to your message with fireworks of color.
"""

import datetime

params = {
    "event": textbox("CHRISTMAS"),
    "date": textbox("2026-12-25", label="date (YYYY-MM-DD)"),
    "at": textbox("00:00", label="time (HH:MM)"),
    "message": textbox("MERRY CHRISTMAS!", label="when it's here"),
    "tint": color_picker("#30ff80"),
}


def setup(c):
    c.sx, c.sy = rand(0, c.w, n=80), rand(0, c.h, n=80)
    try:
        c.target = datetime.datetime.strptime(f"{date.strip()} {at.strip()}", "%Y-%m-%d %H:%M")
        c.bad = ""
    except ValueError:
        c.target, c.bad = None, "Date should look like 2026-12-25"


def frame(c, t):
    c.clear()
    c.sy = (c.sy + c.dt * c.h * 0.1) % c.h                       # gentle snow behind
    c.points(c.sx + sin(t + c.sy * 0.1) * 2, c.sy, (0.25, 0.25, 0.3))
    if c.target is None:
        c.text(c.bad, color="#ff6060", size=c.fit_size(c.bad, c.w * 0.95, c.h * 0.2))
        return
    left = (c.target - datetime.datetime.now()).total_seconds()
    if left <= 0:                                                  # it's here!
        c.text(message, color=hsv(t * 0.3), size=c.fit_size(message, c.w * 0.95, c.h * 0.6), font="Archivo Black")
        for i in range(6):
            a = t * 2 + i
            c.circle(c.w * fract(i * 0.37 + t * 0.1), c.h * fract(i * 0.61 + t * 0.07), c.h * 0.08 * wave(t + i),
                     hsv(i / 6 + t * 0.2), blend="add", alpha=0.6)
        c.glow(0.6)
        return
    d, rest = divmod(int(left), 86400)
    h, rest = divmod(rest, 3600)
    m, s = divmod(rest, 60)
    big = f"{d}d {h:02}:{m:02}:{s:02}" if d else f"{h:02}:{m:02}:{s:02}"
    c.text(event, c.w / 2, c.h * 0.22, (0.75, 0.75, 0.8), c.fit_size(event, c.w * 0.9, c.h * 0.24), "Archivo Black")
    size = c.fit_size(big, c.w * 0.95, c.h * 0.42, "JetBrains Mono")
    pulse = 0.8 + 0.2 * (1 - fract(left))                         # brightens on each second
    c.text(big, c.w / 2, c.h * 0.66, tuple(k * pulse for k in tint), size, "JetBrains Mono")
    c.glow(0.4)
