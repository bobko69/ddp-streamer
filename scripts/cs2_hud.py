"""CS2 live HUD
Your Counter-Strike 2 game on the wall while you play: health and armor,
ammo, money, the score and your kills, with effects - the wall goes white
when you're flashed, pulses red when you take damage, bursts on a kill,
counts down a planted bomb and sweeps the winning team's color.

Uses Valve's Game State Integration (VAC-safe), so it works on any server.
Setup: the script saves the config file in My scripts (Folder button);
copy it into CS2's game\\csgo\\cfg folder on the gaming PC and restart CS2.
"""

params = {
    "other_pc": checkbox(True, label="CS2 runs on another computer"),
    "port": slider(3000, 1024, 65000, step=1),
}

CT, TT = (0.35, 0.6, 1.0), (1.0, 0.6, 0.15)          # team colors
BOMB_TIME = 40.0


def setup(c):
    host = local_ip() if other_pc else "127.0.0.1"
    written, copy = cs2_setup(port, host)
    name = os.path.basename(copy)
    if written:
        c.help = ["Start CS2 (or restart it)", "and play a game"]
    else:
        c.help = [f"Copy {name} (in My scripts)", "into CS2's game\\csgo\\cfg folder", f"on the gaming PC (sends to {host})",
                  "then start CS2"]
    c.prev = {}
    c.fx = []                     # effects: [kind, start time, extra]
    c.bomb_since = None


def get(s, path, default=0):
    v = get_path(s, path)
    return default if v is None else v


def bar(c, x, y, w, h, frac, color):
    c.rect(x, y, w, h, (0.12, 0.12, 0.16))
    c.rect(x, y, max(0.0, w * clamp(frac)), h, color)


def waiting(c, t):
    c.text("CS2", c.w / 2, c.h * 0.17, mix(TT, CT, wave(t * 0.3)), c.h * 0.26, "Archivo Black")
    c.text("Waiting for the game", c.w / 2, c.h * 0.38, white, c.fit_size("Waiting for the game", c.w * 0.9, c.h * 0.12))
    err = data_error("game")
    lines = [err] if err else c.help
    for i, line in enumerate(lines):
        c.text(line, c.w / 2, c.h * (0.54 + i * 0.12), "#ff6060" if err else (0.6, 0.62, 0.7),
               c.fit_size(line, c.w * 0.95, c.h * 0.1))


def frame(c, t):
    c.clear()
    s = game_state(port, other_pc)
    info = game_info(port)
    if s is None or info["age"] is None or info["age"] > 30 or "player" not in s:
        waiting(c, t)
        return
    me = s["player"]
    hp, armor = get(me, "state.health"), get(me, "state.armor")
    kills, hs = get(me, "state.round_kills"), get(me, "state.round_killhs")
    phase, bomb = get(s, "round.phase", ""), get(s, "round.bomb", "")

    # ---- what just happened? compare with the last frame -------------------------
    p = c.prev
    if p:
        if 0 < hp < p["hp"]:
            c.fx.append(["hurt", t, p["hp"] - hp])
        if kills > p["kills"]:
            c.fx.append(["headshot" if hs > p["hs"] else "kill", t, None])
        if bomb == "planted" and p["bomb"] != "planted":
            c.bomb_since = t
        if bomb in ("defused", "exploded") and p["bomb"] != bomb:
            c.fx.append([bomb, t, None])
        if phase == "over" and p["phase"] != "over":
            c.fx.append(["win", t, get(s, "round.win_team", "")])
    if bomb != "planted":
        c.bomb_since = None
    c.prev = dict(hp=hp, kills=kills, hs=hs, bomb=bomb, phase=phase)
    team = CT if me.get("team") == "CT" else TT

    # ---- bomb ticking: a red pulse that speeds up ----------------------------------
    left = None
    if c.bomb_since is not None:
        left = max(0.0, BOMB_TIME - (t - c.bomb_since))
        rate = 1 + (BOMB_TIME - left) / 8
        c.clear(mix(black, (0.4, 0, 0), 1 - fract((t - c.bomb_since) * rate)))

    # ---- score line ------------------------------------------------------------------
    m = s.get("map", {})
    sz = c.h * 0.17
    c.text(str(get(m, "team_ct.score")), c.w * 0.08, sz * 0.7, CT, sz, "Archivo Black")
    c.text(str(get(m, "team_t.score")), c.w * 0.92, sz * 0.7, TT, sz, "Archivo Black")
    if left is not None:
        mid, col = f"BOMB {left:.0f}", (1, 0.3, 0.3)
    elif phase == "freezetime":
        mid, col = "BUY TIME", (0.4, 1, 0.5)
    else:
        mid, col = f"ROUND {get(m, 'round') + 1}", (0.6, 0.62, 0.7)
    c.text(mid, c.w / 2, sz * 0.7, col, c.fit_size(mid, c.w * 0.6, sz * 0.8))

    # ---- health, armor, ammo, money ------------------------------------------------------
    if hp > 0:
        hcol = mix((1, 0.15, 0.1), (0.2, 1, 0.4), hp / 100)
        big = c.h * 0.36
        c.text(str(hp), c.w * 0.03, c.h * 0.5, hcol, big, "Archivo Black", align="left")
        bx = c.w * 0.06 + c.text_width("100", big, "Archivo Black")
        bar(c, bx, c.h * 0.4, c.w * 0.95 - bx, c.h * 0.11, hp / 100, hcol)
        bar(c, bx, c.h * 0.55, c.w * 0.95 - bx, c.h * 0.05, armor / 100, (0.5, 0.7, 1.0))
        gun = next((w for w in (me.get("weapons") or {}).values() if w.get("state") == "active"), {})
        if "ammo_clip" in gun:
            ammo = f"{gun['ammo_clip']}/{gun.get('ammo_reserve', 0)}"
            low = gun["ammo_clip"] <= max(1, gun.get("ammo_clip_max", 30) // 4)
            c.text(ammo, c.w * 0.97, c.h * 0.84, (1, 0.35, 0.3) if low and fract(t * 2) < 0.5 else white,
                   c.h * 0.2, "Archivo Black", align="right")
        c.text(f"${get(me, 'state.money'):,}", c.w * 0.03, c.h * 0.84, (0.4, 1, 0.5), c.h * 0.18, align="left")
        for i in range(kills):                                      # this round's kills
            c.circle(c.w * 0.45 + i * c.h * 0.12, c.h * 0.84, c.h * 0.045, team)
    else:                                                           # dead: the match so far
        st = me.get("match_stats", {})
        kda = f"K / D / A   {st.get('kills', 0)} / {st.get('deaths', 0)} / {st.get('assists', 0)}"
        c.text("DEAD", c.w / 2, c.h * 0.48, (0.5, 0.5, 0.55), c.h * 0.3, "Archivo Black")
        c.text(kda, c.w / 2, c.h * 0.8, (0.7, 0.7, 0.75), c.fit_size(kda, c.w * 0.9, c.h * 0.14))

    # ---- effects on top ----------------------------------------------------------------------
    for fx in c.fx[:]:
        kind, t0, extra = fx
        age = t - t0
        if kind == "hurt" and age < 0.5:                            # red glow round the edge
            k = (1 - age / 0.5) * min(1.0, extra / 40)
            edge = smoothstep(0.25, 0.5, np.maximum(abs(c.x - 0.5), abs(c.y - 0.5)))
            c.img[..., 0] += edge * k
        elif kind in ("kill", "headshot") and age < 0.8:            # a ring bursting out
            k = 1 - age / 0.8
            c.circle(c.w / 2, c.h / 2, c.w * (0.1 + age), (1, 0.9, 0.3), width=max(1, c.h * 0.04), alpha=k)
            if kind == "headshot":
                c.img *= 1 - 0.7 * k                                 # dim the HUD behind the word
                c.text("HEADSHOT", color=(1, 0.95, 0.5), size=c.fit_size("HEADSHOT", c.w * 0.9, c.h * 0.3),
                       font="Archivo Black", alpha=k)
        elif kind in ("defused", "exploded") and age < 2.0:
            col = CT if kind == "defused" else (1, 0.5, 0.1)
            if kind == "exploded":
                c.clear(mix(col, black, age / 2))
            word = kind.upper()
            c.text(word, color=white if kind == "exploded" else col, size=c.fit_size(word, c.w * 0.9, c.h * 0.4),
                   font="Archivo Black", alpha=1 - age / 2)
        elif kind == "win" and age < 2.5:                          # the winners' color sweeps across
            col = CT if extra == "CT" else TT
            c.img *= 0.25 + 0.75 * smoothstep(1.8, 2.5, age)        # dim the HUD behind the words
            x = (age / 2.5) * c.w * 1.5 - c.w * 0.25
            c.rect(x - c.w * 0.25, 0, c.w * 0.25, c.h, col, alpha=0.5)
            c.text(f"{'CT' if extra == 'CT' else 'T'} WIN", color=col, size=c.h * 0.35, font="Archivo Black",
                   alpha=min(1.0, (2.5 - age) * 2))
        elif age > 2.5:
            c.fx.remove(fx)
    flashed = get(me, "state.flashed") / 255                        # flashbang: everything to white
    if flashed > 0:
        c.img += (1 - c.img) * flashed
    c.glow(0.3)


import os
