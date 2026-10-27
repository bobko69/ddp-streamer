"""Any number from the web
Shows one value from any web address that answers with JSON data - a
price, a counter, a sensor, a score. Set the address and the path to the
value; it flashes green when it goes up and red when it goes down. The
example shows the Bitcoin price from CoinGecko.
"""

params = {
    "url": textbox("https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd", label="address"),
    "path": textbox("bitcoin.usd", label="path to the value"),
    "label": textbox("BITCOIN"),
    "prefix": textbox("$"),
    "suffix": textbox(""),
    "decimals": slider(0, 0, 4, step=1),
    "every": slider(60, 15, 3600, step=1, label="refresh every (s)"),
}


def setup(c):
    c.last = None
    c.flash = 0.0
    c.flash_col = white


def frame(c, t):
    c.clear()
    data = fetch(url, every=every)
    value = get_path(data, path) if data is not None else None
    c.text(label, c.w / 2, c.h * 0.2, (0.6, 0.65, 0.75), c.fit_size(label, c.w * 0.9, c.h * 0.22), "Archivo Black")
    if value is None:
        err = data_error(url) or ("Nothing at that path" if data is not None else "")
        msg = err or "Loading..."
        c.text(msg, c.w / 2, c.h * 0.62, "#ff6060" if err else (0.6, 0.6, 0.7), c.fit_size(msg, c.w * 0.95, c.h * 0.18))
        return
    try:
        v = float(value)
        text = f"{prefix}{v:,.{decimals}f}{suffix}"
        if c.last is not None and v != c.last:
            c.flash, c.flash_col = 1.0, ((0.2, 1, 0.4) if v > c.last else (1, 0.25, 0.25))
        c.last = v
    except (TypeError, ValueError):
        text = f"{prefix}{value}{suffix}"                       # not a number: show it as text
    c.flash = max(0.0, c.flash - c.dt * 0.7)
    col = mix(white, c.flash_col, c.flash)
    c.text(text, c.w / 2, c.h * 0.62, col, c.fit_size(text, c.w * 0.95, c.h * 0.5, "Archivo Black"), "Archivo Black")
