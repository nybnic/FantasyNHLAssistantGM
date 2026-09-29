# engine/: decisions

| File | Decides |
|---|---|
| `lineup.py` | The best slot assignment for one day. Exact DP; a move must gain `KEEP_SLOT_BONUS` |
| `availability.py` | Who plays: skater injury/lineup status, goalie start odds (DFO confirmations > recent share > prior; back-to-backs) |
| `briefing.py` | The evening lineup message: timing (21:00 local or 1h before first puck), quiet hours, when an update is worth sending |
| `matchup.py` | The weekly plan: both teams' week, P(win), the goalie minimum, add/drops, the add budget |

## How the weekly plan works (`matchup.py`)
- **A team's week** = the best lineup on each remaining game day (the opponent is
  assumed optimal too), plus points so far from box scores. Goalie points are
  multiplied by P(reaching the 3-game minimum).
- **P(win)** = normal approximation of the point difference (the variance
  constants come from `model/CLAUDE.md`).
- **Candidate moves:** free agents shortlisted per position, times drops
  (the open spot first, then my 4 lowest long-run players).
- **Score** = this week's gain + long run. Long run = the whole-lineup
  projection over the next 2 weeks (with durability), per week, times the weeks
  left, x `LONG_RUN_DISCOUNT` 0.5.
- **Accept** if score >= the add threshold (3 pts at an even pace, paced by adds
  left) **and** (win odds +2 pts **or** the next two weeks gain >= 2x the threshold).
- `scripts/explain_week.py` prints every candidate with its verdict. Use it
  before arguing about a recommendation.

## Decision log (settled, don't reopen without new evidence)
| Date | Decision | Why |
|---|---|---|
| 2026-09-29 | Optimize P(win this week), not season points | H2H: only beating this week's opponent counts |
| 2026-09-29 | **Always keep 3 goalies** (policy A, Nico) | Season sim: 2G + a skater = +35 pts, but 2.5 vs 1.1 weeks with goalie points zeroed, plus goalie streams cost adds |
| 2026-09-29 | Long run is judged by a whole-lineup projection, not per-player value | The per-player shortcut dropped D into a thin D group and chased injury-prone forwards |
| 2026-09-29 | Upgrades that cost > 1 pt this week wait | They can be made next week at no cost |
| 2026-09-29 | Long-run edges must show within 2 weeks | Tiny edges multiplied over a season are noise (e.g. goalie-for-goalie swaps) |
| 2026-09-29 | Keep 6 adds for playoff weeks 24-26 | Playoffs decide the title, and Yahoo's 36 covers the entire season incl. playoffs (settings page). The regular-season pace is 30 adds / 23 weeks |
| 2026-09-29 | Post-draft waivers: adds count from Sep 30 | Every undrafted player showed "W (Sep 30)" |
| 2026-09-29 | Keep our projections (age-fixed), use Yahoo as a cross-check | After the age fix they match Yahoo closely, and ours update daily |

## Judgment calls (untested)
`MODEL_SD_SHARE` 0.08, `LONG_RUN_DISCOUNT` 0.5, `BASE_ADD_SCORE` 3,
`PLAYOFF_RESERVE` 6, `MIN_WIN_GAIN` 0.02. Revisit when we have in-season results.
