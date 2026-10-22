"""The dashboard's home-screen icons (site/icon-*.png): the Telegram card's win-odds ring,
blue and orange on the night background. Drawn large and scaled down for smooth edges.

    python -m scripts.make_icons
"""
from pathlib import Path

from PIL import Image, ImageDraw

NIGHT, CARD, BLUE, ORANGE, WHITE = "#0B1422", "#13213A", "#4D9BF0", "#F07A45", "#FFFFFF"
SIZES = {"icon-180.png": 180, "icon-192.png": 192, "icon-512.png": 512}  # iOS's touch icon, Android's two


def icon(size: int, scale: int = 4) -> Image.Image:
    n = size * scale
    img = Image.new("RGB", (n, n), NIGHT)  # square: iOS and Android round the corners themselves
    d = ImageDraw.Draw(img)
    pad, width = n * 0.2, n * 0.11
    box = (pad, pad, n - pad, n - pad)
    d.ellipse((pad - width * 0.6, pad - width * 0.6, n - pad + width * 0.6, n - pad + width * 0.6), fill=CARD)
    # A near-even week, as the card draws it: you in blue clockwise from the top, them in orange.
    d.arc(box, start=-90, end=-90 + 0.48 * 360, fill=BLUE, width=round(width))
    d.arc(box, start=-90 + 0.48 * 360, end=270, fill=ORANGE, width=round(width))
    d.line((n / 2, n - pad - width, n / 2, n - pad), fill=WHITE, width=round(width * 0.22))  # 50%, across the ring
    return img.resize((size, size), Image.LANCZOS)


def main(folder: Path = Path("site")) -> None:
    for name, size in SIZES.items():
        icon(size).save(folder / name, optimize=True)
        print(folder / name)


if __name__ == "__main__":
    main()
