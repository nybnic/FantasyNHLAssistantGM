# engine/: decisions

| File | Decides |
|---|---|
| `lineup.py` | The best slot assignment for one day. Exact DP; a move must gain `KEEP_SLOT_BONUS` |
| `availability.py` | Who plays: skater injury/lineup status, goalie start odds (DFO confirmations > recent share > prior; back-to-backs) |
| `briefing.py` | The evening lineup message: timing (19:30-20:30 local, or 1h before first puck), quiet hours, when an update is worth sending |
| `matchup.py` | The weekly plan: both teams' week, P(win), the goalie minimum, add/drops, the add budget, the mid-week stance |
| `trade.py` | `/trade`: both teams' points per week before and after, over the 2 weeks after the review. Open spots are filled from free agents before and after (so a trade gets no credit for a hole the plan fills anyway), and you keep 3 goalies. Shows F/D/G counts (and injured) and flags a trade that leaves them unable to fill their starters. `/trade` alone: screen all 1-for-1 and 2-for-1 with a quick per-player value, keep those they wouldn't see as a loss by this league's draft rounds (`league/draft.py`) and that don't leave them short, judge the top 12 in full (~1 min), reply with the best per team |

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
- **Decided week** (P(win) < 10% or > 90%): this week's gain counts zero, so only
  a move that pays off within two weeks gets an add.
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
| 2026-10-01 | Screenshots are read with free offline OCR (RapidOCR), no paid API (Nico) | Claude vision would be more robust, but costs money; OCR + badge colours read Nico's screenshots perfectly |
| 2026-10-01 | Evening briefing window 19:30-20:30 Helsinki (Nico); no first briefing after it closes, updates until 23:00 | When Nico sets his lineup |
| 2026-10-01 | Decided weeks save adds: below 10% or above 90% win, this week's points count zero and an add must pay off within two weeks (Nico) | Points in a decided week don't change the result, and adds are capped per season, so a skipped add carries over. Unchecked: if points-for breaks standings ties, they aren't fully worthless |
| 2026-10-01 | Mid-week stance: chase below 50% win, protect above, concede/coast past 10%/90%; the plan goes out again from Wednesday noon (Nico) | Chase shows the add with the biggest win-odds swing even when it fails the add rule, with its cost, so Nico decides. Stances don't change the add rule itself |
| 2026-10-01 | No goalie-minimum alerts beyond the weekly line (Nico) | Yahoo already warns; the model still counts the minimum in P(win) |
| 2026-09-29 | Keep our projections (age-fixed), use Yahoo as a cross-check | After the age fix they match Yahoo closely, and ours update daily |

## Judgment calls (untested)
`MODEL_SD_SHARE` 0.08, `LONG_RUN_DISCOUNT` 0.5, `BASE_ADD_SCORE` 3,
`PLAYOFF_RESERVE` 6, `MIN_WIN_GAIN` 0.02, `CONCEDE_BELOW` 0.10, `COAST_ABOVE` 0.90, `trade.MIN_GAIN_PER_WEEK` 1.0, `trade.PICK_DECAY` 0.85 (how a draft round "feels" to a manager). Revisit when we have in-season results.
