"""PaddleCAT colour theme -- neon magenta + lime on deep purple, from the icon.

Import the palette names (BG, MAGENTA, LIME, ...) instead of hardcoding hex,
and call apply() once *before* the first CTk widget (including the root
window) is created: it restyles every standard customtkinter widget.
"""

import customtkinter as ctk

# -- Palette -----------------------------------------------------------------
BG        = "#130720"   # window background (the icon's purple-black)
SURFACE   = "#1e0d2e"   # frames / cards
SURFACE_2 = "#2b1542"   # inputs, unselected segments, menus
RAISED    = "#3a1d57"   # hover / tracks
BORDER    = "#4a2a66"

MAGENTA      = "#ff3df2"  # brand + primary controls ("PADDLE")
MAGENTA_HOT  = "#ff7af6"  # hover
MAGENTA_DEEP = "#a3169a"  # selected tab/segment (light text sits on it)
MAGENTA_SOFT = "#ff9cf8"  # hints, "your turn" prompts
MAGENTA_INK  = "#6b0a65"  # button / selected fill: white text reads at 11:1
MAGENTA_INK_HOT = "#8a0f82"

LIME      = "#b6ff3b"     # correct / go / active ("CAT")
LIME_HOT  = "#d4ff85"

TEXT      = "#f3e9ff"     # primary text
TEXT_SOFT = "#c9b8dc"     # secondary text
MUTED     = "#9a86b0"     # status / idle
DIM       = "#7a6690"     # captions
FAINT     = "#4f3d63"     # inactive indicators

WARN      = "#ffb347"     # amber
DANGER    = "#ff5a4f"     # errors + Stop (kept clear of magenta's hue)
ON_ACCENT = "#1a0726"     # text on magenta / lime / danger fills

# Fills for the big action buttons' normal (go) and running (stop) states.
# Both carry white text, so both are deep shades.
GO = MAGENTA_INK
STOP = "#9e1b2f"

HEADING_FAMILY = "Bahnschrift"   # ships with Windows 10+; DIN-like, close to the icon
MONO_FAMILY = "Consolas"


def _pair(c):
    return [c, c]   # [light, dark] -- the app is always dark


_OVERRIDES = {
    "CTk": {"fg_color": BG},
    "CTkToplevel": {"fg_color": BG},
    "CTkFrame": {"fg_color": SURFACE, "top_fg_color": SURFACE,
                 "border_color": BORDER},
    # Deep magenta fill + white text for legibility; the bright neon magenta
    # lives in the outline.
    "CTkButton": {"fg_color": MAGENTA_INK, "hover_color": MAGENTA_INK_HOT,
                  "border_color": MAGENTA, "border_width": 2,
                  "text_color": "#ffffff", "text_color_disabled": DIM},
    "CTkLabel": {"text_color": TEXT},
    "CTkEntry": {"fg_color": SURFACE_2, "border_color": BORDER,
                 "text_color": TEXT, "placeholder_text_color": DIM},
    "CTkCheckBox": {"fg_color": MAGENTA, "border_color": MAGENTA_DEEP,
                    "hover_color": MAGENTA_HOT, "checkmark_color": ON_ACCENT,
                    "text_color": TEXT, "text_color_disabled": DIM},
    "CTkSwitch": {"fg_color": RAISED, "progress_color": MAGENTA,
                  "button_color": LIME, "button_hover_color": LIME_HOT,
                  "text_color": TEXT, "text_color_disabled": DIM},
    "CTkRadioButton": {"fg_color": MAGENTA, "border_color": MAGENTA_DEEP,
                       "hover_color": MAGENTA_HOT, "text_color": TEXT,
                       "text_color_disabled": DIM},
    "CTkProgressBar": {"fg_color": SURFACE_2, "progress_color": LIME,
                       "border_color": BORDER},
    "CTkSlider": {"fg_color": RAISED, "progress_color": MAGENTA,
                  "button_color": LIME, "button_hover_color": LIME_HOT},
    "CTkOptionMenu": {"fg_color": SURFACE_2, "button_color": RAISED,
                      "button_hover_color": MAGENTA_DEEP, "text_color": TEXT,
                      "text_color_disabled": DIM},
    "CTkComboBox": {"fg_color": SURFACE_2, "border_color": BORDER,
                    "button_color": RAISED, "button_hover_color": MAGENTA_DEEP,
                    "text_color": TEXT, "text_color_disabled": DIM},
    "CTkScrollbar": {"fg_color": "transparent", "button_color": RAISED,
                     "button_hover_color": MAGENTA_DEEP},
    "CTkSegmentedButton": {"fg_color": SURFACE_2,
                           "selected_color": MAGENTA_INK,
                           "selected_hover_color": MAGENTA_INK_HOT,
                           "unselected_color": SURFACE_2,
                           "unselected_hover_color": RAISED,
                           "text_color": "#ffffff", "text_color_disabled": DIM},
    "CTkTextbox": {"fg_color": SURFACE_2, "border_color": BORDER,
                   "text_color": TEXT, "scrollbar_button_color": RAISED,
                   "scrollbar_button_hover_color": MAGENTA_DEEP},
    "CTkScrollableFrame": {"label_fg_color": SURFACE_2},
    "DropdownMenu": {"fg_color": SURFACE, "hover_color": RAISED,
                     "text_color": TEXT},
}


def apply():
    """Load the stock dark theme, then paint it PaddleCAT."""
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("blue")
    theme = ctk.ThemeManager.theme
    for widget, keys in _OVERRIDES.items():
        for key, color in keys.items():
            if isinstance(color, str) and color != "transparent":
                color = _pair(color)
            theme[widget][key] = color
