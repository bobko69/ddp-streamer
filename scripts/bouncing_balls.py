"""Bouncing balls
Balls thrown in with gravity, bouncing off the floor and the walls and
losing a little energy each bounce. Shows how to keep a list of things
between frames and move them with c.dt.
"""

params = {
    "balls": slider(8, 1, 30, step=1),
    "gravity": slider(1.0, 0.0, 3.0),
    "bounce": slider(0.85, 0.5, 1.0),
    "trails": checkbox(True),
}


def new_ball(c):
    r = rand(c.h * 0.04, c.h * 0.1)
    return dict(x=rand(r, c.w - r), y=rand(r, c.h * 0.5), vx=rand(-1, 1) * c.w * 0.5,
                vy=rand(-0.5, 0) * c.h, r=r, color=hsv(rand(), 0.8, 1))


def setup(c):
    c.balls = [new_ball(c) for _ in range(balls)]


def frame(c, t):
    if trails:
        c.fade(0.75)
    else:
        c.clear()
    for b in c.balls:
        b["vy"] += c.h * 2.0 * gravity * c.dt
        b["x"] += b["vx"] * c.dt
        b["y"] += b["vy"] * c.dt
        r = b["r"]
        if b["y"] > c.h - r:                          # floor
            b["y"] = c.h - r
            b["vy"] = -abs(b["vy"]) * bounce
            if abs(b["vy"]) < c.h * 0.3:              # nearly stopped: throw it again
                b["vy"] = -c.h * rand(1.2, 1.8)
        if b["x"] < r or b["x"] > c.w - r:            # walls
            b["vx"] = -b["vx"]
            b["x"] = clamp(b["x"], r, c.w - r)
        c.circle(b["x"], b["y"], r, b["color"])
        c.circle(b["x"] - r * 0.3, b["y"] - r * 0.3, r * 0.3, (1, 1, 1), alpha=0.5)   # shine
