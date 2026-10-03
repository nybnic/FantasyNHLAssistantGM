"""Everything the decision engines need from the model, built once per run:
skater priors (last three seasons + DFO projections) and this season's
games, goalie save %, team ratings, and each team's recent starting goalies.
"""
from __future__ import annotations

import datetime as dt
from collections import defaultdict
from dataclasses import dataclass, field

from clients import dfo_projections, nhl_client, nhl_stats
from clients.nhl_stats import GoalieGame, SkaterGame
from engine import availability
from model import games as games_model
from model.projections import SkaterPrior, SkaterProjection, project_skater, season_skater_priors

GAMES_PER_SEASON = 82
HEALTHY_SHARE = 0.97  # a healthy regular still misses a game or two


def current_season_id(today: dt.date) -> int:
    first_year = today.year if today.month >= 7 else today.year - 1
    return first_year * 10_000 + first_year + 1


@dataclass
class ModelContext:
    today: dt.date
    season: int
    skater_priors: dict[int, SkaterPrior]
    skater_fallback: dict[str, SkaterPrior]
    skater_games: dict[int, list[SkaterGame]]
    goalie_history: dict[int, list[GoalieGame]]
    goalie_games: dict[int, list[GoalieGame]]
    team_starts: dict[str, list[tuple[dt.date, int]]]  # team -> [(date, starter id)], oldest first
    team_ratings: dict[str, games_model.TeamRating]
    league: games_model.League
    projected_starts: dict[int, float] = field(default_factory=dict)  # DFO season GS
    projected_gp: dict[int, float] = field(default_factory=dict)  # DFO season GP, skaters
    last_results: dict[str, str] = field(default_factory=dict)  # team -> its last start's availability.start_result

    def skater(self, player_id: int, position: str) -> SkaterProjection:
        group = "D" if position == "D" else "F"
        prior = self.skater_priors.get(player_id) or self.skater_fallback[group]
        return project_skater(prior, [g for g in self.skater_games.get(player_id, []) if g.date < self.today])

    def goalie_start(self, player_id: int, team: str, opponent: str, home: bool) -> dict[str, float]:
        past = [g for g in self.goalie_games.get(player_id, []) if g.date < self.today]
        sv = games_model.save_pct(self.goalie_history.get(player_id, []), past, self.league.save_pct)
        return games_model.goalie_start(team, opponent, home, sv, self.team_ratings, self.league)

    def durability(self, player_id: int) -> float:
        """Share of a healthy player's games he's projected to play (injury
        history, age); 1 when there's no projection."""
        gp = self.projected_gp.get(player_id)
        return 1.0 if gp is None else min(gp / (GAMES_PER_SEASON * HEALTHY_SHARE), 1.0)

    def games_missed(self, player_id: int, team: str) -> int:
        """Team games in a row he has missed, up to today (how slowly an
        absent player comes back depends on it: availability.RETURN_CURVES).
        A team's games are the dates it had a starting goalie."""
        played = {g.date for g in self.skater_games.get(player_id, []) if g.date < self.today}
        missed = 0
        for date, _ in reversed(self.team_starts.get(team, [])):
            if date in played:
                break
            missed += 1
        return missed

    def prior_start_share(self, player_id: int) -> float | None:
        gs = self.projected_starts.get(player_id)
        return None if gs is None else min(gs / GAMES_PER_SEASON, 0.85)


def skater_ages(registry: list[dict], past_ids: list[int], season: int) -> dict[int, float]:
    """Age on Oct 1 of `season`, for everyone with a known birth date."""
    born: dict[int, dt.date] = {}
    for season_id in past_ids:
        born.update(nhl_stats.skater_birth_dates(season_id))
    for p in registry:
        if p.get("birth_date"):
            born.setdefault(p["id"], dt.date.fromisoformat(p["birth_date"]))
    start = dt.date(season // 10_000, 10, 1)
    return {pid: (start - day).days / 365.25 for pid, day in born.items()}


def build(today: dt.date) -> ModelContext:
    season = current_season_id(today)
    past_ids = [season - 10_001 * i for i in (1, 2, 3)]
    past_skaters = [nhl_stats.skater_games(s) for s in past_ids]

    registry = nhl_client.current_rosters() + [
        {"id": g.player_id, "name": g.name, "team": g.team, "position": g.position} for g in past_skaters[0]
    ]
    dfo = dfo_projections.fetch()
    skater_rows = dfo_projections.match_to_nhl_ids(dfo["skaters"], registry)
    priors, fallback = season_skater_priors(past_skaters, skater_rows, ages=skater_ages(registry, past_ids, season))

    skater_games: dict[int, list[SkaterGame]] = defaultdict(list)
    for g in nhl_stats.skater_games(season, today=today):
        skater_games[g.player_id].append(g)

    past_goalies = [nhl_stats.goalie_games(s) for s in past_ids[:2]]
    goalie_history: dict[int, list[GoalieGame]] = defaultdict(list)
    for season_games in past_goalies:
        for g in season_games:
            goalie_history[g.player_id].append(g)
    this_goalies = nhl_stats.goalie_games(season, today=today)
    goalie_games: dict[int, list[GoalieGame]] = defaultdict(list)
    team_starts: dict[str, list[tuple[dt.date, int]]] = defaultdict(list)
    appearances: dict[tuple[str, int], int] = defaultdict(int)
    last_start: dict[str, GoalieGame] = {}
    for g in this_goalies:
        goalie_games[g.player_id].append(g)
        if g.date < today:
            appearances[(g.team, g.game_id)] += 1
            if g.started:
                team_starts[g.team].append((g.date, g.player_id))
                last_start[g.team] = g  # games come sorted by date
    last_results = {team: availability.start_result(g, appearances[(team, g.game_id)] > 1)
                    for team, g in last_start.items()}

    last = past_goalies[0]
    league_sv = sum(g.stats["sv"] for g in last) / sum(g.shots_against for g in last)
    played = [g for g in this_goalies if g.date < today]
    team_ratings, league = games_model.ratings(games_model.team_games(last), games_model.team_games(played), league_sv)

    goalie_registry = registry + [
        {"id": g.player_id, "name": g.name, "team": g.team, "position": "G"} for g in past_goalies[0]
    ]
    goalie_rows = dfo_projections.match_to_nhl_ids(dfo["goalies"], goalie_registry)

    return ModelContext(
        today=today,
        season=season,
        skater_priors=priors,
        skater_fallback=fallback,
        skater_games=skater_games,
        goalie_history=goalie_history,
        goalie_games=goalie_games,
        team_starts=team_starts,
        team_ratings=team_ratings,
        league=league,
        projected_starts={pid: row["gs"] for pid, row in goalie_rows.items()},
        projected_gp={pid: row["gp"] for pid, row in skater_rows.items()},
        last_results=last_results,
    )
