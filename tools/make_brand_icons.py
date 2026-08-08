#!/usr/bin/env python3
"""Render the integration's brand icons from docs/logo/icon.svg's geometry.

Why a Python renderer rather than an SVG rasteriser: this machine has no
working SVG->PNG tool (cairosvg needs a native libcairo that isn't
installed), and the earlier browser-canvas workaround was fiddly and
unverifiable. The artwork is a handful of primitives, so drawing it with
Pillow is deterministic, dependency-light and reproducible in CI.

Geometry is kept in the SVG's own 300x300 coordinate space so this file
and docs/logo/icon.svg stay readable side by side. Drawn at 4x and
downsampled with LANCZOS for antialiasing (Pillow has no native AA).

The badge is a rounded SQUARE that fills the frame, not a circle. Home
Assistant and HACS render the icon inside their own square/rounded
container, so a circular badge leaves dead corners and reads noticeably
smaller than neighbouring integrations - the AIPAI light integration's
icon fills its frame, and this now matches it.

Per home-assistant/brands: icon.png is 256x256 and icon@2x.png is
512x512, both square. A separate logo is deliberately NOT shipped - the
brands docs say that when the logo would be square, ship only the icon
and it is used as the logo fallback.

    python tools/make_brand_icons.py
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

REPO_ROOT = Path(__file__).resolve().parent.parent
BRAND_DIR = REPO_ROOT / "custom_components" / "jebao_local" / "brand"
LOGO_DIR = REPO_ROOT / "docs" / "logo"

TEAL = (11, 110, 145, 255)
WHITE = (255, 255, 255, 255)
WAVE = (255, 255, 255, 217)  # the SVG's opacity="0.85"

VIEWBOX = 300
SUPERSAMPLE = 4


def _draw(size: int) -> Image.Image:
    """Draw at `size` px using the SVG's 300-unit coordinate space."""
    s = size / VIEWBOX

    def u(v: float) -> float:
        return v * s

    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # Badge: rounded square filling the frame (22% corner radius, the
    # squircle proportion HA's own icons use).
    d.rounded_rectangle([0, 0, size - 1, size - 1], radius=u(66), fill=TEAL)

    # Inlet / outlet pipes
    d.rounded_rectangle([u(63), u(132), u(63 + 52), u(132 + 36)], radius=u(9), fill=WHITE)
    d.rounded_rectangle([u(185), u(132), u(185 + 52), u(132 + 36)], radius=u(9), fill=WHITE)

    # Pump housing + hub
    d.ellipse([u(150 - 48), u(150 - 48), u(150 + 48), u(150 + 48)], fill=WHITE)
    d.ellipse([u(150 - 17), u(150 - 17), u(150 + 17), u(150 + 17)], fill=TEAL)

    # Impeller spokes (round caps, as in the SVG)
    for (x1, y1, x2, y2) in [(150, 136, 150, 125), (163, 159, 173, 166), (137, 159, 127, 166)]:
        d.line([u(x1), u(y1), u(x2), u(y2)], fill=WHITE, width=max(1, round(u(5))))
        for (cx, cy) in ((x1, y1), (x2, y2)):
            r = u(2.5)
            d.ellipse([u(cx) - r, u(cy) - r, u(cx) + r, u(cy) + r], fill=WHITE)

    # Flow chevrons in the outlet pipe
    for x in (204, 221):
        pts = [(u(x), u(141)), (u(x + 12), u(150)), (u(x), u(159))]
        d.line(pts, fill=TEAL, width=max(1, round(u(6))), joint="curve")
        for (px, py) in pts:
            r = u(3)
            d.ellipse([px - r, py - r, px + r, py + r], fill=TEAL)

    # Water wave: the SVG's quadratic Béziers, sampled to a polyline.
    def quad(p0, p1, p2, steps=24):
        for i in range(steps + 1):
            t = i / steps
            mt = 1 - t
            yield (
                mt * mt * p0[0] + 2 * mt * t * p1[0] + t * t * p2[0],
                mt * mt * p0[1] + 2 * mt * t * p1[1] + t * t * p2[1],
            )

    segments = [
        ((95, 222), (112, 205), (130, 222)),
        ((130, 222), (148, 239), (166, 222)),
        ((166, 222), (184, 205), (202, 222)),
        ((202, 222), (220, 239), (238, 222)),
    ]
    pts: list[tuple[float, float]] = []
    for p0, p1, p2 in segments:
        pts.extend((u(x), u(y)) for x, y in quad(p0, p1, p2))
    d.line(pts, fill=WAVE, width=max(1, round(u(7))), joint="curve")
    for cap in (pts[0], pts[-1]):
        r = u(3.5)
        d.ellipse([cap[0] - r, cap[1] - r, cap[0] + r, cap[1] + r], fill=WAVE)

    return img


def render(size: int) -> Image.Image:
    big = _draw(size * SUPERSAMPLE)
    return big.resize((size, size), Image.LANCZOS)


def _font(size: int, bold: bool = True):
    """A sans face for the README banner. Falls back to Pillow's built-in
    bitmap font, which is ugly but keeps this script runnable anywhere -
    the banner is documentation, not a shipped brand asset."""
    for name in (("arialbd.ttf", "arial.ttf") if bold else ("arial.ttf",)):
        for base in (Path("C:/Windows/Fonts"), Path("/usr/share/fonts/truetype/dejavu")):
            candidate = base / name
            if candidate.is_file():
                return ImageFont.truetype(str(candidate), size)
    for name in ("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf",):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            pass
    return ImageFont.load_default()


def render_logo(width: int = 480) -> Image.Image:
    """The README banner: the same badge with the wordmark under it.

    Not shipped as a brand asset - see the module docstring on why only
    the icon goes in brand/. This just keeps the README consistent with
    the icon rather than showing the old circular badge.
    """
    ss = 2
    w = width * ss
    badge = round(w * 0.52)
    title_px, sub_px = round(w * 0.155), round(w * 0.062)
    gap, sub_gap = round(w * 0.055), round(w * 0.028)

    title, subtitle = "JEBAO", "U N O F F I C I A L"
    f_title, f_sub = _font(title_px), _font(sub_px)
    tmp = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    t_box = tmp.textbbox((0, 0), title, font=f_title)
    s_box = tmp.textbbox((0, 0), subtitle, font=f_sub)
    t_h, s_h = t_box[3] - t_box[1], s_box[3] - s_box[1]

    h = badge + gap + t_h + sub_gap + s_h + round(w * 0.03)
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    img.paste(_draw(badge * ss).resize((badge, badge), Image.LANCZOS), ((w - badge) // 2, 0))

    d = ImageDraw.Draw(img)
    y = badge + gap
    d.text((w / 2, y - t_box[1]), title, font=f_title, fill=(15, 42, 61, 255), anchor="ma")
    y += t_h + sub_gap
    d.text((w / 2, y - s_box[1]), subtitle, font=f_sub, fill=(200, 40, 40, 255), anchor="ma")

    return img.resize((width, round(h / ss)), Image.LANCZOS)


def main() -> None:
    BRAND_DIR.mkdir(parents=True, exist_ok=True)
    for name, size in (("icon.png", 256), ("icon@2x.png", 512)):
        out = BRAND_DIR / name
        render(size).save(out, "PNG", optimize=True)
        print(f"wrote {out.relative_to(REPO_ROOT)} ({size}x{size})")

    logo = LOGO_DIR / "logo.png"
    LOGO_DIR.mkdir(parents=True, exist_ok=True)
    banner = render_logo()
    banner.save(logo, "PNG", optimize=True)
    print(f"wrote {logo.relative_to(REPO_ROOT)} ({banner.width}x{banner.height})")


if __name__ == "__main__":
    main()
