# engine/: decisions

| File | Decides |
|---|---|
| `lineup.py` | The best slot assignment for one day. Exact DP; a move must gain `KEEP_SLOT_BONUS` |
| `availability.py` | Who plays: skater injury/lineup status tonight, fading on later days along return curves fit from game logs (slower the more games missed), goalie start odds (DFO confirmations > recent share > prior; back-to-backs; each goalie's own last start, by team games since, and his recent form move the shares among his team's goalies, most for the next game) |
| `briefing.py` | The evening lineup message, as a diff against Yahoo's Start Active (simulated: everyone with a game, overflow by season value): one line when it's fine, else the benches and starts that beat it, with why. Timing (19:30-20:30 local, or 1h before first puck), quiet hours, when an update is worth sending |
| `ir.py` | The IR and IR+ slots: an injured active player who fits an empty one (DFO status standing in for Yahoo's tag), and an IR'd player back in his team's lineup. The weekly plan's adds assume the IR moves are made, so an add can be drop-free; Done applies the IR move too |
| `scorecard.py` | How the moves turned out. The main check: each move I made against the team just before it (`made_moves` reads them off the daily roster record, IR stints and IR-return cuts told apart; `move_weeks` plays the week with that one swap undone, box scores, best lineup each day, goalie minimum; read as wins at the week's first-plan odds), its first 3 weeks against the plan's forecast for them (`move_check`, Monday's result, `moves_checked`). The season against doing nothing (the drafted roster, `week_value`), one line. Suggestions not made: raw points vs the drop over 14 days. `scripts/scorecard.py` lists all |
| `addprice.py` | What an add buys and costs, in win probability: later points' worth, the budget's pace, the add price (`pacing_price`: `BAR` 0.12 x (even pace / the pace the adds left allow)^`PUSH` 2; `solve`, the earlier simulated price, kept for the replay and simulations) |
| `season.py` | Playoff and title odds and this week's leverage (P(playoffs) if won - if lost) next to a typical week left: the rest of the season simulated from the standings, my real schedule, the others paired at random. A line in the plan; not yet used by the add price (Nico to decide) |
| `matchup.py` | The week: both teams' projections, P(win), the goalie minimum, every add/drop's value (this week, the weeks ahead, the long run), the add budget |
| `plan.py` | The plan: which add/drops and when, as one coherent set (`compose`, `search`), and whether a new one replaces the one Nico has seen (`decide`, `CHANGE_MARGIN`) |
| `report.py` | What the dashboard and charts show, as plain data (the schedule grid, the add budget, last week's result and score race; each team's week player by player and by stat group, `team_view`; a player's per-game numbers by stat, availability and durability, `player_view`, and his season so far against what was projected before each game, `season_view`; every team's games this week and the next two, `team_games`); `site/index.html` and `notify/charts.py` draw it |
| `trade.py` | `/trade`: both teams' points per week before and after, over the 6 weeks after the review (`LONG_RUN_WEEKS`, as an add's long run). Open spots are filled from free agents before and after (so a trade gets no credit for a hole the plan fills anyway), and an add the trade needs beyond those is charged the add price (`add_cost`, in points over the horizon), and you keep 3 goalies. Shows F/D/G counts (and injured) and flags a trade that leaves them unable to fill their starters. `/trade` alone: screen all 1-for-1 and 2-for-1 with a quick per-player value, keep those they wouldn't see as a loss by how the players feel to a manager (draft round, `league/draft.py`, blended toward this season's fantasy-point rank: `PERCEPTION_GAMES`) and that don't leave them short, judge the top 12 in full (~30 s on GitHub, 2026-10-03), reply with the best per team |

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
  The final score shown (message, card, dashboard) is read the same way (`final_scores`: the
  points still to come moved toward the two sides' average, so its gap is the margin P(win)
  uses); the players' raw projections stay in the matchup tab and the logged forecasts.
- **Candidate moves:** free agents shortlisted per position (and by schedule fit),
  times drops (the open spot first, then my 2 cheapest to lose per group: forwards,
  D, goalies; `cut_costs`: losing him valued as a move, from the nights he'd be in my
  best lineup this week and the next two, plus later points by his own tenure), plus my
  most-benched skater (`bench_shares`: 25%+ of his long-run points on the bench,
  `BENCHED_SHARE`), so a good player stuck behind better ones at his position is
  tried against adds at other positions. An add who can't play yet (waivers, or this week's adds
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
- **Back from IR now** (`bot/weekly.activate_returns`): a player in an IR slot whom DFO lists in his
  team's lineup takes an active spot first (Yahoo allows no other move until he has one), and the
  active player cheapest to lose (`cut_costs`, never a goalie below 2) is cut when the roster is full. The plan's adds are built on that roster, and
  the activation is the plan's first line (2026-10-11: the plan had dropped Lindell for Spurgeon while
  Celebrini's return had to cut Lindell).
- **IR returns** (`ir_returns`, the top `IR_RETURN_MOVES` 30 moves): with players in IR slots, each
  day after this week mixes the move's gain as it is with its gain once they're back
  (`with_returns`: the roster with them back, the cheapest to lose cut, by projection), weighted by
  the odds they're back (`ir_back`, return curves). So an add that would be the one cut counts
  only until then. IR stashes keep their own return logic (not the others' returns).
- **Durability** (`matchup.durability`): a player's projected missed games (DFO projected GP) cost
  only his edge over a replacement-level free agent (`ctx.replacement_xfp`, best 3 by group), since
  a missed game is streamed. Untested (DFO projections aren't archived).
- **Later points** (`tenure_horizon`, since 2026-10-23, Nico; roadmap item 8): the
  whole-lineup projection (with durability) on every day left through the fantasy playoffs
  (`bot/weekly.week_inputs`' `future`), each day's gain x P(the add is still mine that week)
  x the share of a gap that far ahead that shows up (`tenure.realizes`: 0.84 weeks 3-6, 0.76
  to 12, 0.74 after, `check_gaps`); the weeks ahead take the same survival before their
  P(win). P(still mine) is `engine/tenure.py`'s curve for where the add would sit
  (`tenure_bin`): a skater by how many of my other skaters are closer to waiver level at
  their position, a goalie by whether he'd be my weakest of three or more; fit Kaplan-Meier
  on the replay's tenures under the bot's own policy (the lowest skater 42% still there at 3
  weeks, 7% at 12; 7th+ 94% / 66%; goalies 36-51% / 11-16%). The playoffs count as
  scheduled, at the same weight (Nico, 2026-10-17). A player cut (`cut_costs`) is judged by
  where he sits now; an IR stash by where he'd sit; IR returns mix in as before. The Board
  shows each move's odds at 3, 6 and 12 weeks (`stays`); under 25% at 6 weeks it's called a
  stream, from half a keeper (`STREAM_BELOW`, wording only). Replaced the 6-week rate x
  the weeks left x `LONG_RUN_DISCOUNT` 0.5, the keep share and the streaming hold (kept in
  `matchup.py` for the replay's reference variants).
- **Value** (in win-pts, percentage points of a weekly win) = this week's change
  in P(win) + each week ahead's + later points x `later_weight` (what a point
  does in a typical week: 1 / sqrt(2 pi (sigma^2 + tau^2)), sigma = a typical full
  week's spread, mine and an average team's (`weekly.typical_sigma`; until 2026-10-11 it
  was the days left scaled to 7, 1.5x the weight on a Sunday), tau = the league's
  matchup spread from all known rosters, x 0.80 as margins realize). The add
  price's simulation counts the weeks ahead's points as later points.
- **Accept** if value >= the add price (`addprice.pacing_price`, since 2026-10-22): 12 win-pts at an
  even pace (30 adds over 23 weeks; the playoffs' 6 over 3), times (even pace / the pace the adds left
  allow) squared: lower with adds saved, higher when spent ahead of pace, at most 2 a week. It moves only
  when adds are made or saved. Close weeks with good moves clear it twice, lopsided weeks not at all,
  through the moves' values (a near-certain week's points barely move its P(win)).
- **The plan** (`plan.compose`): moves best value first, each judged with the
  ones before it made (no shared add or drop). A move that pays this week takes
  one of this week's adds today. A keeper that does nothing this week (< 1 pt)
  waits for Monday's adds if another move can use this week's add; otherwise it
  takes this week's add, which would expire unused, today, or before Wednesday's
  plan is held for it (`holds_keepers`, unless the week is decided).
- **The day** (`plan.time_moves`): each move's exact day, the earliest of its week's days that
  loses no points (the drop's games first, the add in before his); waiting only risks a claim.
- **Tonight's starter** (`plan.starter_conditions`): a goalie add made today whose team plays
  tonight, its starter not yet confirmed by DFO, is re-valued with his partner confirmed instead.
  If it then no longer beats the best move using the same drop (else the add price), it's made
  only once he's confirmed: the card says so and what to do if the partner starts. A confirmation
  the other way changes the plan whatever the margin; his way sends one "go" line.
- **Keeping it** (`plan.decide`, `bot/weekly.run_plan`): the plan Nico has seen
  stays unless a move can't be made (taken, its drop gone, skipped, no longer
  worth an add) or a new plan is `CHANGE_MARGIN` (0.02 wins) better. Its old
  adds are always scored again (`extra_ids`), so a shortlist reshuffle can't drop
  them. A change is one message with its reason; replaced cards lose their buttons.
- **Breakout watch** (`bot/watch.py`, Nico 2026-10-13): every run, free agents whose role just changed:
  a goalie whose team's starter (50%+ of the starts) is injured, at once; a goalie expected to start 20+
  points more than projected before the season (his share of the starts ahead, injured teammates'
  redistributed, 40%+); a DFO promotion to a top-6 line, the top D pair or PP1, from its first game; an
  ice-time jump (last 3 games 3+ min over the 10 before). Valued by a quiet plan run that scores them; one alert
  per player (all his signals) once in 14 days, held through quiet hours. A promotion alone is sent only when his
  best move is worth half the add price (`ALERT_SHARE`) or is in the plan; the rest are listed on the dashboard
  (`state.watch.log`). Goalies and ice-time jumps are always sent. Thresholds are judgment calls.
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
| 2026-10-10 | **DFO's injury status decides, not its injured section** (Nico) | DFO lists every injured player in its injured section ("ir" group) whatever his status; the bot read all of them as NHL IR (the slow, 3+ game return curve, and IR-slot eligible). Celebrini sat there "dtd" after 2 missed games: read as IR, 29% to play 3-6 days on and 59% at six weeks. On Oct 10, 53 of 86 players in that section were "out" (27) or "dtd" (26). Now: "ir", or the section with no status, is IR; "out" follows the curve for his games missed (Yahoo tag O: IR+ only); "dtd" there is out tonight (off the chart), then `DTD_RETURN`. Effect that day: Spurgeon for Lindell +10.9 -> +9.5, for Samuelsson +10.7 -> +10.8 (the plan kept, under the change margin); the Marchand stash went (out, so IR+ only, which Celebrini holds). `DTD_RETURN` stays a judgment call: the DFO archive (`bot/dfo_archive.py`, daily since Oct 6) is what can fit it |
| 2026-10-11 | ~~**Later points ramp from the streaming hold to the season** (Nico: option 2 of three), on the lower of the add's and the drop's gap over waiver level~~ The ramp stays; judged on the add since 2026-10-20 (`keep_share`, `KEEP_RAMP` 1.0 points a game) | The on/off switch credited the 4th skater from waiver level the season and the 3rd only the ~3-week hold. Samuelsson and McBain were 0.003 a game apart (-0.36 each): Spurgeon for Samuelsson +19 win-pts (12 later), the same D upgrade for Lindell +10 (1.5 later). An add who'd be a streaming spot himself got the season too (Dach, Landeskog). After: Spurgeon for Samuelsson keeps 1% of the season, +7.7, under the 9.0 price; the plan became Spurgeon for Lindell (+10). Ramp width untested: for the replay harness |
| 2026-10-11 | **Every drop is judged with the schedule and my best lineup** (Nico), the IR-return cut included: the cheapest to lose (`matchup.cut_costs`), not the lowest points a game | The cut for Celebrini was Lindell by points a game alone; Nico: he plays on a light night. A game on a night the slot is full is worth nothing, one on an open night in full. Lindell stays the likely cut (week 3: 3.5 pts lost, only his Wednesday game makes the lineup; the next cheapest 4.6), but a week like week 4 would favor another (Samuelsson 7.7 vs Lindell 10.4) |
| 2026-10-11 | **Keep the settings after the first replay** (Nico): `KEEP_RAMP` 1.0, 3 streaming spots, `LONG_RUN_DISCOUNT` 0.5; no more weight on later points for now | `scripts/replay.py`, 2025-26 (see "Replayed"): none of the alternatives differed by more than its error (ramp width +/- 0.07, spots +/- 0.13, discount +/- 0.04 wins a season). Every swap credited the season +0.24 (+/- 0.13) is a hint, not evidence: a 16-league run would settle it |
| 2026-10-13 | **Role changes are read as they happen** (Nico: "the bot doesn't recognise fast enough real changes in responsibilities", Garand behind an injured Shesterkin): an injured goalie's starts go to his healthy teammates by share (0.30 by the return curve, 0.45 with a news timeline, `model/CLAUDE.md`); an injury's news timeline ("rest of the season", "four more weeks") beats the return curve while it lasts | Before, the backup's share rose only as he started: ~20% now, ~55% after five straight starts, ~80% after ten, 2-3 weeks late. Factors fit 2023-25, better on 2025-26. The news timeline's after-part is a judgment call (archived from 2026-10-13 to fit) |
| 2026-10-13 | **The watch flags a bigger expected role, not one game** (Nico: "not a watch if a FA gets one start, but if it is expected that they will start a lot more vs projected") | Its first day sent four "DFO lists him No. 1" goalie alerts: DFO's No. 1 is tonight's starter, so a tandem backup's one start made him No. 1. Now a goalie needs an expected share of the starts 20+ points over his preseason projection (one start moves it ~7). Refined the same day (Nico): a starter's injury flags his team's free-agent goalies at once, whatever the shares; a skater's DFO promotion flags from its first game |
| 2026-10-14 | **A line promotion alone is sent only when the plan values him at half the add price or more** (Nico: "50% enough"; ice-time jumps stay as they are, every 30 min stays); one alert per player | The watch's second day sent 26 alerts (~9 messages): 23 DFO promotions (7 to the second line alone, 5 to PP1, 5 to the top D pair), in a week of line shuffles. Promotions under the bar are listed on the dashboard, not re-checked (the plan's own runs score free agents anyway). The 50% is a judgment call: check it with the DFO promotion test (mid-November) and `state.watch.log`'s values |
| 2026-10-15 | **My most-benched skater is tried as a drop too** (Nico: "Conduct the change"), when 25%+ of his long-run points sit on the bench (`BENCHED_SHARE`, a judgment call) | Pinto, the third C-only center (5.7 a game, above a waiver C), starts 4 of his 9 games Oct 15 - Nov 1 behind Celebrini and Scheifele, while 4 D leave D slots open on their idle nights. His long-run value kept him out of the 2 cheapest forwards, so Pinto for a D was never weighed. A simple count (xfp x the nightly lineup, Oct 15 - Nov 1): Pinto for Samberg +12.8 pts vs Samberg for Samuelsson +5.1. It only widens the search: every move still meets the add price |
| 2026-10-17 | **The fantasy playoffs count as scheduled** (Nico: "the playoff schedule / lineup is considered also in the decisions"): a move's later points take its projected gain on the days of weeks 24-26 (my best lineup each night, the goalie minimum each week), not three average weeks; same weight as any later week | The 6-week average stood in for the playoffs, so a goalie whose team is idle on my open nights then counted as if it weren't. Woll for Pinto on the real schedule (`scripts/third_goalie.py`): +3.7 pts a week in weeks 4-23, -0.5 in the playoffs (in week 26 his team plays on 3 nights both of mine do); Stolarz +1.6 and +1.8. After: Woll +44.8 -> +37.8 win-pts (later points 52.6 -> 43.5), Stolarz +36.5 -> +33.6, Grubauer and Schmid fall under the price; skaters barely move. Not weighted up: playoff points x2 lowered title odds (2026-10-03, `sim_leverage`). Costs ~50% more long-run projection per move |
| 2026-10-20 | **A swap's season credit is judged on the add** (Nico: "keeper-add yes", after asking why Pinto is always the drop: a goalie add for a streaming-spot skater got only the hold, Meriläinen for Larsson 4 later points to 43 for Pinto); a goalie add is a keeper | Replayed, 16 leagues: skater swaps only, +0.23 (0.09) wins a season at the bot's price with fewer adds, +0.07 (0.08) at equal adds; with goalie moves (the replay makes them since), +0.18 (0.12) at the bot's price. A streamer added onto a spot below waiver level gets some season too (the line is my spots' best gap): as replayed. Today's plan: Meriläinen for Pinto stays (+44); the add price rose 9.9 -> 12.3 (more moves carry season value), so Arvidsson for McBain (8.3, a streaming swap) fell out |
| 2026-10-20 | **Goalie starts read each goalie's own record; a goalie add worth it only if he starts waits for the confirmation** (Nico: "Conduct 1, 2, 3", after Kuemper for Pinto was planned at 70% to start the morning after his first-period pull) | The old rule moved only the team's next game, by the team's last start: Forsberg's loss lifted Kuemper. Fit (`model/CLAUDE.md`): next-game log loss 0.690 -> 0.658, games 2-3 0.701 -> 0.687, later a little (form). Kuemper ~60%, and with Forsberg confirmed his week falls from +12 to ~+2 points, under Woll for Pinto: the card now says to wait for LAK's starter, Woll if Forsberg starts. Waiting risks a claim (adds are instant); how often that happens is untested |
| 2026-10-21 | **The add price's pools keep each week's best moves per add and drop, not per add** (Nico: "make the edit", after asking whether 2 adds a week would run out before the playoffs) | The pools kept each add with his best drop, mostly one player (weeks 1-4: 17, 15, 24, 22 of 25 moves shared a drop), and a second add can't share it, so the simulation saw almost no second adds and the price stayed low (8.7 win-pts) while the live plan, weighing every drop, kept finding a second move over it. Week 4 per pair: 2.0 adds a week at 8.7, its price alone 14.8. Live on 2026-10-21 (Check, explain-week): 14.3 (week 4 re-logged; weeks 1-3 keep the bias until they age out). Pospisil for Larsson (20.5) still passes; Kleven, Guhle, Murphy for Larsson stay over it, Zub for Larsson (12.1) falls under. Nico had used 5 adds in 4 weeks (on pace, 1.25); at 2 a week from here 39 more would be needed with 31 left |
| 2026-10-22 | **The add price paces the budget: 12 win-pts x (even pace / the pace the adds left allow)^2** (Nico: "pacing to leave 6 for the playoffs; the better the move the better to spend; why should the candidate pool move the price?"; then "Yes" to 12 x pacing^2) | The simulated price followed the current week's shortlist: 8.7 -> 14.3 -> 19.4 -> 17.9 -> 22 win-pts in a day (one goalie's start odds moved it), as weeks 1-3's pools hardly offer a second add and week 4's is rewritten every run. Replayed (16 leagues, "Replayed"): every pacing bar ahead of the simulated price, PUSH 2 +0.22 (0.11), 3-6x steadier; the simulated price ran 0.61 / 0.16 / 0.02 by thirds of the season. The bar is set on the live scale (the replay's values run 2-3x the live ones early): ~1.3 adds a week on the live plans of Oct 9-22. Leverage stays in the moves' values, not the price. On 2026-10-22: 13 win-pts (25 spare adds for 20 weeks, 1.25 a week) instead of 22. Re-check `BAR` mid-December against the per-move scoreboard |
| 2026-10-21 | **Engine freeze** (Nico): a valuation or plan rule changes only with replay, backtest or calibration evidence (CLAUDE.md, Rules); questions about one recommendation go to the roadmap's "Questions for the replay" | 232 log entries in three weeks, most a rule patched after one case, while the replay found the tuned settings worth +/- 0.07 wins a season, inside their error. The scoreboard (Monday's result) now measures what the moves add |
| 2026-10-21 | **Each move is judged against the team just before it, not the drafted roster** (Nico: "should it compare against the latest team before each move?") | Against the draft, the moves' credit grows as the season goes (the drafted roster never fills a hole), moves can't be told apart, and an add's cost is ignored. Each move with its swap undone, over its first 3 weeks (the plan's horizon), against the forecast the plan made for those weeks and the add price, is the engine's calibration check. An add into an open spot carries the player a later IR return cut (Pinto kept Lindell from Oct 12). The draft comparison stays as one line, "against doing nothing" |
| 2026-10-21 | **Opponents' rosters stay frozen for the week: no streaming adjustment** (Nico asked for one; the evidence says no) | `scripts/check_forecasts.py`, weeks 1-3, all 48 team-weeks: every team beats our forecast by ~22 pts a week (+23.0, +21.5, +21.4: a level bias, which cancels in a matchup), the misses' spread matches our sd (27-34 vs 28-32), a team's adds that week don't predict its miss (-1.4 +/- 7.5 pts per add, within weeks; -0.6 +/- 6.4 by count), and neither do its misses in other weeks (correlation -0.38). Week 2's opponent (+56) made no adds. Re-run it as weeks come in (Standings screenshots each Monday); an effect beyond 2 SE reopens this |
| 2026-10-22 | **The look: night style everywhere, one plan per screen** (Nico, after mockups of three directions): the Telegram card is card B (dark, the win odds as a ring, today's move as a face-off); NHL headshots and logos loaded from NHL's image server (not stored); the dashboard becomes bottom tabs in the same style; Telegram text gets bold and a few emoji markers; the move card shows fantasy points a game (so far, projected), not ice time or games played; schedules cover this week and the next two, the league-wide streaming table last | Readability on a phone: the old card showed at ~75% with 12 px notes, and the dashboard was one 6,900 px page |
| 2026-10-22 | **The final score shown is the one the win odds read** (Nico: "wouldn't the adjusted final score be more legit", after "expect 210-207" next to win 45%) | 61-94 down, the raw projections said 210-207 (+36 still to come), but P(win) counts that +36 at 0.80 (`check_sigma`): a margin of -4.7. The message's "expect", the card's and dashboard's "→" and the race chart's ends now show 206-211 (`matchup.final_scores`); the "Why 45%" card says how the projections add up and why they're read smaller. Display only: the odds, the moves and the logged forecasts (`results`, `league_weeks`, raw, for the calibration checks) are unchanged |
| 2026-10-23 | **Later points by tenure: each later day's gain x P(the add is still mine then), over the whole season left** (Nico: "not an arbitrary value deciding whether value should count only short term or the full season"; then "Bot's policy, step 0 first", "Yes", and "why shouldn't we already use the new model as the replay confirmed it?": live without the two weeks in shadow) | The audit (step 0, "Replayed"): the keep share gave the season to the adds that left soonest (keep 1, mostly goalies: 5.2 weeks held, half their later points realized) and the 6-week rate barely carried over (slope 0.15). Tenure (`engine/tenure.py`, `matchup.tenure_horizon`), replayed against the live rule: +0.38 (0.11) wins a season on the fitting leagues, +0.23 (0.10) on new ones, fewer adds. Replaces the keep share, the hold, the 6-week rate x the weeks left and `LONG_RUN_DISCOUNT`. Live on Oct 23: Nurse for Larsson 26.0 -> 18.9 win-pts (later 18.2 -> 9.8 pts), Pospisil for Larsson 17.2 -> 9.7 (later +0.6 -> -3.1: a forward for my 4th D leaves a D slot empty all season), and Kantserov for Landeskog comes up at 22.0 (Landeskog, likely streamed, is now cheap to lose): the plan became Kantserov for Landeskog now, Nurse for Larsson Monday. `addprice.BAR` stays 0.12: tenure's second-best Monday values in the replay run 0.95x (weeks 3-5) / 0.80x (6-12) the old ones, and at the same bar it spent 28.0 adds against 29.2; the pacing lowers the price if adds go unspent. A plan run still takes ~1 min (the long run's per-player days cached). Next: its later points realize 1.44x (its audit), a later weight above 1 to replay |
| 2026-10-23 | **A two-week stream's weeks ahead keep tenure's survival** (Nico asked why Pospisil for Larsson fails though his weeks 5-6 are so good, then "Test it now"; then whether a replaced streamer's successor is counted, "Test it") | Replayed ("Replayed"): counting the weeks ahead in full, for every add or only for streams whose gain falls off after them, loses to tenure on both seeds (together ~-0.07 and ~-0.11 wins a season, +/- 0.05), and at full weight weeks +1 / +2 come true at only 0.72-0.88 of predicted (with survival 0.86-1.15). With Thursday decisions too, so the next streamer is played out mid-week: ~-0.13 and ~-0.11 (0.05). The successor's points belong to his own move, which costs its own add. Pospisil for Larsson stays at 9.7 win-pts, under the 13.1 bar |
| 2026-10-27 | **The full plan every day, in the half hour before Yahoo's day turns** (Nico: "30-15 min before day changes in Yahoo I want the automatic /week report daily") | Yahoo's day turns at midnight Pacific (its transaction deadline is 11:59 PM PT): 09:00 Helsinki in summer time, 10:00 from Nov 1. The plan, cards and dashboard are as on /week; a daily plan on Monday or Wednesday stands for that noon's. An exception to "never resend what was sent" by Nico's call |
| 2026-10-27 | **Tonight's starting goalies read every run 19-22 local; any news re-weighs the plan quietly** (Nico: "starting at 19 and up until 22 my time I want the bot to check gamedaytweets/goalies"; GamedayTweets is behind a Cloudflare bot check, so Nico picked DailyFaceoff, already the source) | The morning skates' confirmations come out 12-15 Eastern. A new, changed or newly confirmed starter re-runs the plan (a message only if it changes, or a goalie add waiting on his start can go); the briefing reads the same fresh copy. An input, not a valuation change |
| 2026-09-29 | Optimize P(win this week), not season points | H2H: only beating this week's opponent counts |
| 2026-09-29 | ~~**Always keep 3 goalies** (policy A, Nico)~~ Superseded 2026-10-02: goalie count by value | Season sim: 2G + a skater = +35 pts, but 2.5 vs 1.1 weeks with goalie points zeroed, plus goalie streams cost adds |
| 2026-10-02 | **Two goalies or three is judged by value** (Nico: "based on analytical estimates"); never fewer than two | The long run now checks the goalie minimum each week (it had checked a 6-week block: "100%") and weighs a goalie lost for the week (`availability.GOALIE_KEEP`, fit on game logs), so a third goalie is kept, dropped or added like any move. `scripts/sim_goalies.py`, 2025-26, 8 leagues x 16 teams: by-value + like-for-like swaps +0.05 wins a season vs always-3 + swaps (+/- 0.10), carrying 2.87 goalies; always-2 -0.65 (+/- 0.18) vs always-3, -1.01 with swaps, with twice the weeks zeroed. So by value is as good as three and clearly better than two |
| 2026-09-29 | Long run is judged by a whole-lineup projection, not per-player value | The per-player shortcut dropped D into a thin D group and chased injury-prone forwards |
| 2026-09-29 | Upgrades that cost > 1 pt this week wait | They can be made next week at no cost |
| 2026-09-29 | ~~Long-run edges must show within 2 weeks~~ Superseded 2026-10-01 by the add price | Tiny edges multiplied over a season are noise (e.g. goalie-for-goalie swaps); now handled by the 6-week long run and streaming-spot holds |
| 2026-09-29 | Keep 6 adds for playoff weeks 24-26 | Playoffs decide the title, and Yahoo's 36 covers the entire season incl. playoffs (settings page). The regular-season pace is 30 adds / 23 weeks. Checked 2026-10-11 against the prize split (1st in the regular season 25%, champion 45%, runner-up 20%, third 10%; `config.league.PRIZE_SHARES`) with `scripts/sim_reserve.py`: keeping all 6 earns the most in every case; a smaller reserve -0.9 to -4.0% of the pool a season (average team), releasing them when P(playoffs) < 15% from week 13 -0.11 (weak team -0.26), weighting adds by playoff leverage -0.7 (Nico asked whether a bad start should free them) |
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
| 2026-10-28 | /trade charges an add the trade needs the add price (Nico) | Its free agent's points came free, so a 2-for-1 looked better than the 1-for-1 inside it by a swap the plan fails (Stamkos, McBain for Eichel +1.1 vs Stamkos for Eichel -0.6, the gap McBain -> Pospisil). Leaving the spot open was tried and dropped: it reads as an empty starting slot most nights (-14.5 a week), not as the add it would get. The price converts at a typical week's worth of a point, spread over the weeks the plan would count the gain (`trade.counted_weeks`: the rest of the week, then each week to the playoffs x tenure's survival for where the best incoming player would sit x how far-off gaps realize). Checked the same day: the worth of a point matched weeks 1-3's real margins (model 0.0083-0.0097, realized 0.0093, SE 0.0013); a flat 6 weeks overcharged keepers 1.6-2x (lowest skater 3.5 weeks, 4th-6th 9.6, 7th+ 12.4). Live: Stamkos, McBain for Eichel -0.4 (Eichel 7th+, 12.4 weeks), McBain, Landeskog for Cooley +1.5 (Cooley my 2nd lowest at C, 5.6 weeks) |
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

## Replayed (`scripts/replay.py`, 2025-26, 2026-10-11)
8 leagues x 16 teams, every team in turn managed by this valuation (the others lazily), each using its
30 regular-season adds; wins a season vs the live settings (+/- SE, paired). Simplified: single NHL
positions, Monday decisions, no free-agent competition, no IR slots; goalies not streamed until 2026-10-20
(since: goalie moves too, each week's goalie points x P(3+ starts), starts at the last-10 share).
- `KEEP_RAMP` 1.0: 0.5 -0.07 (0.07), 2.0 +0.03 (0.06), the old switch +0.00 (0.12). The width doesn't matter.
- `STREAMING_SPOTS` 3: 2 -0.07, 4 +0.08, 5 -0.05 (0.12-0.13). The count doesn't matter.
- `LONG_RUN_DISCOUNT` 0.5: 0.35 +0.05 (0.04). No difference.
- Later points: every swap credited the season +0.24 (0.13), +1.5 pts a week; every swap only the
  streaming hold -0.11 (0.08). Under 2 SE each, but no sign later points weigh too much.
- Adds themselves: no swaps -4.1 wins a season (0.23) at the bot's price, which spent 19 of 30 adds
  here (all 30 used: 15.65 wins vs 15.49, same leagues; not paired-tested).
- Planned exits (Nico, 2026-10-13: add Landeskog for week 4, stream him out before his weak week 5),
  16 leagues: a streamer's hold may end after this week or a week ahead, then he's offered as a drop.
  At the 0.09 price, free exits +0.19 (0.09), charged the unused share of an add +0.07 (0.07), but
  spending 21.4 and 19.5 adds vs 18.8. At equal adds (price 0, all ~30 spent) free exits -0.05 (0.09),
  with 9.8 planned and 6.2 made a season. So the gain was the extra adds, not the exit: the full hold
  stays. Untested: mid-week exits (the replay decides on Mondays only).
- Keeper add (Nico, 2026-10-20: Meriläinen for Larsson, keeping Pinto, got the hold's 4 later points to
  Meriläinen for Pinto's 43): the keep share on the add's gap alone, so a keeper added onto a streaming
  spot counts the season. 16 leagues: at the 0.09 price +0.23 (0.09) wins a season, +1.4 pts a week, with
  fewer adds (17.8 vs 18.8), so not the extra adds; at equal adds (price 0, 29.9 each) +0.07 (0.08), +0.9
  pts a week. Skater swaps only (the replay streams no goalies). Live since 2026-10-20 (Nico).
- With goalie moves (2026-10-20; the reference is now the live rule, keeper add), 16 leagues: at the 0.09 price
  the old rule -0.18 (0.12), skaters only -0.61 (0.14) wins a season (-3.0 pts a week, 17.4 adds vs 24.9);
  at equal adds (price 0, ~30 each) the old rule -0.04 (0.13), skaters only -0.52 (0.13) (-2.7 pts a week).
  So goalie moves are worth ~0.5 wins a season beyond the adds they use; keeper add helps at the bot's
  price and costs nothing at equal adds.

- The price rule (Nico, 2026-10-21: "pacing to leave 6 for the playoffs; the better the move the better to
  spend; why should the candidate pool move the price?"). The live price (`addprice.solve` each Monday over the
  pools logged so far) against a fixed bar x (even pace / the pace the budget left needs)^k, both pacing 30
  adds by the price alone. 16 leagues, vs the live price: bar 0.08 +0.12 (0.11) wins a season, 0.10 +0.15
  (0.11), 0.12 +0.19 (0.11, 26.8 adds), 0.08 with k 2 +0.22 (0.11, 29.2 adds); every pacing variant ahead, the
  last at 2 SE. The live price averaged 0.27 but by thirds of the season 0.61 / 0.16 / 0.02 (a few early pools
  price high, then it dumps the rest late) and moved 0.054 Monday to Monday (1.62 at most); the pacing bars
  stay ~0.09-0.12 all season and move 0.009-0.019. The old fixed 0.09 with an even-pace cap (8 leagues): +0.02
  (0.16), 25.3 adds. Live since 2026-10-22 at bar 0.12 (decision log). The replay's bar doesn't carry over to the live bot: its
  move values run 2-3x the live ones at this stage (Mondays, weeks 3-5: best 0.72, second best 0.30, medians;
  live, each day's first plan Oct 9-22: ~0.24 and ~0.11), as its rosters, drafted on preseason projections
  with no IR and lazy injury handling, leave holes early; they fall to 0.11 / 0.05 after week 12. What
  carries over is the shape: a pacing bar no worse than the live price, and spending early when moves are
  rich didn't cost. A live bar of ~0.12-0.13 would have made ~1.3 adds a week on the live plans so far.

- The audit (2026-10-23, step 0 of tenure, `scripts/replay.py --audit`, 16 leagues at "pace 0.08 k2", 7,474
  moves): each move's predicted parts on its Monday against what it brought while it stood (the real score
  against the roster with that swap undone, until the add left). Points per move, predicted / realized
  (SE): this week 6.15 / 5.63 (0.15), week +1 4.56 / 3.29, week +2 3.88 / 3.30, later 23.4 / 17.7 (0.6).
  By keep share (n; later predicted / realized; weeks held; still held at 6 and 12 weeks): 0, 1,811, 5.1 /
  5.7, 4.4 wks, 45% / 17%; 0-0.5, 1,778, 9.0 / 18.0, 8.4, 73% / 45%; 0.5-1, 757, 25.9 / 34.3, 11.3,
  83% / 60%; 1 (a goalie add is a keeper), 3,128, 41.7 / 20.5, 5.2, 29% / 17%. Goalie moves (3,832):
  34.4 / 17.8, 5.4 weeks. Swaps that change position realized 1.7-2.9x their (small) later points. By
  the 6-week rate's quartile, predicted / realized a week from week 3: 0.6 / 1.2, 2.5 / 2.3, 4.5 / 3.9,
  9.1 / 4.6; slope 0.15 (0.04). In wins, value / realized: under 0.1 0.07 / 0.08, 0.1-0.2 0.15 / 0.15,
  0.2-0.4 0.28 / 0.24, 0.4+ 0.89 / 0.39 (0.02). So the average hides offsetting errors: the season
  credit goes to the moves that leave soonest (goalies), the schedule window is mostly noise for the
  weeks after, and the top values run ~2x high (partly the best of many candidates, which any estimate
  would show). The replay's caveats stand (its values run 2-3x the live ones early). No rule changed.

- Tenure (2026-10-23, roadmap item 8 steps 1-2; `scripts/replay.py` variants "whole schedule" and "tenure",
  against "pace 0.08 k2", the live valuation at the live-like price; 16 leagues each). Step 1 alone, the long
  run from the whole season left (keep share and hold as live): -0.12 (0.11) wins a season, so no gain, though
  its later points come truer (0.91 of predicted, slope on the weeks after 0.23). Steps 1-2, each later day's
  gain x P(the add still stands) by where he'd sit on my roster (`engine/tenure.py`, Kaplan-Meier on the
  reference's 7,474 moves: a skater who'd be my lowest by waiver gap 42% still there at 3 weeks, 7% at 12;
  4th-6th lowest 85% / 45%; 7th+ 94% / 66%; my weakest of 3+ goalies 36% / 11%, another goalie 51% / 16%) x
  how far-off gaps realize (0.84 / 0.76 / 0.74), no keep share, hold or discount: on the leagues the curves
  were fit on (seed 11) +0.38 (0.11), +3.1 pts a week, 28.0 adds vs 29.2; on new leagues (seed 23) +0.23
  (0.10), +2.2 pts a week, 27.9 vs 29.2. Its own audit (seed 23): values of 0.4+ realized 0.44 for 0.67
  predicted (the live rule 0.39 for 0.90), 0.2-0.4 0.20 for 0.29; later points now under-predicted (14.1 /
  20.4), so a later weight above 1 is the next thing to try. The curves refit on its own moves stay close
  (lowest 0.37 at 3 weeks, 4th-6th 0.82, goalies 0.42 / 0.51). Live since 2026-10-23 (Nico, decision log).
- A deliberate two-week stream (2026-10-23, Nico: "how come Pospisil for Larsson fails as Pospisil's 2-week
  schedule is so advantageous?", then "Test it now"): the weeks ahead at full weight instead of tenure's
  survival, for every add ("tenure full ahead") or only when the add's gain a week there beats his gain a
  week after ("tenure stream ahead"); later weeks as tenure. Against tenure, 16 + 16 leagues (seeds 11 /
  23): full ahead -0.11 (0.08) / -0.03 (0.07), together ~-0.07 (0.05); stream ahead -0.09 (0.07) / -0.13
  (0.07), together ~-0.11 (0.05). Neither helps, and the audit says why: with survival, weeks +1 / +2
  realize 0.98 / 1.15 and 0.86 / 1.13 of predicted; at full weight 0.88 / 0.85 and 0.72 / 0.86, so an add
  does leave early often enough that the discount is right. Tenure stays as it is (decision log).
- With mid-week streams (2026-10-23, Nico: "if a streamer gets replaced he will be replaced with a new
  streamer who will then have a favorable schedule - is this accounted for?", then "Test it"): the replay
  also decides on Thursdays ("... midweek": the week's adds left, valued on the days left with the points
  banked, each night scored with that night's roster). Against "tenure midweek", seeds 11 / 23: full
  ahead -0.15 (0.07) / -0.10 (0.07), together ~-0.13 (0.05); stream ahead -0.14 (0.07) / -0.08 (0.07),
  ~-0.11 (0.05); weeks +1 come true at 0.64-0.71 of predicted at full weight. So with the next streamer in
  the slot played out mid-week too, the weeks ahead still keep tenure's survival. And deciding on
  Thursdays too is itself worth +0.23 (0.08) wins a season on both seeds (tenure Monday-only vs midweek),
  28.7 adds vs 27.9: the live bot plans every day already, so this backs what it does; replays from here
  should use "tenure midweek" as the reference, being nearer the live bot.
- The margin's shrink by the days left (2026-10-25, Nico: "test the Sunday margin shrink in the replay,
  and other days than Monday", after Guhle's 5.8 win-pts on a Sunday at 7.5%). `check_sigma --from-day`
  (16 leagues, 2025-26 / 2024-25): the spread of the days left is right every day (Sunday x1.07 / x1.02,
  tails normal); the margin of the days left realizes at 0.81-0.88 by the day over all weeks, but at
  0.90-0.98 from Friday on once both goalie minimums are met (Sunday 0.93 / 0.95) and 0.71-0.83 while
  one is open. Replayed, 32 leagues against "tenure daily" (a decision every day, as the live bot plans):
  the margin by days left +0.01 (0.02), 0.94 once both minimums are met -0.04 (0.02), Mondays and
  Thursdays only -0.13 (0.06). `MARGIN_REALIZES` 0.80 stays every day: reading a decided week's margin
  more exactly changes few moves and wins nothing. Deciding every day beats Mon + Thu by ~2 SE, as the
  live bot does; replays from here use "tenure daily" as the reference.

## Judgment calls (untested)
`addprice.BAR` 0.12 (set on two weeks of live plans; re-check mid-December), `MODEL_SD_SHARE` 0.08, `addprice.DEFAULT_TAU` 20 (until rosters give a spread),
`PLAYOFF_RESERVE` 6, `MIN_GOALIES` 2 (the floor), `MIN_WIN_GAIN` 0.02 (display only), `LONG_RUN_WEEKS` 6 (/trade only since tenure), `STREAM_WEEKS` 2 (the "next two weeks" shown), `tenure`'s bins (by rank on my roster: replayed as a whole, the cut points not tuned), `STREAM_BELOW` 0.25 (wording only), `KEEPER_WAITS_BELOW` 1.0, `plan.CHANGE_MARGIN` 0.02, `CONCEDE_BELOW` 0.10 / `COAST_ABOVE` 0.90 / `EVEN_WITHIN` 0.03 (wording only; an even week also shows the biggest swing), `trade.MIN_GAIN_PER_WEEK` 1.0, `trade.PICK_DECAY` 0.8 (how a draft round "feels" to a manager), `trade.PERCEPTION_GAMES` 20, `trade.add_cost` (the 6-week rate standing in for the gain's later rate; the incoming player's tenure for the whole gain, the free agent's share included), `availability.DTD_RETURN` (0.85 then 0.97; fit from the DFO archive once it has the cases), `matchup.RELIEF_SHARE` 0.5 (only its order matters). Revisit when we have in-season results.
