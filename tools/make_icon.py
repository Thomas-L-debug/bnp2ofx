"""Génère assets/icon.ico (plusieurs tailles) pour l'exe et l'installeur."""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "assets" / "icon.ico"
SIZES = (16, 24, 32, 48, 64, 128, 256)


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for name in ("segoeui.ttf", "SegoeUI.ttf", "arial.ttf", "Arial.ttf"):
        try:
            return ImageFont.truetype(name, size=size)
        except OSError:
            continue
    windir = Path(r"C:\Windows\Fonts")
    for name in ("segoeui.ttf", "arial.ttf", "calibri.ttf"):
        path = windir / name
        if path.exists():
            return ImageFont.truetype(str(path), size=size)
    return ImageFont.load_default()


def draw_icon(size: int) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    # Fond arrondi vert BNP-ish
    pad = max(1, size // 16)
    d.rounded_rectangle(
        [pad, pad, size - pad - 1, size - pad - 1],
        radius=size // 5,
        fill=(0, 109, 87, 255),
    )
    # Feuille
    m = size * 0.22
    x0, y0 = m, size * 0.18
    x1, y1 = size - m, size * 0.82
    d.rounded_rectangle([x0, y0, x1, y1], radius=max(2, size // 16), fill=(250, 252, 249, 255))
    # Lignes de relevé
    line_w = max(1, size // 32)
    inset = size * 0.08
    for i, frac in enumerate((0.34, 0.46, 0.58)):
        y = size * frac
        right = x1 - inset if i < 2 else x0 + (x1 - x0) * 0.62
        d.line([(x0 + inset, y), (right, y)], fill=(0, 109, 87, 220), width=line_w)
    # Pastille OFX
    if size >= 32:
        r = size * 0.20
        cx, cy = size * 0.72, size * 0.72
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(232, 168, 12, 255))
        font = _font(max(6, int(size * 0.16)))
        text = "OFX" if size >= 48 else "O"
        bbox = d.textbbox((0, 0), text, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        d.text((cx - tw / 2, cy - th / 2 - bbox[1] / 2), text, fill=(255, 255, 255, 255), font=font)
    return img


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    images = [draw_icon(s) for s in SIZES]
    images[0].save(
        OUT,
        format="ICO",
        sizes=[(s, s) for s in SIZES],
        append_images=images[1:],
    )
    print("Écrit :", OUT)


if __name__ == "__main__":
    main()
