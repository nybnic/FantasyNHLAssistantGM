import datetime as dt
import io
from zoneinfo import ZoneInfo

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


HELSINKI_NOW = dt.datetime(2026, 10, 2, 10, 30, tzinfo=ZoneInfo("Europe/Helsinki"))


def _read(monkeypatch, lines, size, now=HELSINKI_NOW):
    monkeypatch.setattr(screenshot, "_read_lines", lambda img: lines)
    buf = io.BytesIO()
    Image.new("RGB", size).save(buf, "PNG")
    return screenshot.read(buf.getvalue(), now=now)


def test_the_websites_transactions_page_reads_full_names_and_eastern_times(monkeypatch):
    # OCR lines from Nico's yahoo.com Transactions screenshot at Telegram's size (974 px wide).
    lines = [(57, 43, 27, "Transactions"), (811, 116, 17, "AllTeamsY"),
             (123, 184, 17, "VasilyPodkolzinEDM-LW,RW"), (124, 202, 17, "Free Agent"), (827, 205, 17, "Lazy Lew"),
             (796, 221, 17, "Oct2,3:21am"), (124, 223, 17, "MatthewWoodNSH-C,Rw"), (123, 241, 17, "To Waivers"),
             (122, 276, 19, "Elias Lindholm Bos-c目"), (851, 280, 18, "GWp"), (122, 294, 20, "Free Agent"),
             (797, 294, 19, "Oct2,3:04 am"),
             (123, 756, 18, "Dylan CozensoTT-c"), (726, 759, 17, "Vanilla Thunder( Mikael)"), (620, 767, 17, "Traded to"),
             (789, 776, 17, "Sep 30, 7:04 am"), (125, 777, 17, "SethJarvisCAR-LW,RWIR-NR"),
             (748, 811, 22, "Pastasauce(fMikael)"), (125, 813, 17, "KieferSherwoodsJ-LW,Rw"),
             (620, 822, 18, "Traded to"), (788, 831, 17, "Sep30,7:04am"), (124, 832, 17, "JohnTavaresTOR-c"),
             (124, 866, 20, "Jared McCann SEA-C,Lw"), (814, 867, 20, "Pastasauce"),
             (123, 885, 17, "Free Agent"), (789, 885, 17, "Sep30,7:03am")]
    shot = _read(monkeypatch, lines, (974, 900))
    assert shot["kind"] == "transactions"
    rows = shot["rows"]
    assert [(r["type"], r["when"], r["teams"]) for r in rows] == [
        ("add/drop", (10, 2, 10, 21), ["Lazy Lew"]), ("add", (10, 2, 10, 4), ["GWp"]),  # 3:21 am in New York
        ("trade", (9, 30, 14, 4), ["Pastasauce", "Vanilla Thunder"]), ("add", (9, 30, 14, 3), ["Pastasauce"])]
    assert rows[0]["players"] == [{"name": "Vasily Podkolzin", "positions": ["LW", "RW"], "action": "add"},
                                  {"name": "Matthew Wood", "positions": ["C", "RW"], "action": "drop"}]
    assert [(p["name"], p["action"]) for p in rows[2]["players"]] == [
        ("Dylan Cozens", "from:0"), ("Seth Jarvis", "from:0"), ("Kiefer Sherwood", "from:1"),
        ("John Tavares", "from:1")]


def test_a_block_cut_off_at_the_bottom_of_the_website_is_left_out(monkeypatch):
    lines = [(124, 443, 17, "JackMcBainUTA-C,LW"), (123, 461, 17, "Free Agent"), (771, 465, 16, "Nico'sGroovyTeam"),
             (790, 482, 14, "Oct1,11:48am")]  # Schenn's drop is below the screen's edge
    assert _read(monkeypatch, lines, (974, 490))["rows"] == []


def test_the_league_chat_reads_moves_dated_relative_to_when_it_was_sent(monkeypatch):
    # OCR lines from Nico's league chat screenshots at Telegram's size (590 px wide), joined.
    lines = [(61, 33, 25, "10:18N"),  # the phone's clock: taken at 10:18
             (97, 204, 23, "Yahoo Fantasy"), (287, 207, 19, "hiera18:48"),
             (96, 233, 24, "Nico's Groovy Team added Jack McBain"), (97, 262, 24, "anddroppedBraydenSchenn"),
             (147, 325, 20, "JackMcBainUTA-C,LW"), (149, 362, 24, "Brayden Schenn NYI-C,LW"),
             (97, 685, 27, "Yahoo Fantasy"), (296, 691, 19, "hiera20:18"),
             (96, 715, 26, "Nico's Groovy Team dropped Sergei"), (95, 745, 24, "Murashov"),
             (148, 807, 23, "Sergei Murashov PIT-G"),
             (97, 922, 25, "Yahoo Fantasy"), (297, 926, 23, "13mago"),
             (97, 951, 26, "Gwp added Elias Lindholm"), (147, 1015, 21, "EliasLindholmBos-C"),
             (29, 1078, 21, "Hitthe+(plus)iconto createa poll"), (108, 1176, 24, "Add a message")]
    rows = _read(monkeypatch, lines, (590, 1280))["rows"]
    assert [(r["type"], r["when"], r["teams"]) for r in rows] == [  # newest first
        ("add", (10, 2, 10, 5), ["Gwp"]), ("drop", (10, 1, 20, 18), ["Nico'sGroovyTeam"]),
        ("add/drop", (10, 1, 18, 48), ["Nico'sGroovyTeam"])]
    assert rows[2]["players"] == [{"name": "Jack McBain", "positions": ["C", "LW"], "action": "add"},
                                  {"name": "Brayden Schenn", "positions": ["C", "LW"], "action": "drop"}]
    assert rows[1]["players"][0]["name"] == "Sergei Murashov"  # the wrapped sentence, the card's name


def test_the_league_chat_reads_processed_claims_and_moves_sent_together(monkeypatch):
    lines = [(96, 185, 26, "Yahoo Fantasy"), (295, 190, 18, "avant-hiera10:49"),
             (97, 216, 22, "ViktoriosaddedEastonCowan"), (148, 279, 21, "EastonCowanToR-LW"),
             (135, 384, 21, "Transactionshavebeenprocessed"), (108, 477, 25, "2 new transactions"),
             (156, 543, 23, "Jyri(Gwp)"), (153, 574, 21, "AnthonyStolarzToR-G"),
             (97, 789, 24, "Nico's Groovy Team added Esa Lindell"), (150, 851, 21, "Esa Lindell DAL-D"),
             (424, 947, 19, "Joakim,Thomas"),
             (97, 982, 24, "Vantaa added Joey Daccord")]  # its card hidden by the poll tip: the sentence will do
    monkeypatch.setattr(screenshot, "_icon_action", lambda *a: "add")
    rows = _read(monkeypatch, lines, (590, 1280))["rows"]
    assert [(r["teams"], [p["name"] for p in r["players"]]) for r in rows] == [
        (["Vantaa"], ["Joey Daccord"]), (["Nico'sGroovyTeam"], ["Esa Lindell"]), (["Gwp"], ["Anthony Stolarz"]),
        (["Viktorios"], ["Easton Cowan"])]
    assert {r["when"] for r in rows} == {(9, 30, 10, 49)}  # sent together: the header's time for all


def test_chat_dates():
    now = dt.datetime(2026, 10, 2, 10, 18, tzinfo=ZoneInfo("Europe/Helsinki"))  # a Friday
    assert screenshot._chat_when("hier à 18:48", now) == (10, 1, 18, 48)
    assert screenshot._chat_when("avant-hier a13:16", now) == (9, 30, 13, 16)
    assert screenshot._chat_when("13m ago", now) == (10, 2, 10, 5)
    assert screenshot._chat_when("2h ago", now) == (10, 2, 8, 18)
    assert screenshot._chat_when("Yesterday at 6:48 PM", now) == (10, 1, 18, 48)
    assert screenshot._chat_when("lun. à 09:00", now) == (9, 28, 9, 0)
    assert screenshot._chat_when("Not for everyone!", now) is None


def test_player_lines_split_the_name_from_a_glued_team_code():
    assert screenshot._player_line("KieferSherwoodsJ-LW,Rw") == {
        "name": "Kiefer Sherwood", "team": "SJ", "positions": ["LW", "RW"]}
    assert screenshot._player_line("JackMcBainUTA-C,LW")["name"] == "Jack McBain"
    assert screenshot._player_line("JamieDrysdalePHl-D")["team"] == "PHI"
    assert screenshot._player_line("Jean-Gabriel PageauNYI-C")["name"] == "Jean-Gabriel Pageau"
    assert screenshot._player_line("Free Agent") is None
