# model/: projections

Everything is in one currency: **expected fantasy points (xFP)**, per game for
skaters and per start for goalies. `context.build(date)` loads all data once per
run. `ModelContext.skater()` and `.goalie_start()` are what the engines call.

## How a skater is projected (`projections.py`)
1. **History prior:** the last 3 seasons (weights 1 / 0.6 / 0.35), as per-TOI
   rates, regressed toward the position average (20 games).
2. **Aged:** rates are scaled by `AGE_CURVES` (F peak 26, D peak 23.5).
3. **Blended 50/50 with DailyFaceoff's preseason projection**
   (`PROJECTION_WEIGHT`). A player with no NHL history takes DFO whole.
4. **In-season:** Bayesian update per stat (`SKATER_K` = games of prior evidence)
   times role = recency-weighted TOI (half-life 6 games).

Goalies: per start, the matchup model in `games.py` (team strength, opponent,
home ice, the goalie's regressed save %). A start matters about 10x more than the
matchup, so start odds live in `engine/availability.py`.

## Evidence (keep this table current)
| Claim | Evidence |
|---|---|
| Skater model beats naive baselines | Backtest 2025-26, 7,910 player-weeks: MAE 1.595 vs 1.669 season-to-date, 1.797 last-10; pairwise 63.9% |
| `SKATER_K` doubled | Backtest: flat optimum at 2-3x the original |
| Age curve | Fit on 2024-25, checked on 2025-26: season-level MAE 0.628 -> 0.580, age bias mostly gone. In-season backtest 1.599 -> 1.595 (partly in-sample) |
| A goalie's track record barely predicts his points per start | Backtest pairwise 51% (a coin flip), so the game model dominates |
| Per-game variance: skater ~2.5 x xFP, goalie start ~21 | 2025-26 game logs (used by `engine/matchup.py`) |
| Our model vs Yahoo | 711-skater preseason export: Spearman 0.95. Free-agent D after the age fix: veterans within ~0.1-0.3 pts/game |

## Judgment calls (untested, labeled in code)
- `PROJECTION_WEIGHT = 0.5`: DFO projections aren't archived, so they can't be backtested.
- The DFO half isn't age-adjusted (the curve was fit on history only).
- Durability = DFO projected GP / (82 x 0.97). It is only used for long-run value.

## Known weaknesses / open questions
- No injury-return model: DFO "out" means zero for the whole horizon.
- Relief goalie appearances are ignored (they can't be planned).
- Yahoo projects fewer games than DFO for injury-prone players (Letang 61 vs 77).
  Yahoo exports aren't in the repo (public), so they can't be used in CI.
- Goalie start share early in the season rests on DFO's projected GS until real starts accumulate.

## Changing the model
Add a variant to `scripts/backtest.py`, compare MAE **and** pairwise on the same
player-weeks, and fit any new curve on one season before checking it on the next.
Record the result in the evidence table above.
