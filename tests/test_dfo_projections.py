from clients import dfo_projections

# Shaped like the live 5v5hockey embed: JS object literals, each row repeated
# per table view, names HTML-escaped.
PAGE = """
<p>Projections updated Sept. 23, 2026</p>
<script>
const data = [
    {
    id: 'Brady Tkachuk_Panthers',
    isFavourite: favouritePlayerListData.some(e => e.name === 'Brady Tkachuk'),
    player: {'logo': 'https://x/b.webp', 'name': 'Brady Tkachuk'},
    team: {'logo': 'https://x/fla.webp', 'name': 'Panthers'},
    Pos: "F",
    alt_pos: "C,LW",
    adp: "25.1",
    GP: 78.5, G: 28.8, A: 36.7, PTS: 65.5, plus_minus: 8.6, PIM: 80.0,
    PPG: 6.3, PPA: 11.2, PPP: 17.5, SOG: 265.0, ATOI: 17.1, FOW: 156.6,
    BLK: 30.3, HIT: 227.3, VAR: 4.45,
    },
    {
    id: 'Brady Tkachuk_Panthers',
    player: {'logo': 'https://x/b.webp', 'name': 'Brady Tkachuk'},
    team: {'logo': 'https://x/fla.webp', 'name': 'Panthers'},
    Pos: "F", GP: 1.0, G: 99.0, A: 0, plus_minus: 0, PIM: 0, PPG: 0, PPA: 0,
    SOG: 0, ATOI: 1.0, FOW: 0, BLK: 0, HIT: 0,
    },
    {
    id: 'K\\'Andre Miller_Hurricanes',
    player: {'logo': 'https://x/k.webp', 'name': 'K&#x27;Andre Miller'},
    team: {'logo': 'https://x/car.webp', 'name': 'Hurricanes'},
    Pos: "D", GP: 80.0, G: 7.0, A: 25.0, plus_minus: 5.0, PIM: 30.0, PPG: 1.0,
    PPA: 4.0, SOG: 150.0, ATOI: 21.5, FOW: 0.0, BLK: 120.0, HIT: 90.0,
    },
];
const goalies = [
    {
    id: 'Jacob Fowler_Canadiens',
    player: {'logo': 'https://x/j.webp', 'name': 'Jacob Fowler'},
    team: {'logo': 'https://x/mtl.webp', 'name': 'Canadiens'},
    Pos: "G", GS: 35, W: 17, L: 14, t_o: 4, SO: 1.6, SV: 945, GA: 105, SA: 1050,
    },
];
</script>
"""


def test_parse_dedupes_and_unescapes():
    data = dfo_projections.parse(PAGE)
    assert data["updated"] == "Sept. 23, 2026"
    names = [r["name"] for r in data["skaters"]]
    assert names == ["Brady Tkachuk", "K'Andre Miller"]
    tkachuk = data["skaters"][0]
    assert tkachuk["gp"] == 78.5 and tkachuk["toi"] == 17.1
    assert tkachuk["stats"]["hit"] == 227.3 and tkachuk["stats"]["pm"] == 8.6
    goalie = data["goalies"][0]
    assert goalie["gs"] == 35 and goalie["stats"]["so"] == 1.6


def test_match_breaks_ties_by_position_and_falls_back_to_last_name(monkeypatch):
    monkeypatch.setattr(
        dfo_projections.dfo_lines, "teams",
        lambda: [{"code": "VAN", "name": "Vancouver Canucks"}, {"code": "NSH", "name": "Nashville Predators"}],
    )
    registry = [
        {"id": 1, "name": "Elias Pettersson", "team": "VAN", "position": "C"},
        {"id": 2, "name": "Elias Pettersson", "team": "VAN", "position": "D"},
        {"id": 3, "name": "Alexander Kerfoot", "team": "NSH", "position": "C"},
    ]
    rows = [
        {"name": "Elias Pettersson", "team": "Canucks", "position": "D"},
        {"name": "Alex Kerfoot", "team": "Predators", "position": "F"},
    ]
    matched = dfo_projections.match_to_nhl_ids(rows, registry)
    assert matched[2]["position"] == "D"
    assert matched[3]["name"] == "Alex Kerfoot"
    assert 1 not in matched
