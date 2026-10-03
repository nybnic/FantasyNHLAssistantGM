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
| Role blend: ice-time half-life 6 games, prior 3 games (`scripts/backtest.py --role-grid`, 2026-10-03) | Half-lives 2-9 x prior 0.5-3, scored on 2024-25 and 2025-26, next week and next 4 weeks: none beats 6 / 3 on MAE and pairwise in both seasons (differences <= 0.003 MAE, 0.2 pp). A separate PP half-life changed nothing. After a role jump (last 3 games 3+ min above the 10 before), every skater (`--all-players`), the model under-projects relative to everyone else by ~0.25-0.3 pts/game next week (2024-25: -0.19 vs +0.13 overall; 2025-26: -0.17 vs +0.07) and ~0.15 over 4 weeks, but faster settings remove that bias only by chasing noise (MAE worse). Half of it in the top-400 pool was selection on the season's outcome. So box-score ice time is too noisy to act on faster; a cleaner signal (DFO line charts, archived since 2026-10-03) is the next test |
| Age curve | Fit on 2024-25, checked on 2025-26: season-level MAE 0.628 -> 0.580, age bias mostly gone. In-season backtest 1.599 -> 1.595 (partly in-sample) |
| A goalie's track record barely predicts his points per start | Backtest pairwise 51% (a coin flip), so the game model dominates |
| Per-game variance: skater ~2.5 x xFP, goalie start ~21 | 2025-26 game logs (used by `engine/matchup.py`). They set P(win)'s spread, so also what a point is worth in win odds and the add price (`engine/addprice.py`); `MODEL_SD_SHARE` 0.08 on top is a guess |
| P(win) spread (`scripts/check_sigma.py`, 2026-10-01) | 2025-26, 20 simulated leagues, 3520 matchups projected as the bot does: error / sigma has SD 1.01 per team-week, actual margin spread 42.9 vs model 44.7 pts, calibration within ~3 pts except 80-100% (88% predicted, 83% won). Sigma is right. But a projected margin point shows up as 0.87 in results, and teams score 0.46 sigma below projection (overprojection; it cancels between two teams) |
| Injured / scratched players return slowly, and slower the longer they've been out (`engine/availability.RETURN_CURVES`) | `scripts/fit_absence.py`, regulars (14+ of the team's last 20 games): after missing 1-2 team games, P(plays) 0.32 in 1-2 days, 0.56 in 1-2 weeks, ~0.67 by 4-6 weeks; after 16+, 0.03 / 0.16 / 0.36. Fit on 2024-25; 2025-26 within 0.06 (6-15 missed at 2+ weeks: up to 0.13 higher). Replaces "DFO out = zero for the whole horizon" |
| Return curves in P(win) (`check_sigma`, 2026-10-01) | Projecting absent skaters along the curves (and healthy ones at 0.97, as the bot does) cut the mean error per team-week from -0.36 to -0.13 sigma; SD of the error 0.96, calibration within ~3 pts except 80-100% (88% predicted, 85% won). The old replay's -0.45 was partly traded players projected on their final team's schedule (fixed: -0.36). Brier isn't comparable between the two replays: their lineups, so their results, differ |
| Healthy regulars miss games too: P(plays) 0.96 in 1-2 days, 0.89 at 4-6 weeks (`fit_absence`, all 3 seasons) | The bot uses 0.97 this week and 0.97 x DFO durability in the long run. Not changed yet: open question below |
| A healthy goalie keeps his starts: 98% of them a week on, 95% two weeks on, 90% by 4-6 weeks (`availability.GOALIE_KEEP`) | `scripts/fit_absence.py --goalies`: starts k days on over his share of the last 10, relative to 3-6 days (back-to-backs and the share's drift to the mean are apart). Fit 2024-25; 2023-24 and 2025-26 within 0.04. Used in the long run, all week or nothing, so losing a goalie takes his starts (a third goalie's insurance) |
| The last result moves the next start (`availability.LAST_RESULT_ODDS`) | `scripts/fit_goalie_starts.py`, the last starter's odds of starting the team's next game (not a back-to-back) vs his blended share: after a win x1.01, a loss x0.62, a loss with 5+ GA or pulled x0.32 (fit 2023-25, n=3916); 2025-26 alone 0.96 / 0.63 / 0.35, log loss 0.651 -> 0.625. Two games on it's mixed (W 1.34, L 1.22, L bad 0.74), not modeled. Not fit on back-to-backs (too few) |
| Back-to-back repeat (`BACK_TO_BACK_REPEAT` 0.15) | Same script: the goalie who started last night starts again at 0.09 / 0.13 / 0.16 of his share (2023-24 / 24-25 / 25-26, n ~350 each, rising); 0.15 = 2024-26 pooled. Was 0.35, a guess |
| Goalie count by value (`scripts/sim_goalies.py`, 2026-10-02) | See the decision log: as good as always-3 (+0.05 +/- 0.10 wins), always-2 clearly worse. Replay assumptions: starters known on the day, an add worth 9 pts over 4 weeks, skaters healthy in projections |
| Our model vs Yahoo | 711-skater preseason export: Spearman 0.95. Free-agent D after the age fix: veterans within ~0.1-0.3 pts/game |

## Judgment calls (untested, labeled in code)
- `PROJECTION_WEIGHT = 0.5`: DFO projections aren't archived, so they can't be backtested.
- The DFO half isn't age-adjusted (the curve was fit on history only).
- Durability = DFO projected GP / (82 x 0.97). It is only used for long-run value.

## Known weaknesses / open questions
- Fringe skaters look over-projected: every skater +0.07 to +0.13 pts/game (2025-26 / 2024-25,
  `backtest --role-grid --all-players`) vs -0.06 to +0.02 for the top 400. Suspect: thin histories
  regressed up toward the position average (`PRIOR_REGRESSION_GAMES`). Free agents come from this
  pool, so add gains may be overstated. Next to test (roadmap).
- Return curves are fit on skaters; injured goalies use them too (times their start share).
  Day-to-day / game-time decisions (0.6 tonight, 0.85 tomorrow, then healthy) are a judgment
  call: DFO statuses aren't archived.
- Healthy skaters' availability falls from 0.96 to 0.89 over six weeks (`fit_absence`); the
  long run's 0.97 x durability may double count or miss part of that. Check before changing.
- Relief goalie appearances are ignored (they can't be planned).
- Yahoo projects fewer games than DFO for injury-prone players (Letang 61 vs 77).
  Yahoo exports aren't in the repo (public), so they can't be used in CI.
- Goalie start share early in the season rests on DFO's projected GS until real starts accumulate.

## Changing the model
Add a variant to `scripts/backtest.py`, compare MAE **and** pairwise on the same
player-weeks, and fit any new curve on one season before checking it on the next.
Record the result in the evidence table above.
