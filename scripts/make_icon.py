#!/usr/bin/env python3
"""Generates icon.ico -- a graphical mark for SDRduo Display.

Renders a miniature version of the app's own panadapter: a jagged
spectrum trace over a mini waterfall using the exact colormap from
static/app.js's colormap() (navy -> blue -> teal -> yellow -> red), on
a dark rounded panel using the app's own theme (static/app.css: --bg
#0b0f14, --border #23303f, --accent #35d0ba). A small green glow-dot
signal LED sits in the corner, mirroring the S-meter/signal-good
indicator in the UI. Built the same way as the sibling Yaesu Radio
Controller / ICOMNodeDisplay / miniVNA Tiny icons (supersample +
rounded-square background + glow-dot rendering), for family visual
consistency.

Usage (run from this project's root):
    python scripts/make_icon.py

Requires: pip install pillow (build-time only, not a runtime dependency).
"""
from __future__ import annotations

import os

from PIL import Image, ImageDraw, ImageFilter

OUT_PATH = os.path.join(os.path.dirname(__file__), "..", "icon.ico")

# Supersampled render size -- downsampled to each .ico size at the end for
# anti-aliased edges (Pillow's ImageDraw has no native AA).
S = 1024

BG = (11, 15, 20, 255)          # app.css --bg
PANEL_EDGE = (35, 48, 63, 255)  # app.css --border
ACCENT = (53, 208, 186)         # app.css --accent (spectrum trace)
GREEN = (62, 207, 94)           # app.css --green (signal-good LED)

# Same stops as static/app.js's colormap(), used for the mini waterfall.
WF_STOPS = [
    (0.0, (8, 8, 40)),
    (0.35, (20, 90, 200)),
    (0.55, (40, 200, 180)),
    (0.75, (230, 210, 40)),
    (1.0, (230, 40, 40)),
]


def wf_color(t: float) -> tuple[int, int, int]:
    for i in range(1, len(WF_STOPS)):
        t0, c0 = WF_STOPS[i - 1]
        t1, c1 = WF_STOPS[i]
        if t <= t1:
            f = (t - t0) / (t1 - t0 or 1)
            return tuple(round(c0[k] + (c1[k] - c0[k]) * f) for k in range(3))
    return WF_STOPS[-1][1]


def rounded_square_bg(size: int) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    r = int(size * 0.22)
    d.rounded_rectangle([0, 0, size - 1, size - 1], radius=r, fill=BG)
    d.rounded_rectangle([0, 0, size - 1, size - 1], radius=r, outline=PANEL_EDGE,
                         width=max(2, size // 128))
    return img


def draw_spectrum_trace(img: Image.Image, x0: int, x1: int, y_base: int, y_top: int) -> None:
    """Jagged panadapter trace with a signal "bump", like a tuned-in
    carrier riding above the noise floor -- filled underneath and
    stroked on top, same visual language as the app's own spectrum
    plot."""
    # Fixed, hand-placed noise-floor jitter (fractions of the available
    # height) plus one clear bump near the middle for "tuned signal".
    xs_frac = [0.00, 0.06, 0.11, 0.16, 0.22, 0.27, 0.33, 0.38, 0.42, 0.46,
               0.50, 0.54, 0.58, 0.63, 0.69, 0.75, 0.81, 0.87, 0.93, 1.00]
    ys_frac = [0.20, 0.28, 0.16, 0.30, 0.18, 0.26, 0.14, 0.30, 0.42, 0.66,
               0.92, 0.64, 0.38, 0.24, 0.32, 0.17, 0.28, 0.20, 0.30, 0.22]

    pts = [(x0 + (x1 - x0) * xf, y_base - (y_base - y_top) * yf)
           for xf, yf in zip(xs_frac, ys_frac)]

    # Filled area under the trace, faded, so it reads as an energy plot.
    fill_layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    fd = ImageDraw.Draw(fill_layer)
    poly = [(x0, y_base)] + pts + [(x1, y_base)]
    fd.polygon(poly, fill=(*ACCENT, 60))
    img.alpha_composite(fill_layer)

    d = ImageDraw.Draw(img)
    d.line(pts, fill=ACCENT, width=max(6, S // 110), joint="curve")


def draw_waterfall(img: Image.Image, x0: int, x1: int, y0: int, y1: int) -> None:
    """A handful of horizontal bands using the app's real colormap, each
    band's bright column echoing the spectrum trace's signal bump above
    it -- reads as a compressed waterfall, not a generic gradient bar."""
    rows = 5
    row_h = (y1 - y0) / rows
    bump_centers = [0.50, 0.50, 0.48, 0.52, 0.50]  # slight drift row to row
    d = ImageDraw.Draw(img)
    cols = 48
    col_w = (x1 - x0) / cols
    for row in range(rows):
        ry0 = y0 + row * row_h
        ry1 = ry0 + row_h
        center = bump_centers[row]
        for c in range(cols):
            cf = (c + 0.5) / cols
            dist = abs(cf - center)
            level = max(0.0, 1.0 - dist * 3.2) ** 1.6
            t = min(1.0, level + 0.06)
            color = wf_color(t)
            cx0 = x0 + c * col_w
            d.rectangle([cx0, ry0, cx0 + col_w + 1, ry1], fill=color)


def draw_glow_dot(img: Image.Image, cx: int, cy: int, core_r: int,
                   color: tuple[int, int, int]) -> None:
    """Soft blurred halo + solid core + offset highlight -- same
    technique as the sibling projects' glow-dot icon marks."""
    halo = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(halo)
    halo_r = int(core_r * 2.1)
    d.ellipse([cx - halo_r, cy - halo_r, cx + halo_r, cy + halo_r], fill=(*color, 160))
    halo = halo.filter(ImageFilter.GaussianBlur(radius=core_r * 0.55))
    img.alpha_composite(halo)

    core = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(core)
    d.ellipse([cx - core_r, cy - core_r, cx + core_r, cy + core_r], fill=(*color, 255))
    img.alpha_composite(core)

    hl_r = core_r * 0.4
    hl_dx = -core_r * 0.32
    hl_dy = -core_r * 0.32
    highlight = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(highlight)
    d.ellipse([cx + hl_dx - hl_r, cy + hl_dy - hl_r, cx + hl_dx + hl_r, cy + hl_dy + hl_r],
              fill=(255, 255, 255, 130))
    highlight = highlight.filter(ImageFilter.GaussianBlur(radius=hl_r * 0.3))
    img.alpha_composite(highlight)


def build() -> None:
    img = rounded_square_bg(S)

    margin = int(S * 0.14)
    x0, x1 = margin, S - margin

    spectrum_top = int(S * 0.16)
    spectrum_base = int(S * 0.52)
    wf_top = int(S * 0.55)
    wf_bottom = int(S * 0.86)

    draw_waterfall(img, x0, x1, wf_top, wf_bottom)
    draw_spectrum_trace(img, x0, x1, spectrum_base, spectrum_top)

    # Signal-good LED, top-right corner.
    draw_glow_dot(img, S - int(S * 0.19), int(S * 0.19), int(S * 0.065), GREEN)

    sizes = [16, 24, 32, 48, 64, 128, 256]
    frames = [img.resize((s, s), Image.LANCZOS) for s in sizes]
    frames[-1].save(
        OUT_PATH, format="ICO",
        sizes=[(s, s) for s in sizes],
        append_images=frames[:-1],
    )
    print(f"Wrote {os.path.abspath(OUT_PATH)}")


if __name__ == "__main__":
    build()
