"""News ticker
Headlines from any news feed (RSS or Atom) scrolling under a title bar,
one after another. Works with most news sites, blogs and podcasts: look
for "RSS" on the site and paste the feed address.
"""

params = {
    "feed": textbox("https://feeds.npr.org/1001/rss.xml", label="feed address"),
    "title": textbox("NEWS"),
    "bar": color_picker("#c01030", label="title bar"),
    "speed": slider(1.0, 0.3, 3.0),
}


def setup(c):
    c.x = None
    c.index = 0


def frame(c, t):
    c.clear()
    bar_h = c.h * 0.3
    c.rect(0, 0, c.w, bar_h, bar)
    c.text(title, c.h * 0.08, bar_h / 2, white, bar_h * 0.75, "Archivo Black", align="left")
    clock = time.strftime("%I:%M").lstrip("0")
    c.text(clock, c.w - c.h * 0.08, bar_h / 2, white, bar_h * 0.6, align="right")
    news = headlines(feed)
    if not news:
        err = data_error("headlines")
        msg = err or "Loading headlines..."
        c.text(msg, c.w / 2, c.h * 0.65, "#ff6060" if err else (0.6, 0.6, 0.7), c.fit_size(msg, c.w * 0.95, c.h * 0.2))
        return
    size = c.h * 0.42
    line = news[c.index % len(news)]
    w = c.text_width(line, size)
    if c.x is None:
        c.x = c.w * 0.25                          # the first one starts partly on screen
    c.x -= c.dt * speed * c.h * 0.9
    if c.x < -w:                                  # this one has gone: next headline
        c.index += 1
        c.x = c.w
    c.text(line, c.x, bar_h + (c.h - bar_h) / 2, white, size, align="left")
    c.rect(0, bar_h, c.w, max(1, c.h * 0.02), bar, alpha=0.6)


import time
