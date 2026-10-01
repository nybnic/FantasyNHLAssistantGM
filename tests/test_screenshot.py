import io

from PIL import Image

from clients import screenshot


def _badge(colour):
    img = Image.new("RGB", (140, 160), (20, 22, 30))  # the app's dark background
    img.paste(colour, (40, 60, 100, 100))
    return img


def test_badge_colours_tell_c_d_g_apart():
    assert screenshot._badge_colour(_badge((90, 170, 250))) == "C"
    assert screenshot._badge_colour(_badge((230, 190, 60))) == "D"
    assert screenshot._badge_colour(_badge((190, 225, 60))) == "G"
    assert screenshot._badge_colour(_badge((80, 210, 170))) == "TEAL"
    assert screenshot._badge_colour(_badge((150, 150, 160))) is None  # BN / IR+: grey


def test_rows_pair_each_name_with_the_line_below(monkeypatch):
    lines = [(30, 480, 40, "Forwards/Defensemen"),
             (358, 587, 40, "M. SCHEIFELE"), (360, 636, 40, "C\ufffdWPG"), (359, 678, 40, "No Game"),
             (359, 767, 40, "B. SCHENN 目"), (360, 814, 40, "Final L 1-2 @ TOR"),
             (357, 1125, 40, "A. TUCH"), (360, 1176, 40, "LW,RW . WSH")]
    monkeypatch.setattr(screenshot, "_read_lines", lambda img: lines)
    monkeypatch.setattr(screenshot, "_slot", lambda img, y, h: "C")
    image = Image.new("RGB", (1179, 2556))
    buf = io.BytesIO()
    image.save(buf, "PNG")
    rows = screenshot.read_roster(buf.getvalue())
    assert rows == [
        {"slot": "C", "name": "M. SCHEIFELE", "team": "WPG", "positions": ["C"]},
        {"slot": "C", "name": "B. SCHENN", "team": "", "positions": []},
        {"slot": "C", "name": "A. TUCH", "team": "WSH", "positions": ["LW", "RW"]},
    ]
