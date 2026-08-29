"""
Minimal dependency-free hex-to-color-name resolver.

Used to turn Ideo4's color_palette hex arrays into a natural-language
clause ("in tones of warm cream, sage green, and burnt terracotta")
rather than leaving raw hex codes in prose output, which no natural-
language image model prompting guide recommends.

Approach: a curated palette of ~140 common/descriptive color names
mapped to RGB, matched via nearest Euclidean distance in RGB space.
This deliberately favors evocative, prompt-friendly names (e.g.
"burnt terracotta", "sage green") over a bare CSS/X11 name list, since
the output is meant to read as natural descriptive language.
"""

# name -> (r, g, b)
_NAMED_COLORS = {
    "black": (0, 0, 0),
    "charcoal": (54, 54, 54),
    "dark gray": (68, 68, 68),
    "gray": (128, 128, 128),
    "light gray": (192, 192, 192),
    "silver": (211, 211, 211),
    "white": (255, 255, 255),
    "off-white": (245, 245, 240),
    "cream": (255, 253, 208),
    "warm cream": (244, 225, 193),
    "ivory": (255, 255, 240),
    "beige": (245, 245, 220),
    "tan": (210, 180, 140),
    "khaki": (195, 176, 145),
    "sand": (194, 178, 128),
    "brown": (101, 67, 33),
    "dark brown": (74, 59, 50),
    "chocolate": (123, 63, 0),
    "chestnut": (149, 69, 53),
    "burnt terracotta": (204, 78, 92),
    "terracotta": (226, 114, 91),
    "rust": (183, 65, 14),
    "sienna": (160, 82, 45),
    "copper": (184, 115, 51),
    "bronze": (205, 127, 50),
    "gold": (212, 175, 55),
    "mustard": (225, 173, 1),
    "amber": (255, 191, 0),
    "orange": (255, 140, 0),
    "burnt orange": (191, 87, 0),
    "peach": (255, 218, 185),
    "coral": (255, 127, 80),
    "salmon": (250, 128, 114),
    "red": (200, 30, 30),
    "brick red": (170, 74, 68),
    "crimson": (150, 20, 30),
    "maroon": (100, 20, 30),
    "burgundy": (128, 0, 32),
    "wine": (114, 47, 55),
    "pink": (255, 182, 193),
    "dusty pink": (215, 174, 174),
    "rose": (204, 121, 138),
    "magenta": (216, 61, 168),
    "fuchsia": (216, 27, 168),
    "purple": (128, 0, 128),
    "plum": (142, 69, 133),
    "lavender": (200, 180, 220),
    "lilac": (200, 162, 200),
    "violet": (127, 0, 255),
    "indigo": (75, 0, 130),
    "navy": (0, 30, 84),
    "midnight blue": (25, 25, 60),
    "blue": (30, 80, 200),
    "cornflower blue": (100, 149, 237),
    "sky blue": (135, 206, 235),
    "powder blue": (176, 224, 230),
    "steel blue": (70, 130, 180),
    "denim blue": (60, 100, 150),
    "teal": (0, 128, 128),
    "turquoise": (64, 191, 191),
    "cyan": (0, 200, 200),
    "aqua": (0, 220, 220),
    "seafoam": (159, 226, 191),
    "mint": (170, 240, 209),
    "sage green": (158, 174, 143),
    "moss green": (100, 120, 62),
    "olive": (110, 110, 40),
    "forest green": (34, 90, 34),
    "emerald": (0, 150, 90),
    "green": (40, 160, 60),
    "lime": (170, 220, 40),
    "chartreuse": (170, 220, 40),
    "yellow": (240, 220, 40),
    "pale yellow": (250, 240, 170),
    "warm yellow": (240, 200, 60),
}

_NAMES = list(_NAMED_COLORS.keys())
_RGBS = list(_NAMED_COLORS.values())


def _hex_to_rgb(hex_str: str):
    h = hex_str.strip().lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    if len(h) != 6:
        return None
    try:
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return None


def hex_to_color_name(hex_str: str) -> str:
    """Return the nearest named color for a hex string, or the hex itself
    unchanged if it can't be parsed (fail soft, never raise)."""
    rgb = _hex_to_rgb(hex_str)
    if rgb is None:
        return hex_str

    best_name = None
    best_dist = None
    for name, candidate_rgb in zip(_NAMES, _RGBS):
        dist = sum((a - b) ** 2 for a, b in zip(rgb, candidate_rgb))
        if best_dist is None or dist < best_dist:
            best_dist = dist
            best_name = name

    return best_name


def hex_list_to_names(hex_list) -> list:
    """Convert a list of hex strings to color names, deduplicating
    consecutive/identical name matches (e.g. two close hexes both
    resolving to "sage green" shouldn't repeat in the output clause)."""
    names = []
    for h in hex_list or []:
        name = hex_to_color_name(h)
        if name not in names:
            names.append(name)
    return names
