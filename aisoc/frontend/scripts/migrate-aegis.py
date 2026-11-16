#!/usr/bin/env python3
"""Align AISOC palette with the aegis design system (aegis-night theme).

teal accent -> aegis cyan, semantic -400 -> aegis -300 text hues,
navy panels darkened toward aegis near-black surfaces.
"""
import colorsys
import re
import sys

EXACT = {
    # teal accent family -> aegis cyan
    (45, 212, 191): (34, 211, 238),    # #2dd4bf -> #22d3ee
    (94, 234, 212): (103, 232, 249),   # #5eead4 -> #67e8f9 (focus)
    (153, 246, 228): (165, 243, 252),  # teal-200 -> cyan-200
    (15, 118, 110): (14, 116, 144),    # teal-700 -> cyan-700
    # semantic -400 -> aegis -300 text hues
    (52, 211, 153): (110, 231, 183),   # emerald-400 -> #6ee7b7
    (110, 231, 183): (110, 231, 183),
    (251, 191, 36): (252, 211, 77),    # amber-400 -> #fcd34d
    (248, 113, 113): (253, 164, 175),  # red-400 -> rose-300 #fda4af
    (252, 165, 165): (253, 164, 175),
    # sky data accent -> aegis accent for primary chart color handled per-file
}

def scale_dark_navy(r, g, b):
    """Darken blue-ish dark panels toward aegis near-black surfaces."""
    h, l, s = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
    hue = h * 360
    if not (190 <= hue <= 250):
        return r, g, b
    nr, ng, nb = colorsys.hls_to_rgb(h, l * 0.55, s)
    return round(nr * 255), round(ng * 255), round(nb * 255)

def map_rgb(r, g, b):
    if (r, g, b) in EXACT:
        return EXACT[(r, g, b)]
    if max(r, g, b) < 95:
        return scale_dark_navy(r, g, b)
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
    # font alignment with aegis
    out = out.replace('"IBM Plex Sans"', '"Inter"').replace("'IBM Plex Sans'", "'Inter'")
    out = out.replace('"IBM Plex Mono"', '"JetBrains Mono"').replace("'IBM Plex Mono'", "'JetBrains Mono'")
    if out != src:
        with open(path, "w", encoding="utf-8") as f:
            f.write(out)
        print(f"migrated: {path}")
    else:
        print(f"unchanged: {path}")
