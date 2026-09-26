"""Blend the in-house model with published projections into one consensus line.

Sources, each counted as one equal opinion where it has a view:
- model:  draft.projections (history-based, every stat this league scores)
- Kodo:   full skater lines (no PIM/GWG, PP/SH as combined points) and full
          goalie lines (GS, W, SO, SV%, SV)
- NHL.com fantasy staff: skater point totals, goalie win totals

Skaters: per-game rates of model and Kodo are averaged stat by stat and
multiplied by the averaged games played; then goals and assists (and the
PP/SH/GWG splits riding on them) are scaled so G+A lands on the average of
that blend and NHL.com's point total, a third opinion on scoring.

Goalies: projected starts average three estimates - the model's (hand set
in data/draft_overrides.csv), Kodo's, and the starts implied by NHL.com's
win total at the blended win rate. Per-start rates (win %, shots against,
save %, shutouts) average model and Kodo.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from statistics import mean

from draft import scoring
from draft.projections import SEASON_GAMES, SKATER_STATS, Projection

MAX_GOALIE_STARTS = 68.0


@dataclass
class SourceView:
    """What each source says about one player, for display on the board."""
    model_fpts: float
    kodo_fpts: float | None = None
    nhl_points: float | None = None
    nhl_wins: float | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def spread(self) -> float:
        """Relative disagreement between model and Kodo fantasy points."""
        if self.kodo_fpts is None or max(self.model_fpts, self.kodo_fpts) <= 0:
            return 0.0
        return abs(self.model_fpts - self.kodo_fpts) / max(self.model_fpts, self.kodo_fpts)


def _split(total: float, model_part: float, model_other: float, default_share: float) -> tuple[float, float]:
    """Split a combined count (e.g. Kodo PPP) using the model's goal share."""
    both = model_part + model_other
    share = model_part / both if both > 0 else default_share
    return total * share, total * (1 - share)


def kodo_skater_line(kodo: dict, model: Projection) -> tuple[dict[str, float], float]:
    """Kodo's line in this league's stat keys; stats Kodo lacks come from the
    model's per-game rates."""
    games = float(kodo.get("GP") or 0) or model.games
    per_game = {k: model.stats[k] / model.games if model.games else 0.0 for k in SKATER_STATS}
    ppg, ppa = _split(float(kodo.get("PPP") or 0), model.stats["ppg"], model.stats["ppa"], 0.35)
    shg, sha = _split(float(kodo.get("SHP") or 0), model.stats["shg"], model.stats["sha"], 0.45)
    goals = float(kodo.get("G") or 0)
    model_gwg_share = model.stats["gwg"] / model.stats["g"] if model.stats["g"] > 0 else 0.15
    line = {
        "g": goals,
        "a": float(kodo.get("A") or 0),
        "pm": float(kodo.get("+/-") or 0) * 0.5,  # same regression the model applies
        "pim": per_game["pim"] * games,
        "ppg": ppg,
        "ppa": ppa,
        "shg": shg,
        "sha": sha,
        "gwg": goals * model_gwg_share,
        "sog": float(kodo.get("SOG") or 0),
        "fow": float(kodo.get("FOW") or 0),
        "hit": float(kodo.get("HIT") or 0),
        "blk": float(kodo.get("BLK") or 0),
    }
    return line, games


def blend_skater(p: Projection, kodo: dict | None, nhl_points: float | None) -> SourceView:
    view = SourceView(model_fpts=p.fpts, nhl_points=nhl_points)
    if kodo:
        k_line, k_games = kodo_skater_line(kodo, p)
        view.kodo_fpts = scoring.skater_points(k_line)
        games = mean([p.games, k_games])
        stats = {}
        for k in SKATER_STATS:
            m_rate = p.stats[k] / p.games if p.games else 0.0
            k_rate = k_line[k] / k_games if k_games else 0.0
            stats[k] = mean([m_rate, k_rate]) * games
        p.stats, p.games = stats, games

    if nhl_points is not None:
        blended = p.stats["g"] + p.stats["a"]
        n_sources = 2 if kodo else 1
        target = (blended * n_sources + nhl_points) / (n_sources + 1)
        if blended > 0:
            factor = target / blended
            for k in ("g", "a", "ppg", "ppa", "shg", "sha", "gwg"):
                p.stats[k] *= factor

    p.fpts = scoring.skater_points(p.stats)
    return view


def blend_goalie(p: Projection, kodo: dict | None, nhl_wins: float | None) -> SourceView:
    view = SourceView(model_fpts=p.fpts, nhl_wins=nhl_wins)
    gs = p.games or 1.0
    m_sa = (p.stats["sv"] + p.stats["ga"]) / gs
    rates = {"w": [p.stats["w"] / gs], "sa": [m_sa], "sv_pct": [p.stats["sv_pct"]], "so": [p.stats["so"] / gs]}
    starts = [p.games]

    if kodo and kodo.get("GS"):
        k_gs = float(kodo["GS"])
        k_svp = float(kodo.get("SV%") or p.stats["sv_pct"])
        k_sa = float(kodo.get("SV") or 0) / k_svp / k_gs if k_svp else m_sa
        rates["w"].append(float(kodo.get("W") or 0) / k_gs)
        rates["sa"].append(k_sa)
        rates["sv_pct"].append(k_svp)
        rates["so"].append(float(kodo.get("SHO") or 0) / k_gs)
        starts.append(k_gs)
        view.kodo_fpts = scoring.goalie_points({
            "gs": k_gs, "w": float(kodo.get("W") or 0), "sv": float(kodo.get("SV") or 0),
            "ga": k_gs * k_sa * (1 - k_svp), "so": float(kodo.get("SHO") or 0),
        })

    win_rate = mean(rates["w"])
    if nhl_wins is not None and win_rate > 0:
        starts.append(min(nhl_wins / win_rate, MAX_GOALIE_STARTS))

    games = min(mean(starts), MAX_GOALIE_STARTS)
    sa, svp = mean(rates["sa"]), mean(rates["sv_pct"])
    p.games = games
    p.stats = {
        "gs": games,
        "w": win_rate * games,
        "sv": sa * svp * games,
        "ga": sa * (1 - svp) * games,
        "so": mean(rates["so"]) * games,
        "sv_pct": svp,
    }
    p.fpts = scoring.goalie_points(p.stats)
    return view


def cap_team_starts(goalies: list[Projection], team_games: float = SEASON_GAMES + 4) -> None:
    """Scale a team's goalies down if their starts add up to more than a
    season (+4 slack for spot starts/relief); blending independent sources
    can over-allocate a crease."""
    by_team: dict[str, list[Projection]] = {}
    for g in goalies:
        by_team.setdefault(g.team, []).append(g)
    for team_goalies in by_team.values():
        total = sum(g.games for g in team_goalies)
        if total > team_games:
            factor = team_games / total
            for g in team_goalies:
                for k in ("gs", "w", "sv", "ga", "so"):
                    g.stats[k] *= factor
                g.games *= factor
                g.fpts = scoring.goalie_points(g.stats)
