#!/usr/bin/env python3
"""One-shot palette migration: neon cyber theme -> professional SOC console.

Exact-match remap for accent/status/text families; HSL de-tealing for dark
panel colors (teal-navy -> slate-navy). Alphas and layout untouched.
"""
import colorsys
import re
import sys

# ── exact (r,g,b) family mapping ─────────────────────────────────────────
EXACT = {
    # neon mint/teal accent -> teal-400 / teal-300
    (86, 247, 222): (45, 212, 191),
    (45, 255, 222): (45, 212, 191),
    (61, 252, 214): (45, 212, 191),
    (44, 241, 224): (45, 212, 191),
    (79, 255, 229): (45, 212, 191),
    (69, 245, 226): (45, 212, 191),
    (130, 255, 210): (94, 234, 212),
    (111, 255, 236): (94, 234, 212),
    (58, 157, 143): (15, 118, 110),   # #3a9d8f gradient tail -> teal-700
    (15, 233, 180): (45, 212, 191),
    (8, 228, 176): (45, 212, 191),
    # neon green -> emerald-400
    (16, 252, 160): (52, 211, 153),
    (0, 255, 136): (52, 211, 153),
    (125, 255, 203): (110, 231, 183),  # #7dffcb -> emerald-300
    # electric blue -> sky-400
    (0, 212, 255): (56, 189, 248),
    (56, 225, 255): (56, 189, 248),
    (127, 233, 255): (125, 211, 252),  # #7FE9FF -> sky-300
    (77, 163, 255): (96, 165, 250),    # #4DA3FF -> blue-400
    (94, 133, 255): (96, 165, 250),
    (156, 196, 255): (147, 197, 253),  # #9CC4FF -> blue-300
    # pink/red danger -> red-400 family
    (255, 107, 146): (248, 113, 113),
    (255, 101, 136): (248, 113, 113),
    (255, 107, 138): (248, 113, 113),  # #FF6B8A
    (255, 111, 145): (248, 113, 113),  # #FF6F91
    (255, 143, 171): (252, 165, 165),
    (255, 157, 178): (252, 165, 165),  # #FF9DB2
    (255, 45, 85): (239, 68, 68),      # #ff2d55 -> red-500
    (255, 213, 223): (254, 202, 202),  # light pinks -> red-200
    (255, 215, 225): (254, 202, 202),
    (255, 217, 228): (254, 202, 202),
    (255, 230, 238): (254, 226, 226),
    # orange / yellow -> orange-500 / amber-400
    (255, 77, 28): (249, 115, 22),
    (255, 184, 0): (251, 191, 36),
    (255, 138, 91): (251, 146, 60),    # #FF8A5B -> orange-400
    # text / muted tints -> slate scale
    (225, 244, 255): (226, 232, 240),  # text
    (223, 248, 255): (226, 232, 240),
    (138, 168, 195): (148, 163, 184),  # muted
    (126, 162, 191): (148, 163, 184),
    (198, 235, 255): (203, 213, 225),
    (159, 209, 234): (148, 163, 184),
    (205, 228, 243): (203, 213, 225),
    (183, 214, 232): (203, 213, 225),
    (212, 237, 255): (203, 213, 225),
    (216, 238, 251): (203, 213, 225),
    (167, 193, 213): (148, 163, 184),
    (127, 169, 191): (148, 163, 184),
    (200, 224, 240): (203, 213, 225),  # #C8E0F0
    (106, 136, 153): (128, 145, 168),  # #6A8899 -> slate-ish
    (58, 85, 102): (84, 99, 122),      # #3A5566 text-dim, lift slightly
    (224, 247, 255): (241, 245, 249),  # #E0F7FF near-white
    (213, 251, 255): (241, 245, 249),
    (220, 251, 255): (241, 245, 249),
    (223, 254, 255): (241, 245, 249),
    (227, 246, 255): (241, 245, 249),
    (239, 250, 255): (241, 245, 249),
    (232, 247, 255): (241, 245, 249),
    (237, 250, 255): (241, 245, 249),
    (200, 248, 243): (203, 213, 225),  # teal-tinted code text -> slate-300
    # ontology accents
    (56, 225, 255): (56, 189, 248),
    (52, 229, 163): (52, 211, 153),    # #34E5A3 -> emerald-400
    (124, 243, 200): (110, 231, 183),  # #7CF3C8 -> emerald-300
    (255, 194, 75): (251, 191, 36),    # #FFC24B -> amber-400
    (255, 217, 138): (253, 230, 138),  # #FFD98A -> amber-200
    (47, 214, 166): (52, 211, 153),    # #2FD6A6 -> emerald-400
    (244, 183, 64): (251, 191, 36),    # #F4B740 -> amber-400
    (255, 214, 0): (250, 204, 21),     # #FFD600 -> yellow-400
    (182, 156, 247): (167, 139, 250),  # #B69CF7 -> violet-400
}

TEAL_DARK_LO, TEAL_DARK_HI = 175.0, 265.0   # de-teal these dark hues -> slate
GREEN_DARK_LO = 120.0                        # keep semantic green darks emerald

def transform_dark(r, g, b):
    h, l, s = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
    hue = h * 360
    if TEAL_DARK_LO <= hue <= TEAL_DARK_HI:
        nh, ns = 220 / 360, min(s, 0.32)
    elif GREEN_DARK_LO <= hue < TEAL_DARK_LO:
        nh, ns = 160 / 360, min(s, 0.45)
    else:
        return r, g, b
    nr, ng, nb = colorsys.hls_to_rgb(nh, l, ns)
    return round(nr * 255), round(ng * 255), round(nb * 255)

def map_rgb(r, g, b):
    if (r, g, b) in EXACT:
        return EXACT[(r, g, b)]
    if max(r, g, b) < 95:
        return transform_dark(r, g, b)
    return r, g, b

RGBA_RE = re.compile(r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*(?:,\s*([0-9.]+)\s*)?\)")
HEX_RE = re.compile(r"#([0-9a-fA-F]{6})\b")

def sub_rgba(m):
    r, g, b = int(m.group(1)), int(m.group(2)), int(m.group(3))
    nr, ng, nb = map_rgb(r, g, b)
    if m.group(4) is not None:
        return f"rgba({nr}, {ng}, {nb}, {m.group(4)})"
    return f"rgb({nr}, {ng}, {nb})"

def sub_hex(m):
    hx = m.group(1)
    r, g, b = int(hx[0:2], 16), int(hx[2:4], 16), int(hx[4:6], 16)
    nr, ng, nb = map_rgb(r, g, b)
    if (nr, ng, nb) == (r, g, b):
        return m.group(0)
    return f"#{nr:02x}{ng:02x}{nb:02x}"

for path in sys.argv[1:]:
    with open(path, encoding="utf-8-sig") as f:
        src = f.read()
    out = RGBA_RE.sub(sub_rgba, src)
    out = HEX_RE.sub(sub_hex, out)
    if out != src:
        with open(path, "w", encoding="utf-8") as f:
            f.write(out)
        print(f"migrated: {path}")
    else:
        print(f"unchanged: {path}")
