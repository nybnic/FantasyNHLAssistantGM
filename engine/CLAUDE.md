# engine/: decisions

| File | Decides |
|---|---|
| `lineup.py` | The best slot assignment for one day. Exact DP; a move must gain `KEEP_SLOT_BONUS` |
| `availability.py` | Who plays: skater injury/lineup status tonight, fading on later days along return curves fit from game logs (slower the more games missed), goalie start odds (DFO confirmations > recent share > prior; back-to-backs; the team's last result moves its next game: a loss sits the goalie who lost) |
| `briefing.py` | The evening lineup message, as a diff against Yahoo's Start Active (simulated: everyone with a game, overflow by season value): one line when it's fine, else the benches and starts that beat it, with why. Timing (19:30-20:30 local, or 1h before first puck), quiet hours, when an update is worth sending |
| `ir.py` | The IR and IR+ slots: an injured active player who fits an empty one (DFO status standing in for Yahoo's tag), and an IR'd player back in his team's lineup. The weekly plan's adds assume the IR moves are made, so an add can be drop-free; Done applies the IR move too |
| `scorecard.py` | How the add suggestions turned out: the add's raw points vs the drop's over the 14 days after, made or not (a line in Monday's result; `scripts/scorecard.py` lists them) |
| `addprice.py` | What an add buys and costs, in win probability: later points' worth, the budget's pace, the add price (solved by simulating weeks over logged candidates) |
| `matchup.py` | The weekly plan: both teams' week, P(win), the goalie minimum, add/drops, the add budget, the mid-week stance |
| `report.py` | What the charts show, as plain data (the decision map, the schedule grid, an add's weekly gain, the add budget, last week's result and score race); `notify/charts.py` draws it |
| `trade.py` | `/trade`: both teams' points per week before and after, over the 2 weeks after the review. Open spots are filled from free agents before and after (so a trade gets no credit for a hole the plan fills anyway), and you keep 3 goalies. Shows F/D/G counts (and injured) and flags a trade that leaves them unable to fill their starters. `/trade` alone: screen all 1-for-1 and 2-for-1 with a quick per-player value, keep those they wouldn't see as a loss by this league's draft rounds (`league/draft.py`) and that don't leave them short, judge the top 12 in full (~1 min), reply with the best per team |

## How the weekly plan works (`matchup.py`)
- **A team's week** = the best lineup on each remaining game day (the opponent is
  assumed optimal too), plus points so far: Yahoo's score from a matchup screenshot
  taken today, else box scores, each day scored with that day's roster
  (`bot/weekly.rosters_by_day`: the day's snapshot, kept until its first puck, plus
  today's players less my later adds). Goalie points are
  multiplied by P(reaching the 3-game minimum).
- **P(win)** = normal approximation of the point difference (the variance
  constants come from `model/CLAUDE.md`).
- **Candidate moves:** free agents shortlisted per position (and by schedule fit),
  times drops (the open spot first, then my 2 lowest long-run players per group:
  forwards, D, goalies). An add who can't play yet (waivers, or this week's adds
  spent: everyone joins Monday) counts from that day, and his drop plays until
  then (`_deferred`): Yahoo makes both at once.
- **IR stashes:** with an IR or IR+ slot empty, the best injured free agents
  (`STASH_CANDIDATES`, by season value; DFO's status standing in for Yahoo's tag)
  can go straight into it: no drop now. Valued as an extra player whose games
  follow the return curves, less the cheapest drop's points on each day times
  the odds he's back by then (`_stash_moves`), against the same add price.
- **Later points** = the long run. Long run = the whole-lineup
  projection over the next 6 weeks (`LONG_RUN_WEEKS`, with durability), per
  week, times the weeks left, x `LONG_RUN_DISCOUNT` 0.5. Dropping one of my
  streaming spots (the 3 skaters closest to waiver level at their position,
  plus my weakest goalie) is credited only its scheduled gain while the
  streamer would be held: spots / the affordable adds a week (`hold_weeks`,
  ~3 weeks at 1.3 adds a week), counting the rest of this week.
- **Value** (in win-pts, percentage points of a weekly win) = this week's change
  in P(win) + later points x `later_weight` (what a point does in a typical
  week: 1 / sqrt(2 pi (sigma^2 + tau^2)), tau = the league's matchup spread
  from all known rosters).
- **Accept** if value >= the add price (`addprice.solve`): set so spending at
  that bar, at most 2 a week, uses adds at the budget's pace, simulated over
  this and earlier weeks' candidates (`state["add_pools"]`). Close weeks with
  good streamers clear it twice, lopsided weeks not at all. This week's adds go
  to moves that pay this week; a keeper gaining < 1 pt waits for Monday.
- `scripts/sim_add_policy.py` compares the price with the old points rule over
  simulated seasons.
- `scripts/explain_week.py` prints every candidate with its verdict. Use it
  before arguing about a recommendation.

## Decision log (settled, don't reopen without new evidence)
| Date | Decision | Why |
|---|---|---|
| 2026-09-29 | Optimize P(win this week), not season points | H2H: only beating this week's opponent counts |
| 2026-09-29 | ~~**Always keep 3 goalies** (policy A, Nico)~~ Superseded 2026-10-02: goalie count by value | Season sim: 2G + a skater = +35 pts, but 2.5 vs 1.1 weeks with goalie points zeroed, plus goalie streams cost adds |
| 2026-10-02 | **Two goalies or three is judged by value** (Nico: "based on analytical estimates"); never fewer than two | The long run now checks the goalie minimum each week (it had checked a 6-week block: "100%") and weighs a goalie lost for the week (`availability.GOALIE_KEEP`, fit on game logs), so a third goalie is kept, dropped or added like any move. `scripts/sim_goalies.py`, 2025-26, 8 leagues x 16 teams: by-value + like-for-like swaps +0.05 wins a season vs always-3 + swaps (+/- 0.10), carrying 2.87 goalies; always-2 -0.65 (+/- 0.18) vs always-3, -1.01 with swaps, with twice the weeks zeroed. So by value is as good as three and clearly better than two |
| 2026-09-29 | Long run is judged by a whole-lineup projection, not per-player value | The per-player shortcut dropped D into a thin D group and chased injury-prone forwards |
| 2026-09-29 | Upgrades that cost > 1 pt this week wait | They can be made next week at no cost |
| 2026-09-29 | ~~Long-run edges must show within 2 weeks~~ Superseded 2026-10-01 by the add price | Tiny edges multiplied over a season are noise (e.g. goalie-for-goalie swaps); now handled by the 6-week long run and streaming-spot holds |
| 2026-09-29 | Keep 6 adds for playoff weeks 24-26 | Playoffs decide the title, and Yahoo's 36 covers the entire season incl. playoffs (settings page). The regular-season pace is 30 adds / 23 weeks |
| 2026-09-29 | Post-draft waivers: adds count from Sep 30 | Every undrafted player showed "W (Sep 30)" |
| 2026-10-01 | Screenshots are read with free offline OCR (RapidOCR), no paid API (Nico) | Claude vision would be more robust, but costs money; OCR + badge colours read Nico's screenshots perfectly |
| 2026-10-01 | Evening briefing window 19:30-20:30 Helsinki (Nico); no first briefing after it closes, updates until 23:00 | When Nico sets his lineup |
| 2026-10-01 | ~~Decided weeks save adds: below 10% or above 90% win, this week's points count zero and an add must pay off within two weeks (Nico)~~ Superseded the same day by the add price, which gives the same result without cut-offs | Points in a decided week don't change the result, and adds are capped per season, so a skipped add carries over. Unchecked: if points-for breaks standings ties, they aren't fully worthless |
| 2026-10-01 | Mid-week stance: chase below 50% win, protect above, concede/coast past 10%/90%; the plan goes out again from Wednesday noon (Nico) | Chase shows the add with the biggest win-odds swing even when it fails the add rule, with its cost, so Nico decides. Stances don't change the add rule itself |
| 2026-10-01 | Free agents are also shortlisted by schedule fit: points on nights my lineup has their slot open, this week and next | The plain shortlist ranked by a player's own games and missed streamers whose games land on my empty nights. Only the search widens; the add rule is unchanged |
| 2026-10-01 | Long run averaged over 6 weeks, not 2; streaming spots (3 skaters nearest waiver level) are judged on the next 2 weeks as scheduled (Nico). The streaming-spot part is superseded by the hold below | Weekly gains swing 5-10 pts on the schedule alone: 2 weeks times the season made Tolvanen for Lindell "-55" (6-week view: -8). A bottom spot gets recycled every 2-3 weeks at ~1.3 adds a week, so its swap isn't a season-long loss. Replaces "long run = 2 weeks x season" |
| 2026-10-01 | Adds are priced in win probability: an add is made when this week's change in P(win) plus its later points' worth beats a price set by simulation to spend the budget at its pace (Nico) | Replaces the 3-pt bar, the 2-win-pt gate and the decided-week cut-offs. Nico: tighter weeks and better adds should get 2 adds, lopsided ones none; the price does that (2 adds at a 0-pt expected margin, 1 at 10-20, none at 40). Season sim, this week's candidates, 5000 seasons: +0.13 wins a season over the old rule (+/- 0.02, paired). Provisional until 4-6 weeks of candidates are logged |
| 2026-10-01 | A streaming-spot swap is credited its scheduled gain over the hold (spots / affordable adds a week, ~3 weeks), never the season; the weakest goalie is a streaming spot too (Nico) | Nico streams his lowest-value players. Season credit made Podkolzin (+5.9 now, 48% -> 54%) beat McBain (+10, -> 59%) on a long run he'd never be held for, and Cooley for Murashov on schedule noise (-10.9, then +12.2). Replaces "the better of next 2 weeks and the long run" |
| 2026-10-01 | This week's adds go to moves that pay this week; a keeper gaining < 1 pt this week waits for Monday's adds and is named as such (Nico) | Silovs (+0 this week) took the last weekly add while McBain (+10, win 48% -> 59%) was labelled "not recommended". Extends "upgrades that cost this week wait". Risk: the keeper gets claimed meanwhile |
| 2026-10-01 | Free agents take Yahoo's position eligibility, seeded from Nico's export (positions only, Nico OK'd committing them) and learned from every Yahoo screenshot (`league/positions.py`) | NHL data gives one position; 37% of 560 skaters differ on Yahoo (Guentzel: NHL C, Yahoo LW/RW) |
| 2026-10-01 | No goalie-minimum alerts beyond the weekly line (Nico) | Yahoo already warns; the model still counts the minimum in P(win) |
| 2026-10-03 | DFO line charts are archived daily in a private repo, not this public one (Nico) | DFO keeps no history, and role/stash signals can only be tested on an archive; republishing their charts publicly risks the source the live bot depends on (`bot/dfo_archive.py`) |
| 2026-10-03 | No fixed bonus for young players (Nico) | Unbacked, and it double-counts the age curve and option value. Stash plan P0 tests whether young, low-sample players are under-projected; if so the fix goes into the model |
| 2026-09-29 | Keep our projections (age-fixed), use Yahoo as a cross-check | After the age fix they match Yahoo closely, and ours update daily |

## Judgment calls (untested)
`MODEL_SD_SHARE` 0.08, `LONG_RUN_DISCOUNT` 0.5, `addprice.DEFAULT_TAU` 20 (until rosters give a spread),
`PLAYOFF_RESERVE` 6, `MIN_GOALIES` 2 (the floor), `MIN_WIN_GAIN` 0.02 (display only), `LONG_RUN_WEEKS` 6, `STREAMING_SPOTS` 3, `GOALIE_STREAMING_SPOTS` 1, `STREAM_WEEKS` 2, `KEEPER_WAITS_BELOW` 1.0, `CONCEDE_BELOW` 0.10 / `COAST_ABOVE` 0.90 / `EVEN_WITHIN` 0.03 (wording only; an even week also shows the biggest swing), `trade.MIN_GAIN_PER_WEEK` 1.0, `trade.PICK_DECAY` 0.85 (how a draft round "feels" to a manager). Revisit when we have in-season results.
