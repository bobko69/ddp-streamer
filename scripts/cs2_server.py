"""Game server status
Who's on your game server: the map, how many are playing, and the players
with their scores, best first. Uses the standard Steam server query, so
it works with CS2 and most Steam game servers (TF2, Rust, ARK, Valheim,
Garry's Mod...). Nothing to set up on the server. CS2 servers leave the
players' names blank in that query, so for CS2 the list comes from the
server's log instead, if the server sends it here (see the manual).
"""
import time

params = {
    "server": textbox("192.168.1.50:27015", label="server address:port"),

    "every": slider(5, 2, 60, step=1, label="refresh every (s)"),
    "columns": slider(2, 1, 3, label="player list columns"),
    "middle": dropdown("Name + team score", "Server name", "Latest kill", label="top middle shows"),
}

ACCENT = (1.0, 0.6, 0.15)
LOG_PORT = 3001         # where a CS2 server sends its log (logaddress_add_http / CS2_LOG_HTTP_URL)


# Map badge: two letters on the map's color (any other map: its first two letters)
MAPS = {"dust2": ("D2", "#e0a85a"), "mirage": ("MI", "#e8c46a"), "inferno": ("IN", "#f0643a"),
        "nuke": ("NK", "#4fc3f7"), "overpass": ("OV", "#6fcf7f"), "ancient": ("AC", "#3fae74"),
        "anubis": ("AN", "#f2c94c"), "vertigo": ("VE", "#8ea6f5"), "train": ("TR", "#b0b4c0"),
        "office": ("OF", "#d6dcf0"), "italy": ("IT", "#f29a72"), "cache": ("CA", "#9be36b")}


def short_map(m):
    m = m.split("/")[-1]
    for p in ("de_", "cs_", "ar_", "dm_", "gg_", "workshop_"):
        if m.startswith(p):
            m = m[len(p):]
    return m


def map_badge(c, m, x, y, s):
    """Square badge, s pixels, top-left at x, y."""
    m = short_map(m)
    letters, color = MAPS.get(m, (m[:2].upper() or "?", "#c0c4d0"))
    k = s * 0.22                                            # cut corners
    c.polygon([(x + k, y), (x + s - k, y), (x + s, y + k), (x + s, y + s - k),
               (x + s - k, y + s), (x + k, y + s), (x, y + s - k), (x, y + k)], color)
    c.text(letters, x + s / 2, y + s / 2, (0.06, 0.07, 0.12), c.fit_size(letters, s * 0.8, s * 0.6, "Archivo Black"),
           "Archivo Black")


CT_BLUE, T_GOLD = (0.45, 0.7, 1.0), (1.0, 0.75, 0.3)


def score_parts(log, current):
    """Team score as text pieces: rounds won if the mode has rounds, else each team's kills."""
    ct, tt = log["ct"], log["t"]
    if not ct and not tt:
        ct = sum(p["kills"] for p in current if p.get("team") == "CT")
        tt = sum(p["kills"] for p in current if p.get("team") == "T")
    return [("CT ", CT_BLUE, "Inter"), (str(ct), white, "Archivo Black"), (" : ", (0.5, 0.52, 0.6), "Inter"),
            (str(tt), white, "Archivo Black"), (" T", T_GOLD, "Inter")]


def draw_parts(c, parts, x0, w, y, size):
    s = min(size, c.fit_size("".join(p for p, _, _ in parts), w, size, "Archivo Black"))
    widths = [c.text_width(p, s, f) for p, _, f in parts]
    x = x0 + (w - sum(widths)) / 2
    for (p, col, f), pw in zip(parts, widths):
        c.text(p, x, y, col, s, f, align="left")
        x += pw


def header_middle(c, info, log, current, x0, x1, top):
    """Between the map badge and the player count: the server's name and the
    team score, or the latest kill (for 5 s), or just the name."""
    w, name, grey = max(4, x1 - x0), info["name"], (0.55, 0.58, 0.68)
    if middle == "Latest kill" and log:
        kills = [e for e in log["events"] if e["type"] == "kill" and time.time() - e["time"] < 5]
        if kills:
            e = kills[-1]
            mid = " HS > " if e["headshot"] else " > "
            draw_parts(c, [(e["killer"], CT_BLUE if e["killer_team"] == "CT" else T_GOLD, "Inter"),
                           (mid, (0.5, 0.52, 0.6), "Inter"),
                           (e["victim"], CT_BLUE if e["victim_team"] == "CT" else T_GOLD, "Inter")],
                       x0, w, (top - 1) / 2, top * 0.62)
            return
    if middle == "Name + team score" and log and log["time"]:
        if top < 18:                                # small display: two lines would be unreadable
            draw_parts(c, score_parts(log, current), x0, w, (top - 1) / 2, top * 0.62)
        else:                                       # one line: name, then the score
            draw_parts(c, [(name + "   ", grey, "Inter")] + score_parts(log, current), x0, w, (top - 1) / 2, top * 0.6)
        return
    c.text(name, x0 + w / 2, (top - 1) / 2, grey, c.fit_size(name, w, top * 0.53))

def frame(c, t):
    c.clear()
    info = game_server(server, every)
    if info is None:
        err = data_error("server")
        msg = err or "Asking the server..."
        c.text("GAME SERVER", c.w / 2, c.h * 0.3, ACCENT, c.fit_size("GAME SERVER", c.w * 0.9, c.h * 0.22), "Archivo Black")
        c.text(msg, c.w / 2, c.h * 0.65, "#ff6060" if err else (0.6, 0.62, 0.7), c.fit_size(msg, c.w * 0.95, c.h * 0.13))
        return
    top = round(c.h * 0.2)                                  # header: map badge left, players right
    map_badge(c, info["map"], 1, 1, top - 3)
    humans = max(0, info["players"] - info["bots"])
    count = f"{humans}/{info['max_players']}"
    cs = c.fit_size(count, c.w * 0.4, top * 0.8, "Archivo Black")
    c.text(count, c.w - 2, (top - 1) / 2, white, cs, "Archivo Black", align="right")
    c.rect(0, top - 1, c.w, 1, ACCENT, alpha=0.6)
    x0, x1 = top + 2, c.w - 2 - c.text_width(count, cs, "Archivo Black") - 4     # free space in the header
    log = server_log(LOG_PORT) if (not info["list"] and info["players"]) or middle != "Server name" else None
    # who's on now: the log remembers leavers too, so keep the most recently seen
    current = sorted(log["players"].values(), key=lambda p: -p.get("seen", 0))[:info["players"]] if log else []
    header_middle(c, info, log, current, x0, x1, top)
    players = info["list"]
    if not players and log and log["time"]:         # people on, but no names (CS2): use the server log
        players = sorted(({"name": p["name"], "score": p["kills"], "bot": p.get("bot")} for p in current),
                         key=lambda p: -p["score"])
    if not players:
        if info["players"]:
            lines = [f"{humans} playing, {info['bots']} bots", "Names: send the server's", "log here (see manual)"]
        else:
            lines = ["Nobody playing - jump in!"]
        for i, msg in enumerate(lines):
            y = top + (c.h - top) * (i + 0.5) / len(lines)
            c.text(msg, c.w / 2, y, (0.6, 0.62, 0.7) if i else white, c.fit_size(msg, c.w * 0.92, c.h * 0.14))
        return
    rows = max(1, int((c.h - top - 1) / (c.h * 0.15)))      # as many rows as fit, spread evenly
    row = (c.h - top - 1) / rows
    per_page = rows * columns
    pages = (len(players) + per_page - 1) // per_page
    page = int(t / 4) % pages                                   # a new page every 4 seconds
    cw = c.w / columns                                          # one column: rank, name, score
    for i, p in enumerate(players[page * per_page:(page + 1) * per_page]):
        x, y = (i // rows) * cw, top + 1 + (i % rows + 0.5) * row   # down the first column, then the next
        rank = page * per_page + i + 1
        col = (1, 0.85, 0.3) if rank == 1 else (0.6, 0.62, 0.7) if p.get("bot") else white
        num = str(p["score"])
        size = row * 0.8
        c.text(str(rank), x + 2, y, (0.5, 0.52, 0.6), size * 0.9, align="left")
        nx = x + max(cw * 0.12 if columns == 1 else 0, c.text_width(rank, size * 0.9) + 4)
        room = x + cw - 3 - c.text_width(num, size, "Archivo Black") - 4 - nx
        name, ns = p["name"], c.fit_size(p["name"], room, size)
        if ns < size * 0.7:                     # too long to shrink readably: cut it short
            ns = size * 0.7
            while len(name) > 1 and c.text_width(name + ".", ns) > room:
                name = name[:-1]
            name += "."
        c.text(name, nx, y, col, ns, align="left")
        c.text(num, x + cw - 3, y, col, size, "Archivo Black", align="right")
    for k in range(1, columns):                                 # thin line between columns
        c.rect(k * cw - 1, top + 2, 1, c.h - top - 4, (0.3, 0.32, 0.4))
