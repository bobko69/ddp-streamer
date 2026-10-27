"""Rainbow wave
The simplest kind of script: one line that gives every pixel a color.
Start here.
"""

params = {
    "speed": slider(0.25, 0.05, 2.0),
    "stripes": slider(1.0, 0.25, 4.0),
}


def pixel(x, y, t):
    # x and y go from 0 to 1 across the display, t is seconds.
    # The hue changes across the display and moves over time; adding a bit
    # of y tilts the stripes.
    return hsv((x + y * 0.3) * stripes - t * speed, 1, 1)
