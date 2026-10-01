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
1. **Waivers per player**: a player dropped in the last day (Transactions
   screenshots) joins a day later and costs waiver priority.
   Done when: such an add shows "(waivers, plays from <day>)" and its week gain
   starts then.
2. **Banked points by day**: points so far from each day's roster, so mid-week
   adds and drops don't move them. Done when: dropping a player mid-week leaves
   "So far" unchanged.
3. **IR management**: flag an IR-eligible player in an active slot (moving him
   makes the next add drop-free), and a returning player who needs a spot.
   Done when: the weekly plan and the briefing include an IR line when relevant.
4. **Weekly results log, then the Sunday report**: Monday's projection, P(win),
   sigma, the final score and the adds per week in state (week 1 backfilled from
   git history); then the report: result vs projection, adds taken or skipped,
   the budget, the week chart. Done when: sent Sunday evening, with the same
   numbers `explain_week` shows. Unblocks every calibration below.
5. **Recommendation scorecard** (`scripts/scorecard.py`): what recommended and
   skipped adds scored vs the drop, over the hold. Done when: one line of it in
   the Sunday report.
6. **Data health**: say once a day when a source is down or stale (DFO lines,
   starting goalies, projections). Done when: a broken DFO fetch shows in Telegram.
7. **Evening briefing as a Start Active diff**: "Start Active is fine tonight",
   or only the overrides (an unconfirmed goalie, a scratch, an overflow night);
   plus an alert when news makes an add clear the price between plans.
   Done when: a night with nothing to change gets one line or nothing.

## Later
- Playoff odds (needs all 16 rosters fresh).
- Verify in Yahoo: the add limit and goalie minimum in the week-19 double week
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
- Opponent profiles: adds per week (Transactions) and lineup efficiency (Yahoo's
  final vs our projection). Log first; shade their projection once 3-4 weeks show an effect.
- Snapshot our own weekly xFP (not DFO's) to check the model in-season.
- Weekly plan message: lead with the action, context after. "Done, other drop"
  button. Naming a playoff opponent with /opp sends the plan.
- Pin requirements; memoize `ModelContext.skater`; split `main.py` (commands,
  ingest, weekly) and the message text out of `engine/matchup.py`.
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
