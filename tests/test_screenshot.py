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


def test_a_matchup_reads_both_sides_the_slot_between_them_and_the_score(monkeypatch):
    # OCR lines as read from Nico's scrolled matchup screenshot (922 px wide).
    lines = [(300, 151, 38, "13.40 / 51.50"), (309, 202, 28, "159.79"), (445, 202, 28, "164.52"),
             (325, 740, 33, "0.00"), (532, 740, 34, "2.90"), (644, 742, 26, "O.EKMAN-LARSSON"),
             (37, 743, 27, "E. LINDELL"), (36, 777, 31, "D\ufffdDAL"), (330, 780, 34, "6.87"),
             (533, 780, 32, "10.64"), (784, 781, 24, "D\ufffdTOR"), (635, 812, 30, "Final (W)2-1 vs NYI"),
             (324, 1341, 33, "0.00"), (533, 1341, 33, "0.00"), (37, 1343, 27, "K.VEJMELKA"),
             (717, 1344, 27, "L.THOMPSON"), (443, 1376, 29, "BN"), (37, 1380, 28, "G\ufffdUTA"),
             (775, 1380, 27, "G.WSH"), (319, 1381, 33, "16.45"), (534, 1381, 32, "10.94")]
    monkeypatch.setattr(screenshot, "_read_lines", lambda img: lines)
    monkeypatch.setattr(screenshot, "_badge_colour", lambda badge: "D")
    buf = io.BytesIO()
    Image.new("RGB", (922, 2000)).save(buf, "PNG")
    shot = screenshot.read(buf.getvalue())
    assert shot["kind"] == "matchup"
    assert shot["score"] == (13.4, 51.5) and shot["projected"] == (159.79, 164.52)
    assert [r["slot"] for r in shot["rows"]] == ["D", "BN"]  # badge colour, then the badge's own text
    first = shot["rows"][0]
    assert first["mine"] == {"name": "E. LINDELL", "team": "DAL", "positions": ["D"], "points": 0.0, "projected": 6.87}
    assert first["theirs"] == {"name": "O. EKMAN-LARSSON", "team": "TOR", "positions": ["D"], "points": 2.9,
                               "projected": 10.64}
    assert shot["rows"][1]["theirs"]["name"] == "L. THOMPSON"  # "G.WSH" below it isn't a player


def test_a_team_page_is_not_a_matchup(monkeypatch):
    lines = [(358, 587, 40, "M. SCHEIFELE"), (360, 636, 40, "C\ufffdWPG")]
    monkeypatch.setattr(screenshot, "_read_lines", lambda img: lines)
    monkeypatch.setattr(screenshot, "_slot", lambda img, y, h: "C")
    buf = io.BytesIO()
    Image.new("RGB", (1179, 2556)).save(buf, "PNG")
    assert screenshot.read(buf.getvalue()) == {
        "kind": "team", "rows": [{"slot": "C", "name": "M. SCHEIFELE", "team": "WPG", "positions": ["C"]}]}


def test_a_small_coloured_letter_still_counts_at_telegram_size():
    img = Image.new("RGB", (70, 120), (20, 22, 30))
    img.paste((230, 190, 60), (30, 50, 36, 60))  # a few gold pixels: a "D" letter shrunk by Telegram
    assert screenshot._badge_colour(img) == "D"


def test_transactions_read_adds_drops_and_trades_with_their_dates(monkeypatch):
    # OCR lines as read from Nico's League > Transactions screenshot (590 px wide).
    lines = [(194, 102, 21, "LeagueTransactions"),
             (22, 191, 27, "Drop"), (404, 195, 17, "jeu.oct.110:32AM"), (22, 233, 28, "Gwp"),
             (423, 236, 21, "A. Stolarz G (W)"),
             (22, 303, 21, "Add"), (374, 305, 18, "mer.sept.3010:30PM"), (24, 346, 20, "Bottomthree"),
             (412, 347, 19, "A.Nikishin D (FA)"),
             (22, 634, 20, "Trade"), (370, 637, 16, "mer.sept.3002:04PM"), (20, 677, 21, "Pastasauce"),
             (436, 678, 18, "Vanilla Thunder"), (20, 701, 22, "Trading away"), (456, 701, 22, "Trading away"),
             (21, 726, 19, "D.Cozens C"), (395, 726, 18, "K.Sherwood LW,RW"), (464, 750, 19, "J.TavaresC"),
             (22, 816, 21, "Add/Drop"), (372, 818, 18, "mer.sept.3002:03PM"), (23, 859, 20, "Pastasauce"),
             (387, 860, 18, "J.McCannC,LW(FA)"), (387, 900, 18, "J. Faulk D"),
             (22, 1146, 22, "Add")]  # cut off by the nav bar: no date, so left out
    monkeypatch.setattr(screenshot, "_read_lines", lambda img: lines)
    monkeypatch.setattr(screenshot, "_icon_action", lambda img, y, h: "add" if y < 880 else "drop")
    buf = io.BytesIO()
    Image.new("RGB", (590, 1280)).save(buf, "PNG")
    shot = screenshot.read(buf.getvalue())
    assert shot["kind"] == "transactions"
    rows = shot["rows"]
    assert [(r["type"], r["when"], r["teams"]) for r in rows] == [
        ("drop", (10, 1, 10, 32), ["Gwp"]), ("add", (9, 30, 22, 30), ["Bottomthree"]),
        ("trade", (9, 30, 14, 4), ["Pastasauce", "Vanilla Thunder"]), ("add/drop", (9, 30, 14, 3), ["Pastasauce"])]
    assert rows[0]["players"] == [{"name": "A. Stolarz", "positions": ["G"], "action": "drop"}]
    assert [(p["name"], p["action"]) for p in rows[2]["players"]] == [
        ("D. Cozens", "from:0"), ("K. Sherwood", "from:1"), ("J. Tavares", "from:1")]
    assert [(p["name"], p["positions"], p["action"]) for p in rows[3]["players"]] == [
        ("J. McCann", ["C", "LW"], "add"), ("J. Faulk", ["D"], "drop")]
