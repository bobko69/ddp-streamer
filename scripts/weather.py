"""Weather
Live weather for any town: an animated sky (sun, moon, clouds, rain, snow,
storm, fog), the temperature colored from icy blue to hot red, and every
few seconds the next 12 hours as a little chart. Free data from
Open-Meteo, no account needed.
"""

params = {
    "place": textbox("Chicago, IL", label="town (or lat, lon)"),
    "units": dropdown("F", "C"),
    "forecast": checkbox(True, label="show next hours too"),
}


def setup(c):
    c.drops = [(rand(), rand()) for _ in range(40)]


def temp_color(v):
    # icy blue at freezing or below, white around 60F / 15C, red when hot
    cold, hot = (32, 95) if units == "F" else (0, 35)
    k = clamp((v - cold) / (hot - cold))
    return mix("#40a0ff", "#ffffff", k * 2) if k < 0.5 else mix("#ffffff", "#ff4020", k * 2 - 1)


def cloud(c, x, y, s, color):
    for dx, dy, r in ((-0.35, 0.1, 0.32), (0.0, -0.12, 0.42), (0.35, 0.08, 0.3), (0.0, 0.15, 0.3)):
        c.circle(x + dx * s, y + dy * s, r * s, color)
    c.rect(x - 0.6 * s, y + 0.1 * s, 1.2 * s, 0.35 * s, color)


def sky(c, kind, day, x, y, s, t):
    """Animated weather icon of size s centred on x, y."""
    if kind in ("clear", "partly"):
        if day:
            sx, sy = (x - 0.15 * s, y - 0.12 * s) if kind == "partly" else (x, y)
            for i in range(10):
                a = t * 0.4 + i * pi / 5
                r1, r2 = 0.42 * s, (0.55 + 0.05 * sin(t * 3 + i)) * s
                c.line(sx + cos(a) * r1, sy + sin(a) * r1, sx + cos(a) * r2, sy + sin(a) * r2, "#ffc000", width=max(1, s / 20))
            c.circle(sx, sy, 0.32 * s, "#ffd020")
        else:
            for i in range(6):
                c.dot(x + (fract(i * 0.37) - 0.5) * s * 1.2, y + (fract(i * 0.61) - 0.5) * s, white,
                      alpha=0.3 + 0.3 * sin(t * 2 + i))
            c.circle(x - 0.05 * s, y, 0.3 * s, "#f0f0d0")
            c.circle(x + 0.1 * s, y - 0.08 * s, 0.26 * s, black)
        if kind == "partly":
            cloud(c, x + 0.1 * s + sin(t * 0.5) * 0.04 * s, y + 0.15 * s, 0.75 * s, "#d0d8e0")
        return
    if kind == "fog":
        for i in range(4):
            yy = y + (i - 1.5) * 0.22 * s
            pts = [(x + (j / 8 - 0.5) * s * 1.2, yy + sin(j + t * 2 + i) * 0.03 * s) for j in range(9)]
            for a, b in zip(pts, pts[1:]):
                c.line(a[0], a[1], b[0], b[1], (0.6, 0.65, 0.7), width=max(1, s / 14))
        return
    dark = kind == "storm"
    for i, (px, py) in enumerate(c.drops[:14 if kind != "snow" else 10]):
        fall = fract(py + t * (0.9 if kind != "snow" else 0.25))
        dx = x + (px - 0.5) * s * 0.9 + (sin(t * 2 + i) * 0.05 * s if kind == "snow" else 0)
        dy = y + 0.2 * s + fall * 0.6 * s
        if kind == "snow":
            c.circle(dx, dy, max(0.6, s / 28), white)
        else:
            c.line(dx, dy, dx - 0.04 * s, dy + 0.12 * s, "#4090ff", width=max(1, s / 30))
    if dark and fract(t * 0.4) < 0.06:                       # lightning
        c.polygon([(x, y), (x - 0.12 * s, y + 0.3 * s), (x, y + 0.28 * s), (x - 0.08 * s, y + 0.6 * s),
                   (x + 0.14 * s, y + 0.22 * s), (x + 0.02 * s, y + 0.24 * s), (x + 0.1 * s, y)], "#ffff80")
    cloud(c, x + sin(t * 0.4) * 0.05 * s, y - 0.05 * s, 0.85 * s, "#606878" if dark else "#c0c8d0")


def frame(c, t):
    c.clear()
    w = weather(place, units)
    if w is None:
        err = data_error("weather")
        c.text(err or "Loading weather...", color=warm_white if not err else "#ff6060",
               size=c.fit_size(err or "Loading weather...", c.w * 0.95, c.h * 0.18))
        return
    page = int(t / 8) % 2 if forecast and w["hours"] else 0
    if page == 0:
        s = min(c.h * 0.8, c.w * 0.42)
        sky(c, w["icon"], w["is_day"], c.w * 0.25, c.h * 0.5, s, t)
        temp = f"{w['temp']:.0f}{w['unit'][0]}"            # "53°"
        x0 = c.w * 0.5
        size = c.fit_size(temp, c.w * 0.48, c.h * 0.5, "Archivo Black")
        c.text(temp, x0 + c.w * 0.24, c.h * 0.42, temp_color(w["temp"]), size, "Archivo Black")
        line = f"{w['description']}  H {w['high']:.0f} L {w['low']:.0f}"
        c.text(line, x0 + c.w * 0.24, c.h * 0.82, (0.7, 0.75, 0.8), c.fit_size(line, c.w * 0.48, c.h * 0.15))
        c.text(w["place"], 2, 1, (0.45, 0.5, 0.6), c.h * 0.13, align="left", valign="top")
    else:
        hours = w["hours"]
        temps = [h["temp"] for h in hours]
        lo, hi = min(temps), max(temps)
        top, bottom = c.h * 0.22, c.h * 0.72
        n = len(hours)
        pts = [(c.w * (i + 0.5) / n, bottom - (v - lo) / max(1, hi - lo) * (bottom - top)) for i, v in enumerate(temps)]
        for i, ((x, y), h) in enumerate(zip(pts, hours)):
            c.line(x, y, x, c.h * 0.8, temp_color(h["temp"]), width=max(1, c.w / n * 0.5), alpha=0.35)
            if i % 3 == 0:
                c.text(f"{h['temp']:.0f}", x, y - c.h * 0.1, temp_color(h["temp"]), c.h * 0.14)
                c.text(h["time"][:2], x, c.h * 0.92, (0.5, 0.55, 0.65), c.h * 0.12)
        for a, b in zip(pts, pts[1:]):
            c.line(a[0], a[1], b[0], b[1], white, width=max(1, c.h / 40))
    c.glow(0.3)
