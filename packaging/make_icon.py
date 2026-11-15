"""packaging/make_icon.py  Draws the Zephyr app icon (packaging/Zephyr.icns). Owner: Swayam.

The mark is a Z written in the air, flipped upside down: one stroke that starts
faint at the bottom left and brightens to the website's pale aqua toward the
fingertip at the top right.
The menu bar draws the same stroke in zephyr/ui/app.py, and the website uses it too.

    python packaging/make_icon.py              writes packaging/Zephyr.icns
    python packaging/make_icon.py out.png      writes a 1024 px PNG instead
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

# the stroke, in a unit square (y down): along the bottom, up the diagonal, along the top
CORNERS = ((0.17, 0.77), (0.75, 0.77), (0.19, 0.27), (0.77, 0.27))
CORNER_RADIUS = 0.11
WIDTH = (0.026, 0.046)          # half-width at the start and at the fingertip
DOT = 0.072                     # fingertip radius
# colours: a dark body, the stroke in the website's pale aqua, a white fingertip
PALETTES = {
    "night": {"body": ((26, 36, 40), (9, 12, 15)), "ink": ((63, 143, 147), (191, 229, 230)),
              "tip": (244, 251, 251), "halo": (191, 229, 230, 120), "glow": 0.45},
}
PALETTE = "night"


def stroke_points(n: int = 600) -> list[tuple[float, float]]:
    """The stroke as n points evenly spaced along it, rounding each corner."""
    def toward(p, q, k):
        d = math.dist(p, q)
        return p[0] + (q[0] - p[0]) * k / d, p[1] + (q[1] - p[1]) * k / d

    def bend(a, b, c):
        return [((1 - t) ** 2 * a[0] + 2 * (1 - t) * t * b[0] + t * t * c[0],
                 (1 - t) ** 2 * a[1] + 2 * (1 - t) * t * b[1] + t * t * c[1]) for t in (i / 40 for i in range(41))]

    a, b, c, d = CORNERS
    pts = [a] + bend(toward(b, a, CORNER_RADIUS), b, toward(b, c, CORNER_RADIUS)) \
              + bend(toward(c, b, CORNER_RADIUS), c, toward(c, d, CORNER_RADIUS)) + [d]
    lengths = [0.0]
    for p, q in zip(pts, pts[1:]):
        lengths.append(lengths[-1] + math.dist(p, q))
    out, j = [], 0
    for i in range(n):
        s = lengths[-1] * i / (n - 1)
        while j < len(lengths) - 2 and lengths[j + 1] < s:
            j += 1
        t = (s - lengths[j]) / max(lengths[j + 1] - lengths[j], 1e-9)
        p, q = pts[j], pts[j + 1]
        out.append((p[0] + (q[0] - p[0]) * t, p[1] + (q[1] - p[1]) * t))
    return out


def mix(c0, c1, t):
    return tuple(int(c0[i] + (c1[i] - c0[i]) * t) for i in range(3))


def draw_stroke(size: int, pal: dict) -> Image.Image:
    """The mark alone on a transparent square. Later points draw over earlier ones, so the
    brighter end of the stroke stays on top where it overlaps."""
    layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    for i, (x, y) in enumerate(pts := stroke_points()):
        t = i / (len(pts) - 1)
        r = (WIDTH[0] + (WIDTH[1] - WIDTH[0]) * t) * size
        alpha = int(255 * (0.42 + 0.58 * t ** 0.8))
        d.ellipse((x * size - r, y * size - r, x * size + r, y * size + r), fill=mix(*pal["ink"], t ** 0.9) + (alpha,))
    x, y = pts[-1]
    r = DOT * size
    d.ellipse((x * size - r, y * size - r, x * size + r, y * size + r), fill=pal["tip"] + (255,))
    return layer


def render(out: int = 1024, palette: str = PALETTE) -> Image.Image:
    pal = PALETTES[palette]
    S = 2048                                    # draw at 2x, then downscale for smooth edges
    k = S / 1024
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))

    # macOS icon grid: an 824 px body centred in 1024, with a soft shadow
    x0, y0, x1, y1 = (int(v * k) for v in (100, 100, 924, 924))
    radius = int(185 * k)
    shadow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle((x0, y0 + int(14 * k), x1, y1 + int(14 * k)), radius, fill=(0, 0, 0, 90))
    img.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(int(22 * k))))
    column = Image.new("RGBA", (1, S))
    for y in range(S):
        column.putpixel((0, y), mix(*pal["body"], y / (S - 1)) + (255,))
    body_mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(body_mask).rounded_rectangle((x0, y0, x1, y1), radius, fill=255)
    img.paste(column.resize((S, S)), (0, 0), body_mask)

    # the mark, with a soft glow and extra light around the fingertip
    box = int(620 * k)
    mark = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    mark.paste(draw_stroke(box, pal), ((S - box) // 2, (S - box) // 2))
    glow = mark.copy()
    glow.putalpha(glow.getchannel("A").point(lambda a: int(a * pal["glow"])))
    glow = glow.filter(ImageFilter.GaussianBlur(int(26 * k)))
    tx, ty = stroke_points()[-1]
    cx, cy = (S - box) // 2 + tx * box, (S - box) // 2 + ty * box
    halo = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    hr = 150 * k
    ImageDraw.Draw(halo).ellipse((cx - hr, cy - hr, cx + hr, cy + hr), fill=pal["halo"])
    glow.alpha_composite(halo.filter(ImageFilter.GaussianBlur(int(60 * k))))
    clipped = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    clipped.paste(glow, (0, 0), body_mask)      # keep the light inside the body
    img.alpha_composite(clipped)
    img.alpha_composite(mark)
    return img.resize((out, out), Image.LANCZOS)


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).with_name("Zephyr.icns")
    render().save(target)
    print(f"wrote {target}")
