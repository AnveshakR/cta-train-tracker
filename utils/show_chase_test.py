# Chase test: every strip lights LED 0..N-1 in turn, in its own colour.
# First lit LED = LED 0, travel direction = strip direction, and the strip's
# real length is where it goes dark. Uses main.py's SEQ/set_color interface.
import neopixel
from machine import Pin

STRIPS = [
    ("red",    12),
    ("blue",   13),
    ("green",  14),
    ("brown",  15),
    ("pink",   19),
    ("orange", 5),    # no D17 on this board
    ("purple", 18),
]

STRIP_LEDS = {
    "red": 10, "blue": 10, "green": 10, "brown": 10,
    "pink": 10, "orange": 10, "purple": 10,
}
LEDS_PER_STRIP = 10  # status page only
BRIGHTNESS = 64
STEP_MS = 1000

STRIP_RGB = {
    "red":    (255, 0, 0),
    "blue":   (0, 0, 255),
    "green":  (0, 255, 0),
    "brown":  (150, 75, 0),
    "pink":   (255, 0, 255),
    "orange": (255, 140, 0),
    "purple": (150, 0, 255),
}
OFF = (0, 0, 0)

MAXN = max(STRIP_LEDS.values())
SEQ = [(str(i), i) for i in range(MAXN)]

NP = {}
for _name, _pin in STRIPS:
    NP[_name] = neopixel.NeoPixel(Pin(_pin, Pin.OUT), STRIP_LEDS[_name])


def set_color(color):
    """An int i lights LED i on every strip; an (r,g,b) fills every strip."""
    if isinstance(color, int):
        i = color
        for name, np in NP.items():
            np.fill(OFF)
            if i < len(np):
                np[i] = STRIP_RGB[name]
            np.write()
    else:
        for np in NP.values():
            np.fill(color)
            np.write()
