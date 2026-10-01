# Roadmap

Pick the top item unless Nico names another. Each item has a short "done when".
Move finished items to the log at the bottom, with the commit.

## Direction (2026-10-01)
Yahoo covers live scores, projections, Start Active and goalie-minimum warnings.
The bot's edge is the **add decision**: spend the 36 adds where they change a
week's result, save them where they don't, and show that trade-off this week and
over the season. Input is mobile screenshots; output is Telegram plus charts.

## Next
1. **Evening briefing as a Start Active diff**: "Start Active is fine tonight",
   or only the overrides (an unconfirmed goalie, a scratch, an overflow night).
   Done when: a night with nothing to change gets one line or nothing.
2. **IR management**: flag an IR-eligible player in an active slot, and a
   returning player who needs a spot.
   Done when: the weekly plan and the briefing include an IR line when relevant.
3. **Sunday report**: the result vs projection, adds taken or skipped, the
   budget, and the week chart.
   Done when: sent Sunday evening, with the same numbers `explain_week` shows.

## Later
- Transactions-page screenshot to refresh every team (lower now: the matchup
  screenshot covers the opponent who matters).
- Playoff odds (needs all 16 rosters fresh).
- Verify in Yahoo: week-19 dates, regular-season ties and standings tiebreakers
  (points-for matters for the decided-week rule), the consolation bracket.
- Late regular season: weigh playoff seeding (the higher seed wins tied playoff
  weeks) when planning weeks 20-23.
- Calibrate `MODEL_SD_SHARE`, the add threshold and `CONCEDE_BELOW`/`COAST_ABOVE`
  once 4-6 weeks of real results exist.
- Streaming-goalie advice (only if the 3-goalie policy changes); Yahoo projected
  GP as a durability input; next year's draft (restore from tag `draft-2026`).

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
| 2026-10-01 | Long run over 6 weeks; streaming spots judged on the next 2 weeks | (this commit) |
