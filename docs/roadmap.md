# Roadmap

Pick the top item unless Nico names another. Each item has a short "done when".
Move finished items to the log at the bottom, with the commit.

## Direction (2026-10-01)
Yahoo covers live scores, projections, Start Active and goalie-minimum warnings.
The bot's edge is the **add decision**: spend the 36 adds where they change a
week's result, save them where they don't, and show that trade-off this week and
over the season. Input is mobile screenshots; output is Telegram plus charts.

## Next
From the 2026-10-01 review (accuracy first, then the evidence loop, then messages).

## Later
- Playoff odds (needs all 16 rosters fresh).
- Verify in Yahoo: what Start Active does with injured players and on an overflow night
  (the briefing assumes it starts anyone with a game, the better season value first);
  when a claim of a player dropped today can play (the bot assumes 2 days
  later: the "W (date)" next to him), the add limit and goalie minimum in the week-19 double week
  (dates confirmed 2026-10-01), regular-season ties and standings tiebreakers
  (points-for matters for the add price, which values only wins), the consolation bracket.
- Late regular season: weigh playoff seeding (the higher seed wins tied playoff
  weeks) when planning weeks 20-23.
- Projections run high and overstate differences (`scripts/check_sigma.py`: margins
  realize at 0.87; teams 0.13 sigma under projection, down from 0.36 with the return
  curves): healthy skaters' falling availability (0.96 -> 0.89 over six weeks) and
  goalie shares are the next suspects, before shrinking add gains.
- Calibrate `MODEL_SD_SHARE`, the streaming-spot counts and the add price once 4-6
  weeks of real results and logged candidates exist (`scripts/sim_add_policy.py --logged`).
- IR: read Yahoo's own injury tag (IR, IR-LT, O, DTD) from screenshots instead of
  inferring it from DFO; value the spot crunch when an IR'd player returns.
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
- Snapshot our own weekly xFP (not DFO's) to check the model in-season.
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
