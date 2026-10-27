"""CS2 server kill feed
Everything happening on your CS2 server, live: the team score, every kill
sliding in (who, with what, headshots), banners for the bomb, round wins,
a new match and players joining, and the leaderboard when it's quiet.

The server sends its game log here as it happens. One-time setup, in the
server's server.cfg (or its console):
    log on
    logaddress_add_http "http://<this computer's address>:3001"
The waiting screen shows the exact line for this computer. With the
joedwards32/cs2 Docker image, set CS2_LOG=on and
CS2_LOG_HTTP_URL=http://<this computer's address>:3001 instead.
"""

params = {
    "port": slider(3001, 1024, 65000, step=1),
    "chat": checkbox(False, label="show chat messages"),
    "rows": slider(3, 1, 6, step=1, label="kill rows"),
}

CT, TT = (0.35, 0.6, 1.0), (1.0, 0.6, 0.15)
BOMB_TIME = 40.0
WEAPONS = {"ak47": "AK-47", "m4a1": "M4A4", "m4a1_silencer": "M4A1-S", "m4a1_silencer_off": "M4A1-S", "awp": "AWP",
           "deagle": "DEAGLE", "usp_silencer": "USP-S", "glock": "GLOCK", "hkp2000": "P2000", "p250": "P250",
           "tec9": "TEC-9", "fiveseven": "FIVE-SEVEN", "cz75a": "CZ75", "revolver": "R8", "elite": "DUALIES",
           "ssg08": "SCOUT", "sg556": "SG 553", "aug": "AUG", "famas": "FAMAS", "galilar": "GALIL", "mp9": "MP9",
           "mac10": "MAC-10", "mp7": "MP7", "mp5sd": "MP5", "ump45": "UMP", "p90": "P90", "bizon": "BIZON",
           "nova": "NOVA", "xm1014": "XM1014", "mag7": "MAG-7", "sawedoff": "SAWED-OFF", "negev": "NEGEV",
           "m249": "M249", "g3sg1": "G3SG1", "scar20": "SCAR-20", "hegrenade": "HE", "inferno": "FIRE",
           "molotov": "MOLLY", "incgrenade": "FIRE", "taser": "ZEUS", "world": "WORLD"}


def weapon(w):
    w = w.replace("weapon_", "")
    if w.startswith("knife") or w == "bayonet":
        return "KNIFE"
    return WEAPONS.get(w, w.upper())


def team_col(team):
    return CT if team == "CT" else TT if team == "T" else (0.7, 0.7, 0.75)


def setup(c):
    c.seen = None               # last event already handled
    c.feed = []                 # kill rows: [event, time added]
    c.banner = None             # [text, color, time, seconds]
    c.bomb_at = None
    c.last_kill = -99.0


def banner(c, t, text, color, secs=2.5):
    c.banner = [text, color, t, secs]


def waiting(c, t):
    c.seen = 0                                   # nothing has happened yet: show everything that comes
    ip = local_ip()
    lines = ["Add to the server's server.cfg:", "log on", "logaddress_add_http", f'"http://{ip}:{port}"']
    c.text("CS2 SERVER", c.w / 2, c.h * 0.12, mix(TT, CT, wave(t * 0.3)), c.fit_size("CS2 SERVER", c.w * 0.9, c.h * 0.2),
           "Archivo Black")
    mono = c.fit_size(lines[3], c.w * 0.96, c.h * 0.15, "JetBrains Mono")      # same size for all code lines
    for i, line in enumerate(lines):
        font = "JetBrains Mono" if i else "Inter"
        size = mono if i else c.fit_size(line, c.w * 0.96, c.h * 0.13)
        c.text(line, c.w / 2, c.h * (0.36 + i * 0.18), white if i else (0.6, 0.62, 0.7), size, font)


def frame(c, t):
    c.clear()
    st = server_log(port)
    if st["error"] or not st["time"]:
        if st["error"]:
            c.text(st["error"], color="#ff6060", size=c.fit_size(st["error"], c.w * 0.95, c.h * 0.14))
        else:
            waiting(c, t)
        return
    # ---- new events since last frame -------------------------------------------------
    if c.seen is None:
        c.seen = st["seq"]                       # don't replay what happened before we started
    for ev in st["events"]:
        if ev["seq"] <= c.seen:
            continue
        c.seen = ev["seq"]
        k = ev["type"]
        if k == "kill":
            c.feed.append([ev, t])
            c.last_kill = t
        elif k == "planted":
            c.bomb_at = t
            banner(c, t, f"BOMB PLANTED {ev['site']}".strip(), (1, 0.25, 0.2), 2.0)
        elif k == "defused":
            c.bomb_at = None
            banner(c, t, "DEFUSED", CT)
        elif k == "win":
            c.bomb_at = None
            who = "CT" if ev["winner"] == "CT" else "T"
            banner(c, t, f"{who} WIN  {ev['ct']}-{ev['t']}", team_col(ev["winner"]), 3.0)
        elif k == "match_start":
            banner(c, t, f"{ev['map'].split('_')[-1].upper()} - GO!", (0.4, 1, 0.5), 3.0)
        elif k == "join":
            banner(c, t, f"{ev['player']} joined", (0.7, 0.75, 0.85), 2.0)
        elif k == "say" and chat:
            banner(c, t, f"{ev['player']}: {ev['text']}", team_col(ev["team"]), 4.0)
    if st["bomb"] != "planted":
        c.bomb_at = None
    c.feed = [f for f in c.feed if t - f[1] < 12][-rows:]      # kills stay 12 seconds

    # ---- planted bomb: red pulse speeding up -----------------------------------------
    if c.bomb_at is not None:
        since = t - c.bomb_at
        c.clear(mix(black, (0.35, 0, 0), 1 - fract(since * (1 + since / 8))))

    # ---- score bar ----------------------------------------------------------------------
    bar_h = c.h * 0.24
    c.rect(0, 0, c.w / 2, bar_h, CT, alpha=0.18)
    c.rect(c.w / 2, 0, c.w / 2, bar_h, TT, alpha=0.18)
    c.text(str(st["ct"]), c.w * 0.03, bar_h / 2, CT, bar_h * 0.9, "Archivo Black", align="left")
    c.text(str(st["t"]), c.w * 0.97, bar_h / 2, TT, bar_h * 0.9, "Archivo Black", align="right")
    mid = st["map"].split("_")[-1].upper() or "CS2"
    if st["round"]:
        mid += f"  R{st['round']}"
    if c.bomb_at is not None:
        mid = f"BOMB {max(0, BOMB_TIME - (t - c.bomb_at)):.0f}"
    c.text(mid, c.w / 2, bar_h / 2, (1, 0.35, 0.3) if c.bomb_at is not None else (0.75, 0.77, 0.85),
           c.fit_size(mid, c.w * 0.5, bar_h * 0.6))

    # ---- kill feed, or the leaderboard when it's quiet -------------------------------------
    area = c.h - bar_h
    row = area / max(rows, 1)
    if c.feed:
        for i, (ev, added) in enumerate(reversed(c.feed)):          # newest on top
            y = bar_h + (i + 0.5) * row
            slide = clamp((t - added) / 0.25)
            x0 = c.w * (1 - slide) + c.w * 0.02
            size = row * 0.62
            killer, victim, gun = ev["killer"], ev["victim"], weapon(ev["weapon"])
            extra = " HS" if ev["headshot"] else ""
            wk = c.text_width(killer, size)
            wg = c.text_width(gun + extra, size * 0.75, "Archivo Black")
            if t - added < 0.4:                                     # flash the new row
                c.rect(0, y - row / 2, c.w, row, (1, 1, 1), alpha=0.25 * (1 - (t - added) / 0.4))
            c.text(killer, x0, y, team_col(ev["killer_team"]), size, align="left")
            c.text(gun, x0 + wk + size * 0.4, y, (0.8, 0.8, 0.85), size * 0.75, "Archivo Black", align="left")
            if extra:
                c.text("HS", x0 + wk + size * 0.4 + c.text_width(gun + " ", size * 0.75, "Archivo Black"), y,
                       (1, 0.85, 0.2), size * 0.75, "Archivo Black", align="left")
            c.text(victim, x0 + wk + wg + size * 0.8, y, team_col(ev["victim_team"]), size, align="left")
    else:
        best = sorted(st["players"].values(), key=lambda p: (-p["kills"], p["deaths"]))[:max(1, min(3, rows))]
        r = area / max(1, len(best))
        for i, p in enumerate(best):
            y = bar_h + (i + 0.5) * r
            size = min(r * 0.62, c.h * 0.2)
            col = team_col(p["team"])
            c.text(f"{i + 1}", c.w * 0.03, y, (0.5, 0.52, 0.6), size, align="left")
            c.text(p["name"], c.w * 0.12, y, col, c.fit_size(p["name"], c.w * 0.5, size), align="left")
            kd = f"{p['kills']}/{p['deaths']}"
            c.text(kd, c.w * 0.97, y, white, size, "Archivo Black", align="right")

    # ---- banner on top --------------------------------------------------------------------------
    if c.banner:
        text, col, t0, secs = c.banner
        age = t - t0
        if age > secs:
            c.banner = None
        else:
            k = min(1.0, age / 0.2, (secs - age) / 0.4)
            c.img *= 1 - 0.75 * k
            c.text(text, color=col, size=c.fit_size(text, c.w * 0.94, c.h * 0.32, "Archivo Black"),
                   font="Archivo Black", alpha=k)
    c.glow(0.3)
