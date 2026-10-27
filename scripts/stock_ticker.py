"""Stock ticker
Live prices scrolling across the wall, green when up and red when down,
each with a little chart of today. Works for stocks (AAPL), indexes
(^GSPC is the S&P 500), crypto (BTC-USD) and currencies (EURUSD=X).
"""

params = {
    "symbols": textbox("AAPL MSFT NVDA ^GSPC BTC-USD ETH-USD", label="symbols (space between)"),
    "speed": slider(1.0, 0.3, 3.0),
    "charts": checkbox(True, label="little charts"),
}

UP, DOWN = (0.2, 1, 0.4), (1, 0.25, 0.25)


def setup(c):
    c.scroll_x = 0.0


def item(c, q, x, size):
    """Draw one quote starting at x; returns its width."""
    col = UP if q["change"] >= 0 else DOWN
    name = q["symbol"].replace("^GSPC", "S&P 500").replace("-USD", "")
    price = f"{q['price']:,.2f}" if q["price"] < 10000 else f"{q['price']:,.0f}"
    pct = f"{abs(q['change_pct']):.2f}%"
    y = c.h * (0.42 if charts else 0.5)
    w1 = c.text_width(name, size, "Archivo Black")
    w2 = c.text_width(price, size, "Inter")
    w3 = c.text_width(pct, size * 0.8, "Inter")
    if x < c.w and x + w1 + w2 + w3 + size * 2 > 0:
        c.text(name, x, y, white, size, "Archivo Black", align="left")
        c.text(price, x + w1 + size * 0.4, y, (0.85, 0.85, 0.9), size, align="left")
        ax = x + w1 + w2 + size * 0.9
        a = size * 0.3                                           # up / down triangle
        if q["change"] >= 0:
            c.polygon([(ax, y + a * 0.6), (ax + a * 2, y + a * 0.6), (ax + a, y - a * 0.9)], col)
        else:
            c.polygon([(ax, y - a * 0.6), (ax + a * 2, y - a * 0.6), (ax + a, y + a * 0.9)], col)
        c.text(pct, ax + a * 2.4, y, col, size * 0.8, align="left")
        hist = q["history"]
        if charts and len(hist) > 1:
            lo, hi = min(hist), max(hist)
            cw = w1 + w2 + w3 + size * 1.6
            ys = [c.h * 0.95 - (v - lo) / max(1e-9, hi - lo) * c.h * 0.22 for v in hist]
            xs = [x + cw * i / (len(hist) - 1) for i in range(len(hist))]
            for i in range(len(xs) - 1):
                c.line(xs[i], ys[i], xs[i + 1], ys[i + 1], col, width=1, alpha=0.8)
    return w1 + w2 + w3 + size * 2.0


def frame(c, t):
    c.clear()
    quotes = [q for q in (stock(s) for s in symbols.split()) if q]
    if not quotes:
        err = data_error("stock")
        msg = err or "Loading prices..."
        c.text(msg, color="#ff6060" if err else warm_white, size=c.fit_size(msg, c.w * 0.95, c.h * 0.2))
        return
    size = c.h * (0.36 if charts else 0.5)
    widths = [item(c, q, -10 ** 6, size) for q in quotes]          # measure only (drawn off-screen)
    total = sum(widths) + c.w * 0.1
    c.scroll_x = (c.scroll_x + c.dt * speed * c.h * 0.9) % total
    x = -c.scroll_x
    while x < c.w:                                                # repeat the strip to fill the width
        for q, wd in zip(quotes, widths):
            item(c, q, x, size)
            x += wd
        x += c.w * 0.1
