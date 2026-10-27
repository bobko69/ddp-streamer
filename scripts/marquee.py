"""Rainbow marquee
Your message scrolling across the display on a wave, each letter its own
color, over a soft moving background. Shows c.text() and c.text_width().
"""

params = {
    "message": textbox("BONG 69 LIGHTS"),
    "speed": slider(1.0, 0.2, 3.0),
    "font": dropdown("Archivo Black", "Anton", "Inter", "Press Start 2P", "Comic Neue", "JetBrains Mono"),
    "wave": slider(0.15, 0.0, 0.4),
}


def frame(c, t):
    # background: a dim, slowly moving gradient
    c.set(palette("party", c.x * 0.5 + c.y * 0.2 + t * 0.05))
    c.img *= 0.12
    size = c.h * 0.55
    gap = size * 0.08
    widths = [c.text_width(ch, size, font) for ch in message]
    total = sum(widths) + gap * len(message) + c.w * 0.5
    x = c.w - (t * speed * c.h * 0.8) % (total + c.w)       # start off the right edge
    for i, ch in enumerate(message):
        w = widths[i]
        if -w < x < c.w + w:
            y = c.h / 2 + sin(x / c.w * 2 * pi + t * 3) * c.h * wave
            c.text(ch, x + w / 2, y, hsv(i / max(1, len(message)) + t * 0.2), size, font)
        x += w + gap
    c.glow(0.4)
