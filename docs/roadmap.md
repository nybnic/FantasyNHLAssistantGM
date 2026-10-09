# Roadmap

Pick the top item unless Nico names another. Each item has a short "done when".
Move finished items to the log at the bottom, with the commit.

## Direction (2026-10-09, `docs/plan-2026-10-09.md`)
Back to the core: one engine, one ranked add/drop table, one source of truth.
inputs (ledger) -> **Board** (one per run, archived) -> rule -> views (Telegram, dashboard).
Moves are scored in expected wins over this week and the next two (played against
the real opponents and schedules) plus shrunk later points; the add price is in
the same unit. A committed plan changes only when it can't be done or something
beats it by 0.02 wins, and says why. Telegram is for the action (4-6 lines, cards
once), the dashboard (HTML, its screenshot as the Telegram image) for evaluating.
(Earlier direction, 2026-10-01: the edge is the add decision; still true.)

## Next (Nico, 2026-10-09: D1-D5 as proposed)
1. **Board + ledger, no behavior change**: done (see the log). The messages still format the moves
   themselves; step 3 rewrites them on the Board.
2. **Honest values**: done (see the log). The error band isn't a step-2 number after all: near-term gaps
   are calibrated (`check_gaps` 1.02), so what flips the advice is day-to-day change, which the
   commitment margin (step 3) handles.
3. **The rule and the messages**: done (see the log). Not done: replaying week 2's real inputs (they'd
   need each day's NHL data as of then); the flow is tested on near-ties, a taken player and a move made.
4. **Visuals**: built (see the log). Done when Nico OKs the look on his phone: the next plan message
   carries the card, and the dashboard is at https://nybnic.github.io/FantasyNHLAssistantGM/.
5. **One replay harness** (2025-26): expected wins, playoff/title odds, plan changes a week, adds used;
   every later rule change ships with its numbers.

Parked: /trade improvements, stash candidates (old item 8). Old items 4 (points for), 5 (opponent
streaming, into step 2 once data exists) and 6 (add-price calibration, mid-November) stand.

### Previous Next (2026-10-03)
Nico, each Monday: League > Transactions, Standings and All Matchups screenshots (All Matchups
before the first game, the new week at 0.00: Yahoo's forecast for all 16 teams). See `docs/data.md`.
1. Logging: weekly xFP snapshot, league moves, every team's weekly score and Yahoo projection
   (`league_weeks`), our forecast for all 16 teams, Yahoo's first forecasts: done. The private data
   archive takes writes (the bot's first push, a19b7a4, 2026-10-03), checked every run by a dry-run push.
2. Projection accuracy. Role blend (no change), the age curve (youth boost removed) and opponent/arena
   effects (no gain, not modeled) done, see the log. Left: margins realize at 0.84 (projected
   differences a bit too wide), and healthy availability (0.96 -> 0.89 over six weeks). Done when:
   backtested on both seasons, `check_sigma` re-run.
3. Message layer out of `engine/matchup.py`, with a whole-message consistency test.
4. Points for: Nico to check in Yahoo whether points for breaks standings ties. If it does, a point
   in a decided week is worth ~1/5 of one in a typical week (0.022 vs 0.12 pts of playoff odds per
   point, simulated 2026-10-03), not zero as the add price counts it. Then add that term to the add
   value and keep it only if `scripts/sim_leverage.py` shows title odds rising.
5. Opponent streaming (needs 3-4 weeks of transactions).
6. Add-price calibration (mid-November): `sim_add_policy --logged`, and re-run `sim_leverage` with the
   logged weeks and real standings (the leverage verdict rests on one week of candidates).
7. Season layer: check our team strengths against Yahoo's weekly projections (`league_weeks`, 3-4
   weeks in); then show a trade's effect on playoff and title odds in /trade.
8. Stash candidates (P0 evidence -> P1 role-aware projection -> P2 candidates with option
   value, plus "Watch" heads-ups (Nico: yes) -> P3 scorecard). No youth bonus (decision log).

## Later
- Stream timing in close weeks: add Monday (more games) or wait for Wednesday (you know the score)?
  Simulate both policies over 2025-26 weeks before changing the rule (Nico asked, 2026-10-03).
- Verify in Yahoo: what Start Active does with injured players and on an overflow night
  (the briefing assumes it starts anyone with a game, the better season value first);
  when a claim of a player dropped today can play (the bot assumes 2 days
  later: the "W (date)" next to him), the add limit and goalie minimum in the week-19 double week
  (dates confirmed 2026-10-01), regular-season ties and standings tiebreakers
  (Next, item 4), the consolation bracket.
- Late regular season: weigh playoff seeding (the higher seed wins tied playoff
  weeks) when planning weeks 20-23.
- Projections overstate differences (`scripts/check_sigma.py`, aged priors since 2026-10-03:
  margins realize at 0.84; teams now -0.01 sigma, unbiased): healthy skaters' falling
  availability (0.96 -> 0.89 over six weeks) is the next suspect, before shrinking add gains.
- Calibrate `MODEL_SD_SHARE`, the streaming-spot counts and the add price once 4-6
  weeks of real results and logged candidates exist (`scripts/sim_add_policy.py --logged`).
- IR: read Yahoo's own injury tag (IR, IR-LT, O, DTD) from screenshots instead of
  inferring it from DFO. (The spot crunch when an IR'd player returns: done 2026-10-09.)
- Opponent profiles: being logged since 2026-10-01 (`league_adds` from Transactions;
  Yahoo's score vs the box scores' best lineup in `results[week]["live"]`). Once 3-4 weeks
  show an effect, shade opponents' projections (inactive managers, heavy streamers).
  Nico (2026-10-02): wait for data, no league-wide prior. Today the plan credits my
  adds but freezes the opponent's roster, so shown win odds run high when they stream
  (one ~4-5 pt add moves a close week ~5 pp; matters most in weeks I lead by 15-30).
  Shape: per team, logged add rate x a typical streamer's gain for the days left x
  a discount for less careful picks (judgment call), minus their lineup gap (Yahoo vs
  best lineup); only adds not yet visible on their roster; widen sigma a little.
  Check against projections already running high (`check_sigma`). Needs Transactions
  screenshots weekly (`league_adds` was empty on 2026-10-02). Done when: per-team
  shading backtested on the logged weeks, and `explain_week` shows it.
- Move the message text out of `engine/matchup.py` (as bot/ did for main.py).
- Speed: the move search spends most of its time in the lineup solver (2,100 distinct
  days a plan, ~6 ms each); a faster solver must keep its tie-breaks (open slots).
- Streaming-goalie advice (only if the 3-goalie policy changes); Yahoo projected
  GP as a durability input; next year's draft (restore from tag `draft-2026`).

## Decide (Nico)
- The repo and dashboard are public, so league-mates could see suggested adds.
  Accepted for now (review, 2026-10-01); revisit if anyone learns of the bot.

## Dropped
- Goalie-minimum alerts beyond the weekly line: Yahoo already warns.

## Log
| Date | Done | Commit |
|---|---|---|
| 2026-09-29 | Weekly matchup plan, league data, /opp /taken /week, age curve | 812f9e6 |
| 2026-09-29 | Duplicate-tap race fix, token-safe errors | ec1ceda |
| 2026-09-29 | CLAUDE.md docs, explain_week, compare_yahoo | 75f63ec |
| 2026-09-29 | Draft tooling removed (tag `draft-2026`) | 5b5df12 |
| 2026-09-29 | League rules doc + test keeping it in sync with config | fe22d39 |
| 2026-09-29 | Yahoo settings page transcribed into the rules doc | e331164 |
| 2026-09-29 | `/trade` command (and `main.py --dry-run --trade "A for B"`) | 74fa6db |
| 2026-09-29 | `/trade` suggestions, roster balance and short-of-starters check | d9fe005 |
| 2026-10-01 | Evening briefings missed: relay crons start the runs | e3e1c8e |
| 2026-10-01 | `/myteam` paste replaces my roster and slots; briefing window 19:30-20:30 | d9fe005 |
| 2026-10-01 | Roster and opponent screenshots via free OCR (`clients/screenshot.py`) | d9fe005 |
| 2026-10-01 | Decided-week rule: no adds chasing a lost (<10%) or won (>90%) week; roadmap re-planned around the add decision | 9a2654c |
| 2026-10-01 | Matchup screenshots: both rosters, slots and Yahoo's live score; fix: an added player's earlier games no longer count as gain | d9fe005 |
| 2026-10-01 | Mid-week stance (chase / protect / concede) with the biggest-swing add; badge colours read at Telegram's photo size | 60752a3 |
| 2026-10-01 | Telegram charts: win odds per add + score race, schedule grid (this week and next: starts, benched games, open slots by position), per-add weekly gain + add budget | b62cd6b |
| 2026-10-01 | Decision map replaces the win-odds chart; best streamer per position on the schedule grid; schedule-fit shortlist | 9b22d8b |
| 2026-10-01 | Drops tried per group (2 lowest forwards, D, goalies): found Ehlers for Schenn | a7da318 |
| 2026-10-01 | Web dashboard (site/, GitHub Pages): tap an add for its week-by-week gain and budget; grid with streamers; table of every add | 6edd0b2 |
| 2026-10-01 | Long run over 6 weeks; streaming spots judged on the next 2 weeks | 2acaa74 |
| 2026-10-01 | League > Transactions screenshots update every roster; Taken button on adds | d1bb801 |
| 2026-10-01 | Coherent plan: this week's adds to moves that pay now (keepers wait for Monday), true biggest swing, verdicts that say why; Yahoo position eligibility learned from screenshots | 0fd4429 |
| 2026-10-01 | Yahoo positions seeded for 559 skaters (`scripts/seed_positions.py`) | aeb0ec2 |
| 2026-10-01 | Streaming-spot adds valued over the expected hold (~3 weeks), weakest goalie included | ef5f857 |
| 2026-10-01 | Add price in win probability: 0, 1 or 2 adds a week by closeness and the adds on offer; season sim vs the old rule | 11cfe36 |
| 2026-10-01 | Review fixes: the add budget counts every add (Done, Transactions, roster screenshots; one add once), in the week it was made; a failed weekly plan stays due; one failing step no longer stops the others; stale cache when a refetch fails; week 19 dates confirmed | (this commit) |
| 2026-10-01 | Injury status fades along return curves fit from game logs (`scripts/fit_absence.py`), slower the more games missed; check_sigma replays the bot's availability: bias -0.36 -> -0.13 sigma | (this commit) |
| 2026-10-01 | Waivers per player: drops (Transactions, Done) put a player on waivers; his add is a claim that counts from the day it can play, and says so | (this commit) |
| 2026-10-01 | Banked points by day: each day's rosters kept until its first puck; points so far scored with them, so mid-week adds and drops don't move them | (this commit) |
| 2026-10-01 | IR management (`engine/ir.py`): move an injured starter to a free IR slot (the plan's adds then need no drop, Done makes the move), and a player back from IR needs a spot; in the weekly plan and once in the briefing | (this commit) |
| 2026-10-01 | Results log (`state["results"]`: each week's first and latest plan, then its result; week 1 from git history) and the week's result report with a score-race chart: Monday noon, before the new plan (Sunday evening is too early: Sunday's games end after midnight Helsinki) | (this commit) |
| 2026-10-01 | Suggestion scorecard (`engine/scorecard.py`, `scripts/scorecard.py`): each add's raw points vs its drop over 14 days, made or skipped; a line in Monday's result. Decisions now name their players (past ones from git history), and untapped suggestions are logged | (this commit) |
| 2026-10-01 | Data check (`clients/health.py`): a failing or stale source (DFO, NHL) gets one Telegram line a day saying what it means for the advice | (this commit) |
| 2026-10-01 | Evening briefing as a Start Active diff (one line when it's fine, else the benches and starts that beat it, with why); an evening news check for adds that newly clear the price; the plan leads with the action | (this commit) |
| 2026-10-01 | "Other drop" button on adds (records the add, asks who was dropped); naming a playoff opponent with /opp sends that week's plan | (this commit) |
| 2026-10-01 | Opponent profiles logged: other teams' adds (Transactions) and Yahoo's score vs the best-lineup box score on each screenshot day | (this commit) |
| 2026-10-01 | Requirements pinned to CI's minor versions (numpy now explicit); the lineup solver's results cached (move search 24 -> 21 s, same output; profiled: the solver, not the projections, is the cost) | (this commit) |
| 2026-10-01 | main.py split into bot/ (ingest, weekly, daily, common); tests and scripts import from there and run offline (checked with sockets blocked) | (this commit) |
| 2026-10-01 | Fix: matchup Totals screenshots no longer add dropped players (slotless rows), short screenshot sets keep the players they miss, a recent player of mine isn't a new add; the week-1 phantom Schenn add and lost Murashov repaired (`state/repairs.py`) | (this commit) |
| 2026-10-01 | Murashov's drop recorded (the earlier repair had wrongly restored him); a drop of mine clears him from `seen_mine`; repairs run once each | (this commit) |
| 2026-10-02 | Goalie count by value: the long run checks the minimum each week and weighs a goalie lost for the week (GOALIE_KEEP, fit); no fixed 3-goalie rule (floor 2); the plan says whether a third is worth an add; replay: by value = always-3 (+0.05), always-2 -0.65 wins | (this commit) |
| 2026-10-02 | Fix: transactions screenshots from the website (full names, Eastern times) and the league chat ("hier à 18:48", "13m ago") are read; they were rejected. One move seen in two layouts is applied once (same team, type and players within 12 h) | (this commit) |
| 2026-10-02 | Fix: with this week's adds spent, free agents join Monday: the streamers (text, chart) and every move count only next week's games, and a deferred add's drop (also a waiver claim's) keeps playing until the add joins. Staal for Stamkos had shown +4.6 this week from 6 open-slot games he couldn't play | (this commit) |
| 2026-10-02 | Goalie streamers show expected starts next to games in open slots (Silovs: 3 games, ~1.9 starts); the points were already weighted by start odds, the game count read as if he'd play all 3 | (this commit) |
| 2026-10-03 | Goalie start odds use the team's last result: after a loss the same goalie's odds x0.62, after a blowout (5+ GA or pulled) x0.32, a win x1.0, for the team's next game (`scripts/fit_goalie_starts.py`: fit 2023-25, 2025-26 0.96/0.63/0.35, log loss 0.651 -> 0.625). Back-to-back repeat 0.35 -> 0.15 (data: 0.09/0.13/0.16). Knight after Soderblom's blowout: 62% -> 81%. sim_goalies rerun: by value still +0.03 vs always-3 (+/- 0.14), always-2 -0.81 | (this commit) |
| 2026-10-03 | Age curve: youth boost removed (fit on 20+ GP survivors, it over-projected under-24s by +0.09 / +0.18 pts/game on every skater); best MAE in both seasons and horizons, pairwise equal or better. `check_sigma` ages priors like the bot: teams -0.13 sigma unaged -> -0.01. Celebrini 9.0 -> 8.3, Bedard 6.5 -> 6.1 | (this commit) |
| 2026-10-03 | Role blend backtested (`backtest --season --horizon --role-grid --all-players`): half-life 6 / prior 3 kept, nothing beats it in both seasons; after an ice-time jump the model under-projects ~0.25 pts/game next week, but reacting faster only chases noise. Found: fringe skaters over-projected | (this commit) |
| 2026-10-03 | DFO line archive: all 32 charts once a game day (~1,300 rows) to the private `nybnic/FantasyNHLAssistantGM-data`; skips until the deploy key secret exists | (this commit) |
| 2026-10-03 | Logging: each fantasy week's first run appends every NHL roster player's projection and owner to `state/xfp_log.csv` (~54 KB a week); the league-moves log keeps a season (3000, was 300) | (this commit) |
| 2026-10-03 | Fix: the stance read "ahead (50%), protect the lead: you're 0 expected points up ... make only the adds listed below" with no adds left. Now "Rest of the week"; within 3 pp of 50% it's "dead even" (and shows the biggest swing like a chase); with no adds left it doesn't point to adds or say chase | (this commit) |
| 2026-10-03 | League freshness from Transactions: `moves_through` dates every roster (they had kept the draft date, so the plan asked for /opp after Transactions had updated them); the plan asks for Transactions screenshots when moves are 2+ days old; other teams' adds before Oct 1 backfilled into `league_adds` (4 -> 13) | (this commit) |
| 2026-10-03 | IR stashes: an injured free agent straight into an empty IR/IR+ slot (Yahoo allows it), valued on the return curves less the later drop weighted by his odds of being back; priced like any add; Done puts him in the slot | (this commit) |
| 2026-10-03 | /trade judged over 6 weeks (was 2: one week's schedule could decide it), like an add's long run; suggestions guess acceptance by a "feel" round that blends the draft round toward this season's fantasy-point rank (`PERCEPTION_GAMES` 20, a judgment call). Measured: 32 s on GitHub (Check workflow) | (this commit) |
| 2026-10-03 | Season odds (`engine/season.py`): playoffs, title, and this week's leverage vs a typical week left, simulated from team projections and standings (even until Standings screenshots are read); a line in the plan. Leverage on P(playoffs), paired: on the title it was noise (0.76-1.12x for one setup) | (this commit) |

| 2026-10-03 | Simulated (`scripts/sim_leverage.py`, whole league, paired seasons): leverage-weighted adds raise playoff odds +0.6-1.6 pts but cut title odds 0.3-1.0 (on P(title): -1.0-2.6); playoff-week points x2: title -0.4-0.9. All disturb the even late-season spending whose adds carry into the playoffs. Add price unchanged | (this commit) |
| 2026-10-03 | Opponent and arena effects on skaters backtested (`scripts/backtest_venue.py`, both seasons): no gain beyond noise (2025-26 slightly worse), so not in the model | (this commit) |
| 2026-10-03 | Standings and All Matchups screenshots read (League tab, OCR checked on Nico's): standings start the season odds, the week's pairings replace random ones, every team's score and Yahoo projection logged (`league_weeks`); today's screenshots recorded by a repair | (this commit) |
| 2026-10-03 | /trade: `PICK_DECAY` 0.85 -> 0.8, stars feel worth more (Nico); a 1st now outweighs a 4th + 6th. Points-for value measured: 0.022 pts of playoff odds per point vs 0.12 through wins (roadmap item 4) | (this commit) |
| 2026-10-03 | /trade relabeled: "typical week's win odds +7%" and "pts/week over the next 6 weeks" instead of "win 50% -> 57%", which read as this week's odds (Nico). A season-long horizon was pushed and reverted the same hour: Nico asked for the label only | (this commit) |
| 2026-10-03 | Keepers that do nothing this week wait for Wednesday's plan (Mon/Tue adds stay free to chase with); streamer caption leads with "You're favored (77%): no stream is worth an add"; `explain_week` lists every player's week (all games / in the lineup) to compare with Yahoo's matchup page | (this commit) |
| 2026-10-03 | Forecasts logged for checking: Yahoo's first projection per team and week kept (All Matchups `first`, matchup `yahoo_first`, `live` with Yahoo's projection); our projection of all 16 teams at the week's first plan (`league_weeks[w].ours`); `xfp_log.csv` adds goalie points per start and each player's expected week (`week_games`, `week_xfp`); Yahoo's per-player projections from matchup screenshots go to the private archive (Nico) | (this commit) |
| 2026-10-03 | The private data archive proven writable: the bot's first push (its README, a19b7a4); every run now checks with a dry-run push and the data check reports a "no" | (this commit) |
| 2026-10-03 | Screenshots safe to send freely: a pre-game matchup's top card read the projections as the score (247.58 - 188.96; now 0 - 0 from "Games Played 0/43"); the week label ("Week 2 1" had read as 21) decides the week, another week's matchup changes nothing but Yahoo's final and the archive; scrolled All Matchups take their batch's week; older standings never replace newer; a team page without /opp goes to the roster it matches. `docs/data.md` describes all data | (this commit) |
| 2026-10-03 | Yahoo website matchup headers read ("Orig Proj", "Live Proj", score): Yahoo keeps the pre-week projection, so it can be sent any time, for any matchup; week 1's (188.20 - 165.01) saved by a repair | (this commit) |
| 2026-10-03 | Week 1 forecast of ours rebuilt from the draft as of Sep 29 (`scripts/retro_forecast.py`, Check workflow `retro-forecast`), saved marked `retro`: me 160.9 vs Bahelin 160.0 (51%); Yahoo's original 188.2 - 165.0. Ours is lower mostly by the goalie minimum in a 6-day week (P 33-93%), which Yahoo ignores | (this commit) |
| 2026-10-05 | Stolarz on my roster (never mine): in a website Transactions screenshot three rows took the player of the row below (its team or date unread), so Stolarz went on my roster as an add and Nikishin and Lindholm changed teams. A row now holds one Yahoo move (an add, a drop, or an add then its drop): extra players are cut, keeping the ones level with the row (website) or first (app), and an undated app header still ends the row above. Applying moves is idempotent (an add of a player already there, or a drop of one already gone, changes nothing), and an add of a player another team holds is left out and said. Overlapping team screenshots take a row's slot from whichever copy shows it (Celebrini's unread first copy had cleared every slot). `/notmine Name` takes a misread player off my roster and refunds his add. One repair undoes it all. Untested on the real screenshot (not kept): the cause is inferred from the moves it produced | (this commit) |
| 2026-10-05 | Every screenshot archived (private repo, `screenshots/`) with what was read from it, so misreads can be checked (Nico). Team names OCR misreads in small text ("Pastasau", "Bahein Boys", "Belova", "Beliora": 5 of 12 rows of a zoomed-out website screenshot unplaced) match the one team they're clearly closest to (difflib >= 0.7 and 0.15 ahead; real names are at most 0.50 alike); unplaced rows come with a tip to zoom in or send as a file | (this commit) |
| 2026-10-05 | The website's Standings table read too (W-L-T mid-page, Pct's three decimals never taken for points): all 16 rows right on Nico's screenshot. Week 1 standings and Yahoo's final of my matchup (163.85 - 189.40; box scores gave 160.85 - 192.70) saved by a repair | (this commit) |
| 2026-10-06 | Check workflow: `explain-week` takes a `players` input (free agents to score too, names only, validated) and shows the top 60 moves, so a move Nico asks about can be checked against live NHL data from a sandbox without network (Martone vs Quinn, Byfield, Chinakhov) | (this commit) |
| 2026-10-06 | `scripts/schedule.py` (Check workflow `schedule`): every NHL game day by day for this week and next, for schedule grids of my roster and streamers. explain-week with `players` lists every move, so a named player's moves are never cut off (Byfield's weren't in the top 60) | (this commit) |
| 2026-10-06 | `explain_week --ir NAME` (Check input `ir`): scores the week as if a player of mine sat on IR+, in memory only, so the open-spot adds can be ranked right after Nico moves someone in Yahoo, before the bot hears of it (Celebrini to IR+) | (this commit) |
| 2026-10-06 | Two runs (Oct 5, 19:31 and 20:30 UTC) were cancelled at the 15-min job limit without ever getting a GitHub runner (no steps, no logs); others that evening waited 5-10 min. The job limit is now 30 min, and `main.py`'s step has its own 12-min limit for a run that hangs (`tests/test_workflow.py`) | (this commit) |
| 2026-10-09 | Step 1: the Board (`bot/board.py`, `state/board.json`): each plan's matchup, add budget and price, every add/drop weighed with its status (now / waits / passes / fails) and why, and the plan's moves, saved by the weekly plan and the news check. `explain_week` now prints that Board from the plan's own computation (it had scored moves before the IR moves the plan assumes, so the two could disagree). The ledger (`state/ledger.json`: adds, taps, league moves, rosters by day, scores, results, forecasts, add candidates) split from the run's bookkeeping; one dict in memory, a pre-split state file wins on load | (this commit) |
| 2026-10-09 | Step 2: weeks +1 and +2 played against their real opponents (`matchup.week_ahead`, `ahead_value`): a move's points there count by the change in that week's P(win). P(win) reads margins at their realized size: 0.80 this week (banked points in full), 0.75 / 0.70 for the weeks ahead (`check_sigma --ahead`, now `--season` too: 0.77/0.76/0.67 in 2024-25, 0.84/0.74/0.73 in 2025-26). Fringe gaps (`scripts/check_gaps.py`): 1.02 / 0.95 / 0.89 / 0.84 / ~0.75 out to week 20 in both seasons, so the long run keeps its 6-week rate x 0.5, for the weeks after the two ahead. The Board shows each move's +1/+2 win-pts and when to make it. On Oct 9: weeks 3-4 at 36% / 41%, add price 9.7 -> 10.0, same plan | (this commit) |
| 2026-10-09 | Step 3: the plan (`engine/plan.py`): one coherent set of dated moves (no shared add or drop, each judged with the earlier ones made); a keeper waits for Monday only if another move can use this week's add (Oct 9: Kelly now, not Kantserov now + Kelly Monday for the same drop). Kept once seen unless a move can't be made or a new plan is 0.02 wins better; a change is one message with its reason, replaced cards lose their buttons, cards go out once on their day. Messages (`bot/messages.py`): a 4-6 line plan on Monday/Wednesday/`/week`; a screenshot gets the score or the change; Taken/Skip re-plan at once; the evening check speaks only on a change. Retired: the goalie line, season line, stance, biggest swing, can-wait text, streamer caption, the decision and per-add charts in Telegram (the schedule chart stays until step 4) | (this commit) |
| 2026-10-09 | Step 4: the dashboard rebuilt around the Board (`site/index.html`, data from `bot/weekly.dashboard_data`): the score, the plan in the message's words, this week and the next two, lineup games left per night (you vs them), the top moves' value split into this week / weeks +1-2 / later against the add price, the schedule grid, every move weighed (sortable), the add budget. The plan message's image is the page's `?card` view rendered by headless Chromium (`notify/snapshot.py`, Playwright, Helsinki time); the workflow installs the browser (cached, allowed to fail: the old schedule chart is the fallback). Checked at 375 px, light and dark. Retired: the decision map and per-add charts (`report.decision_view`, `add_view`, `weekly_gains`) | (this commit) |
| 2026-10-09 | Dashboard grid (Nico: Kelly wasn't in it, it showed only the best streamer per position): the plan's adds get their own rows, marked Plan, from the day each is made (`weekly.plan_weeks`: the roster with the whole plan made); a "By slot" view per position and night (filled, open, filled or freed by the plan; games lost to a full lineup; both teams' lineup games), with a "with the plan's moves" switch | (this commit) |
| 2026-10-09 | Each move gets its exact day (Nico): `plan.time_moves` tries every day it could be made that week and takes the earliest that loses no points (the drop plays his games first, the add is in before his; waiting otherwise only risks a claim), and says why when it waits ("after Samuelsson's game on Fri 9"). Messages name the date ("Fri 9 Oct (today)"); each card opens with "Make it <day>, before that evening's games" (NHL dates: during that day in Helsinki) | (this commit) |
| 2026-10-09 | IR returns in the add values (Nico): an add into an open spot while players in IR slots outnumber the spots left counts in full only until they're likely back (the return curves, as stashes), then only what it beats the weakest player by, or nothing if it would be cut (`matchup.ir_crunch`). The plan says "holds the spot until Celebrini is back". It had credited such adds (Pinto into Celebrini's spot) a season. This week's points unchanged; no effect today (no open spot) | (this commit) |
