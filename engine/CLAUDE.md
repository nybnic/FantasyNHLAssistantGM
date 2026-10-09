# engine/: decisions

| File | Decides |
|---|---|
| `lineup.py` | The best slot assignment for one day. Exact DP; a move must gain `KEEP_SLOT_BONUS` |
| `availability.py` | Who plays: skater injury/lineup status tonight, fading on later days along return curves fit from game logs (slower the more games missed), goalie start odds (DFO confirmations > recent share > prior; back-to-backs; the team's last result moves its next game: a loss sits the goalie who lost) |
| `briefing.py` | The evening lineup message, as a diff against Yahoo's Start Active (simulated: everyone with a game, overflow by season value): one line when it's fine, else the benches and starts that beat it, with why. Timing (19:30-20:30 local, or 1h before first puck), quiet hours, when an update is worth sending |
| `ir.py` | The IR and IR+ slots: an injured active player who fits an empty one (DFO status standing in for Yahoo's tag), and an IR'd player back in his team's lineup. The weekly plan's adds assume the IR moves are made, so an add can be drop-free; Done applies the IR move too |
| `scorecard.py` | How the add suggestions turned out: the add's raw points vs the drop's over the 14 days after, made or not (a line in Monday's result; `scripts/scorecard.py` lists them) |
| `addprice.py` | What an add buys and costs, in win probability: later points' worth, the budget's pace, the add price (solved by simulating weeks over logged candidates) |
| `season.py` | Playoff and title odds and this week's leverage (P(playoffs) if won - if lost) next to a typical week left: the rest of the season simulated from the standings, my real schedule, the others paired at random. A line in the plan; not yet used by the add price (Nico to decide) |
| `matchup.py` | The week: both teams' projections, P(win), the goalie minimum, every add/drop's value (this week, the weeks ahead, the long run), the add budget |
| `plan.py` | The plan: which add/drops and when, as one coherent set (`compose`, `search`), and whether a new one replaces the one Nico has seen (`decide`, `CHANGE_MARGIN`) |
| `report.py` | What the dashboard and charts show, as plain data (the schedule grid, the add budget, last week's result and score race; each team's week player by player and by stat group, `team_view`; a player's per-game numbers by stat, availability and durability, `player_view`); `site/index.html` and `notify/charts.py` draw it |
| `trade.py` | `/trade`: both teams' points per week before and after, over the 6 weeks after the review (`LONG_RUN_WEEKS`, as an add's long run). Open spots are filled from free agents before and after (so a trade gets no credit for a hole the plan fills anyway), and you keep 3 goalies. Shows F/D/G counts (and injured) and flags a trade that leaves them unable to fill their starters. `/trade` alone: screen all 1-for-1 and 2-for-1 with a quick per-player value, keep those they wouldn't see as a loss by how the players feel to a manager (draft round, `league/draft.py`, blended toward this season's fantasy-point rank: `PERCEPTION_GAMES`) and that don't leave them short, judge the top 12 in full (~30 s on GitHub, 2026-10-03), reply with the best per team |

## How the weekly plan works (`matchup.py`)
- **A team's week** = the best lineup on each remaining game day (the opponent is
  assumed optimal too), plus points so far: Yahoo's score from a matchup screenshot
  taken today, else box scores, each day scored with that day's roster
  (`bot/weekly.rosters_by_day`: the day's snapshot, kept until its first puck, plus
  today's players less my later adds). Goalie points are
  multiplied by P(reaching the 3-game minimum).
- **P(win)** = normal approximation of the point difference (the variance
  constants come from `model/CLAUDE.md`). Banked points count in full, the
  margin still to play at `MARGIN_REALIZES` 0.80 (how projected margins realize).
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
- **The weeks ahead** (`AHEAD_WEEKS` 2, `week_ahead`): both teams' projections
  of each, my current roster against that week's real opponent (`config/league.py`),
  the margin read at `MARGIN_REALIZES_AHEAD` (0.75 / 0.70). A move's gain on
  those days, at the size fringe gaps realize (`GAP_REALIZES` 0.95 / 0.89),
  counts by the change it makes to that week's P(win) (`ahead_value`): a
  point in a close week ahead is worth far more than in a lopsided one.
- **IR returns** (`ir_returns`, the top `IR_RETURN_MOVES` 30 moves): with players in IR slots, each
  day after this week mixes the move's gain as it is with its gain once they're back
  (`with_returns`: the roster with them back, the cheapest to lose cut, by projection), weighted by
  the odds they're back (`ir_back`, return curves). So an add that would be the one cut counts
  only until then. IR stashes keep their own return logic (not the others' returns).
- **Durability** (`matchup.durability`): a player's projected missed games (DFO projected GP) cost
  only his edge over a replacement-level free agent (`ctx.replacement_xfp`, best 3 by group), since
  a missed game is streamed. Untested (DFO projections aren't archived).
- **Later points** = the long run after the weeks ahead (`horizon`): the
  whole-lineup projection over the next 6 weeks (`LONG_RUN_WEEKS`, with
  durability), per week, times the weeks left after the ones ahead, x
  `LONG_RUN_DISCOUNT` 0.5. Dropping one of my streaming spots (the 3 skaters
  closest to waiver level at their position, plus my weakest goalie) is
  credited only its scheduled gain while the streamer would be held: spots /
  the affordable adds a week (`hold_weeks`, ~3 weeks at 1.3 adds a week),
  counting the rest of this week; the weeks ahead count only those days too.
- **Value** (in win-pts, percentage points of a weekly win) = this week's change
  in P(win) + each week ahead's + later points x `later_weight` (what a point
  does in a typical week: 1 / sqrt(2 pi (sigma^2 + tau^2)), tau = the league's
  matchup spread from all known rosters, x 0.80 as margins realize). The add
  price's simulation counts the weeks ahead's points as later points.
- **Accept** if value >= the add price (`addprice.solve`): set so spending at
  that bar, at most 2 a week, uses adds at the budget's pace, simulated over
  this and earlier weeks' candidates (`state["add_pools"]`). Close weeks with
  good streamers clear it twice, lopsided weeks not at all.
- **The plan** (`plan.compose`): moves best value first, each judged with the
  ones before it made (no shared add or drop). A move that pays this week takes
  one of this week's adds today. A keeper that does nothing this week (< 1 pt)
  waits for Monday's adds if another move can use this week's add; otherwise it
  takes this week's add, which would expire unused, today, or before Wednesday's
  plan is held for it (`holds_keepers`, unless the week is decided).
- **The day** (`plan.time_moves`): each move's exact day, the earliest of its week's days that
  loses no points (the drop's games first, the add in before his); waiting only risks a claim.
- **Keeping it** (`plan.decide`, `bot/weekly.run_plan`): the plan Nico has seen
  stays unless a move can't be made (taken, its drop gone, skipped, no longer
  worth an add) or a new plan is `CHANGE_MARGIN` (0.02 wins) better. Its old
  adds are always scored again (`extra_ids`), so a shortlist reshuffle can't drop
  them. A change is one message with its reason; replaced cards lose their buttons.
- **Messages** (`bot/messages.py`): the plan message (Monday and Wednesday noon,
  /week): matchup, what to do and when, the week's best stream if it isn't the
  plan's, the weeks ahead, the budget, warnings. A card per move on its day,
  once. A screenshot or Taken/Skip gets the score or the change; the evening
  check speaks only on a change.
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
| 2026-10-03 | Before Wednesday's plan, a keeper gaining < 1 pt this week holds the add instead of taking it, unless the week is decided; the streamer caption says when you're favored (Nico) | Week 2 (77%): Parayko (-0.1 pts this week) took the 2nd Monday add, leaving none to chase with if the week turned. Waiting costs nothing this week; the risk is a claim before Wednesday. Whether a stream in a close week should wait for mid-week information is untested (needs a season sim) |
| 2026-10-03 | /trade shows a trade's gain as "typical week's win odds +7%", not "win 50% -> 57%" (Nico) | It read as this week's matchup odds; it is the 6-week points gain as a typical week's odds vs a team as good as yours. Horizon unchanged (6 weeks) |
| 2026-10-03 | /trade guesses acceptance with stars feeling worth more: `PICK_DECAY` 0.85 -> 0.8 (Nico) | At 0.85 a 4th + 6th-rounder "felt" like a 1st (Knight + Dobson for MacKinnon was suggested); at 0.8 a 1st outweighs a 4th + 6th, not a 2nd + 3rd. Judgment call: no acceptance data |
| 2026-10-03 | The add price stays blind to standings leverage and playoff-week points | `scripts/sim_leverage.py` (whole league, paired seasons): leverage weighting +0.6-1.6 pts playoff odds but -0.3-1.0 title odds; playoff points x2 -0.4-0.9 title. Even spending to week 23 carries the most points into the playoffs. Revisit with 6+ logged weeks |
| 2026-10-09 | **One engine, one Board** (Nico, `docs/plan-2026-10-09.md`): a move is scored in expected wins, the change in P(win) this week and the next two against the real opponents and schedules, plus shrunk later points; title and playoff odds are context, not the per-move score; 6 adds kept for the playoffs | Week 2: 13 cards in 5 days, the add picked by the season-extrapolated long run (Kelly +37, Kantserov +24 for one drop, 0-1.5 pts this week), "chase" while every chase move failed the price. Title odds per move are noise, and leverage weighting lowered title odds (2026-10-03) |
| 2026-10-09 | A committed plan changes only when it can't be done or a new one is 0.02 wins better, and says why; a card is sent once; a screenshot updates the score (Nico) | Repeats and near-tie flips made the advice unreadable. 0.02 is a judgment call until the error band is measured |
| 2026-10-09 | Telegram is for the action, the dashboard (HTML) for evaluating; the Telegram image is a screenshot of the dashboard (Nico) | matplotlib charts weren't readable enough; one design everywhere |
| 2026-10-09 | The next 2 weeks are played against their real opponents; P(win) reads margins at their realized size (0.80 this week, 0.75 / 0.70 for the weeks ahead); the long run stays the 6-week rate x 0.5, now for the weeks after those two | `check_gaps`: fringe gaps realize at 1.02 / 0.95 / 0.89 / 0.84 (weeks 3-6) / ~0.75 (to week 20), both seasons, so the long run isn't inflated and isn't shrunk further. `check_sigma --ahead`, 2024-25 / 2025-26: team margins 0.77 / 0.84 this week, 0.76 / 0.74 a week before, 0.67 / 0.73 two before; spread right (x0.92-1.03). A long run on weeks 3-6 alone was tried and reverted the same day: four weeks of schedule swung moves 15+ pts (Spurgeon +17.6 -> +0.1) |
| 2026-10-09 | A keeper that does nothing this week waits for Monday only if another move can use this week's add; else it takes this week's add (refines 2026-10-01) | Oct 9: Kantserov (1.5 pts this week, 21 win-pts) took the last weekly add and Kelly (0 pts, 40 win-pts) was sent to Monday dropping the same player. Waiting is free only when it frees the add for something else; this week's add expires Sunday |
| 2026-10-09 | One plan message on Monday and Wednesday (and /week), cards once on their day, screenshots answered with the score or the change, the evening check silent unless the plan changed (Nico: D1-D4) | Replaces the plan resent on every screenshot (8 of week 2's 13 cards were repeats), the goalie line, season line, stance, biggest swing, can-wait and streamer caption: their content is in the plan message's lines, the Board and the dashboard |
| 2026-10-09 | Projected missed games cost only the edge over replacement; players in IR slots coming back are in every move's later value (Nico) | Kelly for Samuelsson was +40: 4.15 vs 4.14 per game, 70% of the edge was DFO projecting Samuelsson at 81% of games, and Kelly would be the forward cut when Celebrini returns (~41% by week 3's end). Nico: an injured fringe player is moved to IR or dropped and streamed, so durability shouldn't separate fringe players. After: -5; the plan became Spurgeon for Samuelsson (+11) |
| 2026-10-09 | Every Telegram message links the dashboard, as a "Dashboard" button on its own row (Nico) | The dashboard is where a plan is evaluated; a button, not the URL in the text, so no link preview is added to each message. It stays on a card after Done/Taken/Skip (`notify/telegram.py`) |
| 2026-09-29 | Keep our projections (age-fixed), use Yahoo as a cross-check | After the age fix they match Yahoo closely, and ours update daily |

## Judgment calls (untested)
`MODEL_SD_SHARE` 0.08, `LONG_RUN_DISCOUNT` 0.5, `addprice.DEFAULT_TAU` 20 (until rosters give a spread),
`PLAYOFF_RESERVE` 6, `MIN_GOALIES` 2 (the floor), `MIN_WIN_GAIN` 0.02 (display only), `LONG_RUN_WEEKS` 6, `STREAMING_SPOTS` 3, `GOALIE_STREAMING_SPOTS` 1, `STREAM_WEEKS` 2, `KEEPER_WAITS_BELOW` 1.0, `plan.CHANGE_MARGIN` 0.02, `CONCEDE_BELOW` 0.10 / `COAST_ABOVE` 0.90 / `EVEN_WITHIN` 0.03 (wording only; an even week also shows the biggest swing), `trade.MIN_GAIN_PER_WEEK` 1.0, `trade.PICK_DECAY` 0.8 (how a draft round "feels" to a manager), `trade.PERCEPTION_GAMES` 20. Revisit when we have in-season results.
