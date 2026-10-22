"""The dashboard's and the cards' colours (site/index.html) read on a phone:
text against its background at WCAG's 4.5:1 or more, large figures 3:1."""
import re
from pathlib import Path

PAGE = Path("site/index.html").read_text(encoding="utf-8")


def _tokens() -> dict[str, str]:
    root = re.search(r":root \{(.*?)\}", PAGE, re.S).group(1)
    return dict(re.findall(r"--([\w-]+): (#[0-9A-Fa-f]{6})", root))


def _luminance(hex_: str) -> float:
    def channel(c: int) -> float:
        c /= 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (int(hex_[i:i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def contrast(fg: str, bg: str) -> float:
    a, b = sorted((_luminance(fg), _luminance(bg)), reverse=True)
    return (a + 0.05) / (b + 0.05)


def test_the_palettes_text_reads_on_every_background():
    t = _tokens()
    backgrounds = {"the page": t["surface"], "a card": t["card"], "today's move": "#182A47"}
    text = {"text": t["text"], "secondary text": t["text-2"], "your figures": "#7DB8FF", "their figures": "#FFA57D",
            "the plan's figures": t["aqua"], "drop and warning text": "#FFA57D"}
    for bg_name, bg in backgrounds.items():
        for name, fg in text.items():
            assert contrast(fg, bg) >= 4.5, f"{name} {fg} on {bg_name} {bg}: {contrast(fg, bg):.2f}"
    assert contrast(t["on-blue"], t["blue"]) >= 4.5  # a chosen tab's label
    assert contrast("#0B1422", "#2CC28B") >= 4.5  # the WIN badge


def test_large_figures_and_marks_stand_out():
    t = _tokens()  # the score, the ring and the bars: 3:1 against the card is enough for large type and marks
    for name in ("blue", "orange", "aqua"):
        assert contrast(t[name], t["card"]) >= 3, name
    assert contrast("#FFB08C", t["surface"]) >= 3  # their score on the card's ring row


def test_the_page_declares_a_home_screen_app():
    assert '<link rel="manifest" href="manifest.webmanifest">' in PAGE and 'rel="apple-touch-icon"' in PAGE
    for name in ("manifest.webmanifest", "icon-180.png", "icon-192.png", "icon-512.png"):
        assert (Path("site") / name).exists(), name
