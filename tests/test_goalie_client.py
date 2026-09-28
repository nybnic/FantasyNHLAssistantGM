from clients import goalie_client

CODES = {"washington capitals": "WSH", "san jose sharks": "SJS", "montreal canadiens": "MTL", "boston bruins": "BOS"}


def _game(home, home_goalie, home_label, away, away_goalie, away_label):
    # Flat per-game shape of dailyfaceoff.com/starting-goalies (verified on 2025-26 dates).
    return {
        "homeTeamName": home, "homeGoalieName": home_goalie, "homeNewsStrengthName": home_label,
        "awayTeamName": away, "awayGoalieName": away_goalie, "awayNewsStrengthName": away_label,
    }


def test_confirmed_and_likely_starters_are_kept_unlabelled_are_not():
    entries = [
        _game("Washington Capitals", "Logan Thompson", "Confirmed", "San Jose Sharks", "Alex Nedeljkovic", "Likely"),
        _game("Montreal Canadiens", "Jacob Fowler", None, "Boston Bruins", "Jeremy Swayman", "Unconfirmed"),
    ]
    assert goalie_client.parse_entries(entries, CODES) == {
        "WSH": {"goalie_name": "Logan Thompson", "confirmed": True},
        "SJS": {"goalie_name": "Alex Nedeljkovic", "confirmed": False},
    }


def test_unknown_team_names_are_skipped():
    entries = [_game("Quebec Nordiques", "Someone", "Confirmed", "Boston Bruins", "Jeremy Swayman", "Confirmed")]
    assert goalie_client.parse_entries(entries, CODES) == {"BOS": {"goalie_name": "Jeremy Swayman", "confirmed": True}}
