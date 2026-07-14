"""Dark, high-contrast, touch-friendly theme constants (§3.2).

Tk defaults are inadequate on a small screen in sunlight or at night; these
override them. Font sizes are applied to Tk's named fonts at startup (see
App._apply_theme) so every default-font widget scales together.
"""

# Palette — dark, high contrast
BG = "#0b0f14"          # window background, near-black
BG_PANEL = "#151d26"    # raised panels / status bar / rolling log
BG_BUTTON = "#20303f"
FG = "#e8edf2"          # primary text
FG_MUTED = "#8695a3"    # hint text (last values), secondary
ACCENT = "#3fa7ff"
OK = "#37c871"          # green — good fix
WARN = "#f2b134"        # amber — degraded / no fix / stale
BAD = "#e5484d"         # red   — offline / error

# Font sizes (points), applied to Tk named fonts at startup
SIZE_BASE = 16
SIZE_SMALL = 12
SIZE_LARGE = 22

# Sizing (pixels)
TOUCH_MIN = 44          # minimum touch target (invariant 10)
LOG_ROW = 20            # rolling-log row height
PAD = 8

# Window
MIN_W, MIN_H = 800, 480          # design floor (invariant 10)
DEFAULT_W, DEFAULT_H = 1000, 600  # comfortable on the current 1024x600 display
