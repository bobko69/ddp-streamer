"""CS2 kill map
Where the fighting is on your CS2 server, seen from above: every kill
flashes on the map (a line from the killer to the victim, in the killer's
team color), and the spots of all the fights leave a faint trace that
draws the map's corridors as the match goes on. Higher floors are warmer.

Uses the server's game log, set up like the CS2 server kill feed:
    log on
    logaddress_add_http "http://<this computer's address>:3001"
For many more points, also add  mp_logdetail 3  (every hit, not just kills).
"""
import time

params = {
    "port": slider(3001, 1024, 65000, step=1),
    "flash": slider(6, 2, 20, step=1, label="kill flash (s)"),
    "trace": checkbox(True, label="draw the map from the fights"),
}

CT, TT = (0.35, 0.6, 1.0), (1.0, 0.6, 0.15)
LOW, HIGH = (0.2, 0.32, 0.6), (0.65, 0.5, 0.25)        # trace colors: lowest and highest floor


def team_col(team):
    return CT if team == "CT" else TT if team == "T" else (0.8, 0.8, 0.85)


def setup(c):
    c.view = None               # [min x, max x, min y, max y] in game units, eased toward the fights' extent


def waiting(c, t):
    ip = local_ip()
    lines = ["Add to the server's server.cfg:", "log on", "logaddress_add_http", f'"http://{ip}:{port}"']
    c.text("KILL MAP", c.w / 2, c.h * 0.13, mix(TT, CT, wave(t * 0.3)), c.fit_size("KILL MAP", c.w * 0.9, c.h * 0.2),
           "Archivo Black")
    for i, s in enumerate(lines):
        c.text(s, c.w / 2, c.h * (0.38 + i * 0.16), (0.6, 0.62, 0.7) if i == 0 else white,
               c.fit_size(s, c.w * 0.94, c.h * 0.12))


def short_map(m):
    m = m.split("/")[-1]
    for p in ("de_", "cs_", "ar_", "dm_", "workshop_"):
        if m.startswith(p):
            m = m[len(p):]
    return m.replace("_", " ").upper()


def frame(c, t):
    c.clear()
    log = server_log(port)
    if log["error"]:
        c.text(log["error"], c.w / 2, c.h / 2, "#ff6060", c.fit_size(log["error"], c.w * 0.95, c.h * 0.14))
        return
    if not log["time"]:
        waiting(c, t)
        return
    spots = np.array([s[1:4] for s in log["spots"]], np.float32).reshape(-1, 3)
    if log["map"]:
        c.text(short_map(log["map"]), 2, 1, (0.45, 0.47, 0.55), c.h * 0.11, align="left", valign="top")
    if len(spots) < 2:
        msg = "Waiting for the first kill..."
        c.text(msg, c.w / 2, c.h / 2, (0.6, 0.62, 0.7), c.fit_size(msg, c.w * 0.9, c.h * 0.13))
        return

    # View: the area the fights cover, with a margin; it grows smoothly as new spots appear
    x0, y0 = spots[:, 0].min(), spots[:, 1].min()
    x1, y1 = spots[:, 0].max(), spots[:, 1].max()
    pad = max(x1 - x0, y1 - y0, 400) * 0.06
    want = np.array([x0 - pad, x1 + pad, y0 - pad, y1 + pad], np.float32)
    if c.view is None or abs(want[1] - want[0]) < abs(c.view[1] - c.view[0]) * 0.5:   # new map: jump
        c.view = want
    c.view = c.view + (want - c.view) * min(1.0, c.dt * 2)
    vx0, vx1, vy0, vy1 = c.view
    s = min((c.w - 2) / max(1.0, vx1 - vx0), (c.h - 2) / max(1.0, vy1 - vy0))
    ox = (c.w - (vx1 - vx0) * s) / 2
    oy = (c.h - (vy1 - vy0) * s) / 2

    def to_screen(x, y):                         # game: y points north; screen: y points down
        return ox + (x - vx0) * s, oy + (vy1 - y) * s

    # Trace: every spot, dim, warmer for higher floors (bright where lots happens)
    if trace:
        z = spots[:, 2]
        k = ((z - z.min()) / max(1.0, float(z.max() - z.min())))[:, None]
        cols = np.array(LOW) * (1 - k) + np.array(HIGH) * k
        sx, sy = to_screen(spots[:, 0], spots[:, 1])
        c.points(sx, sy, cols, alpha=min(1.0, max(0.4, 25 / len(spots) ** 0.5)))   # brighter while there are few

    # Kills of the last few seconds: killer -> victim line, a dot and a ring that grows and fades
    now = time.time()
    for e in log["events"]:
        if e["type"] != "kill" or not e.get("victim_pos") or now - e["time"] > flash:
            continue
        age = (now - e["time"]) / flash
        fade = (1 - age) ** 1.5
        col = team_col(e["killer_team"])
        vx, vy = to_screen(*e["victim_pos"][:2])
        if e.get("killer_pos"):
            kx, ky = to_screen(*e["killer_pos"][:2])
            c.line(kx, ky, vx, vy, col, alpha=0.5 * fade)
            c.circle(kx, ky, 1.0, col, alpha=0.8 * fade)
        r = 1.5 + (now - e["time"]) * c.h * 0.03
        c.circle(vx, vy, r, white if e["headshot"] else col, width=1, alpha=0.7 * fade)
        c.circle(vx, vy, 1.6, col, alpha=fade)
