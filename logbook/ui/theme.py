"""Theme: a light scheme for daylight, a dark scheme for night (§3.2).

Tk's defaults are inadequate on a small screen in sunlight or at night, so both
palettes are defined here. ``use()`` rebinds this module's colour names; every
widget reads them at construction time, so switching theme means re-showing the
current view — which is exactly what ``App.toggle_theme`` does.

The scope chose dark by default. On the netbook in daylight that proved too dark,
so **light is the default and dark is kept as the night mode**. Selectable in
config.json (``"ui": {"theme": "light"|"dark"}``) and toggled with **F2** at the
chart table.
"""

LIGHT = {
    "BG": "#f2f4f7", "BG_PANEL": "#e2e8ee", "BG_BUTTON": "#ccd6df",
    "FG": "#11202e", "FG_MUTED": "#54626f", "ACCENT": "#0b5fbe",
    "OK": "#0f7b3a", "WARN": "#8a5300", "BAD": "#b3261e",
}
DARK = {
    "BG": "#0b0f14", "BG_PANEL": "#151d26", "BG_BUTTON": "#20303f",
    "FG": "#e8edf2", "FG_MUTED": "#8695a3", "ACCENT": "#3fa7ff",
    "OK": "#37c871", "WARN": "#f2b134", "BAD": "#e5484d",
}
PALETTES = {"light": LIGHT, "dark": DARK}

MODE = "light"
BG = BG_PANEL = BG_BUTTON = FG = FG_MUTED = ACCENT = OK = WARN = BAD = ""


def use(mode: str) -> str:
    """Switch palette. Unknown modes fall back to light. Returns the mode applied."""
    global MODE, BG, BG_PANEL, BG_BUTTON, FG, FG_MUTED, ACCENT, OK, WARN, BAD
    MODE = mode if mode in PALETTES else "light"
    palette = PALETTES[MODE]
    BG = palette["BG"]
    BG_PANEL = palette["BG_PANEL"]
    BG_BUTTON = palette["BG_BUTTON"]
    FG = palette["FG"]
    FG_MUTED = palette["FG_MUTED"]
    ACCENT = palette["ACCENT"]
    OK = palette["OK"]
    WARN = palette["WARN"]
    BAD = palette["BAD"]
    return MODE


def other(mode: str | None = None) -> str:
    """The mode that isn't the current one."""
    return "dark" if (mode or MODE) == "light" else "light"


def mix(hex_a: str, hex_b: str, t: float) -> str:
    """Blend two ``#rrggbb`` colours; ``t=0`` → a, ``t=1`` → b.

    Used to derive button hover and border shades from the palette, so they
    track light/dark automatically instead of being hand-picked per mode.
    """
    a = tuple(int(hex_a[i:i + 2], 16) for i in (1, 3, 5))
    b = tuple(int(hex_b[i:i + 2], 16) for i in (1, 3, 5))
    return "#%02x%02x%02x" % tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))


use("light")

# Font sizes (points), applied to Tk's named fonts at startup
SIZE_BASE = 16
SIZE_SMALL = 12
SIZE_LARGE = 22

# Sizing (pixels)
TOUCH_MIN = 44          # minimum touch target (invariant 10)
LOG_ROW = 20            # rolling-log row height
PAD = 8

# Window
MIN_W, MIN_H = 800, 480           # design floor (invariant 10)
DEFAULT_W, DEFAULT_H = 1000, 600  # comfortable on the netbook's 1024x600
