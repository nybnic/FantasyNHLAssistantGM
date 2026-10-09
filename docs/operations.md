# Operations

## How it runs
- **GitHub Actions** (`.github/workflows/assistant_gm.yml`): started every 30 min,
  09:00-21:30 UTC, by the relay's Cloudflare crons (`relay/wrangler.toml`), and
  by the relay on each Telegram message. The workflow's own cron is only a
  backup: GitHub ran it ~3 times a day. Changing the crons needs `npx wrangler
  deploy` in `relay/`; `tests/test_relay.py` checks they cover the briefing window. The concurrency group `assistant-gm` runs one at a time; a newer
  queued run replaces an older queued one (shown as "cancelled", harmless).
- Each run: checkout **latest `main`** -> `python main.py` -> commit `state/` ->
  `git pull --rebase` -> push. A whole run is ~20-40 s; the weekly plan adds
  ~1 min (DFO lines for 32 teams + move search).
- **Relay** (`relay/`, a Cloudflare Worker + D1): Telegram's webhook target.
  Queues updates until a run acks them by offset, and dispatches the workflow
  (on each message and on its crons).
  Setup is in the README.
- **Cache**: `data/cache/gm` is kept between runs by `actions/cache` (past
  seasons never expire; current data expires in hours). When a refetch fails,
  a copy up to 2 days old is used instead (`clients/cache.STALE_LIMIT`), with a
  warning in the log.
- **Failures**: each step (Telegram updates, NHL teams, /trade, weekly plan,
  briefing) runs even if an earlier one failed; the run then goes red and
  Telegram gets one alert a day naming the step. A weekly plan counts as sent
  only once its message is out, so a failed one is retried by the next run.
- **Data check**: a data source that fails or is served from an old cached copy
  (DailyFaceoff lines, starting goalies, projections; NHL schedule, rosters, stats)
  gets one Telegram line a day, with what it means for the advice
  (`clients/health.py`, `bot/daily.alert_health`). The run itself stays green.

## Checking a change against live data
`.github/workflows/check.yml` ("Check", run by hand: Actions > Check > Run
workflow, or the API) runs a dry run, `explain_week`, `/trade` suggestions or
a backtest on GitHub's runners, with the bot's NHL data cache (read only).
It sends nothing, commits nothing and gets no secrets; the output is the job
log (public, like the repo). For a development sandbox without network access
to the NHL and DailyFaceoff APIs. `explain-week` lists the top 60 moves and
takes an optional `players` input (comma-separated free agents to score too,
like `--add`; then every move is listed), e.g. to weigh a drop Nico is considering,
and an `ir` input (players of mine to treat as on IR+, `--ir`, this run only) for a
move made in Yahoo that the bot hasn't been told about yet.
`schedule` prints every NHL game day by day for this week and next
(`scripts/schedule.py`).

## Secrets
| Where | Name | Used for |
|---|---|---|
| GitHub | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | Sending, reading |
| GitHub | `RELAY_URL`, `RELAY_TOKEN`, `TELEGRAM_WEBHOOK_SECRET` | Relay (optional) |
| GitHub | `ARCHIVE_DEPLOY_KEY` (SSH private key; its public half is a write deploy key on the private `nybnic/FantasyNHLAssistantGM-data`) | The data archive: DFO line charts, Yahoo matchup rows (optional: without it the steps skip). The key needs **write access**; each run checks with a dry-run push and the data check reports a no |
| Cloudflare | `GITHUB_TOKEN` (fine-grained, Actions r/w), `RELAY_TOKEN`, `WEBHOOK_SECRET` | Relay |

GitHub masks secrets in logs, but anything the bot *sends to Telegram* isn't
masked: `notify/telegram.TelegramError` and the failure alert strip the token.

## Dashboard (GitHub Pages)
`site/index.html` is ours; `site/data.json` is written by every plan run
(`bot/weekly.dashboard_data`: the Board plus the plan in the message's words, the
schedule grid and the add budget) and committed with the state. `index.html?card`
is the compact card the plan message's image is a screenshot of (`notify/snapshot.py`,
headless Chromium installed by the workflow, cached; without it the old matplotlib
schedule chart goes out). The workflow's `deploy-dashboard` job publishes
`site/` when `data.json` changed. One-time setup: repo Settings > Pages >
Source: GitHub Actions. The page is public (like `state/`) and marked
noindex. Preview a dry run's copy: `python -m http.server 8765 --directory data/charts`.

## State files (bot-owned, committed every run)
| File | Holds | Changed by |
|---|---|---|
| `state/roster.json` | My players and Yahoo slots | Done taps (lineups, adds), `/myteam` pastes and screenshots, matchup screenshots, `scripts/seed_roster.py` |
| `state/positions.json` | Yahoo position eligibility per player (free agents use it) | Every Yahoo screenshot or paste; seeded once by `scripts/seed_positions.py` |
| `state/league.json` | The other 15 rosters + a `taken` list, waivers, and `moves_through` (the time every league move is known through) | Transactions screenshots, `/opp` pastes, matchup screenshots, `/taken`, `scripts/seed_league.py` |
| `archive/dfo_lines/YYYY/DATE.csv` in the **private** `nybnic/FantasyNHLAssistantGM-data` | DFO's line charts, all 32 teams, once a game day (13:00 ET, or 30 min before an earlier first puck): line, PP/PK unit, slot, injury, game-time decision, NHL id. Third-party data, so never in this repo | The workflow checks it out into `archive/` (gitignored) and pushes; `bot/dfo_archive.py` writes |
| `archive/yahoo_matchup/YYYY/TIME.csv` in the same private repo | Every player row of my matchup screenshots: slot, points so far and Yahoo's week projection, both teams. Third-party data, so never in this repo (Nico, 2026-10-03) | `bot/ingest.archive_matchup`, when a matchup screenshot is read |
| `state/xfp_log.csv` | Every NHL roster player's projection (xFP, TOI, PP TOI, games, durability; goalies: start share, save %, points per start) and owner, plus his expected points in the week's remaining games (`week_games`, `week_xfp`), at each fantasy week's first run: what the model believed, for in-season checks | The week's first run (`bot/xfp_log.py`), appended |
| `state/ledger.json` | The season's record (`gm_state.LEDGER_KEYS`): the adds ledger (`adds`: what the add budget counts), every tap (`decisions`), league moves applied (`transactions_seen`, `league_adds`), each day's rosters before its first puck (`day_rosters`: banked points), standings, the latest matchup score, each week's plans and result (`results`), every team's weekly forecasts (`league_weeks`), each week's add candidates (`add_pools`, which the add price is solved over), playoff opponents | Every run (via `state/gm_state.py`) |
| `state/gm_state.json` | The run's bookkeeping: Telegram offset, pending cards, sent briefings/plans, the plan Nico has seen (`plan`: its moves, their cards), screenshot buffers, flags. Loaded together with the ledger as one dict | Every run |
| `state/board.json` | The latest plan's Board (`bot/board.py`): the matchup, the add budget and price, every add/drop weighed with its status (plan, passes, fails), when and why, the plan's moves and why it changed. What `explain_week` prints; git history keeps every one | Each weekly plan and evening news check |

## The add budget (`state["adds"]`)
Every add counts once, however the bot learns of it: a Done tap (dated the
day of the tap), my row in a League > Transactions screenshot (dated by the
row), or a new player in a team page, `/myteam` paste or matchup screenshot.
A new player another team had is taken for a trade (no add) and named in the
reply; more than 4 new players at once is a stale roster, not adds. The same
player within 7 days is one add learned twice. Adds from before the ledger
(Oct 1 2026) have no player id; a Transactions row for a player already on my
roster names one of them.

## Keeping league data fresh (no Yahoo API)
- My roster: Done on recommendations; for anything else (moves the bot didn't
  suggest, IR changes), `/myteam` + paste my Yahoo team page.
- Opponent: `/opp` + paste their Yahoo team page, ideally each Monday.
- Both at once, plus the live score: screenshots of the Yahoo app's Matchup tab
  (`clients/screenshot.read`, `bot/ingest.finish_matchup`). The score is used as
  points so far only on the day it was taken, and only if it was taken before
  that day's first puck (later, today's points can't be told apart).
- Free agents: everyone on an NHL roster minus all known rosters minus `taken`.
  Other teams' pickups are unknown until a transactions screenshot: League >
  Transactions in the app or on the website (whose times are US Eastern), or the
  league chat (dated "hier à 18:48", read against when it was sent; moves sent
  together share the first one's time). `bot/ingest.finish_transactions` applies
  them oldest first, each once: the same team, type and players within 12 h of a
  move already seen is that move (`TX_SAME_MOVE`). It warns when new screenshots
  don't reach back to the last ones seen. A set that reaches back to the moves already seen
  and shows the newest of them (the list's top) makes every roster current as of
  when it was sent (`league.json` `moves_through`); the weekly plan asks for
  Transactions screenshots once that is 2+ days old (`LEAGUE_MOVES_STALE_DAYS`). Then there's the Taken
  button on an add, or `/taken`.
- Standings and the league's matchups: League tab screenshots. Standings
  (`state["standings"]`: W-L-T and points for; the weeks they include are the most
  games any team has played) and All Matchups (`state["league_weeks"]`: the
  week's pairings, each team's score and Yahoo projection) feed the season odds
  (`engine/season.py`, `bot/ingest.finish_standings` / `finish_scoreboard`).
- Waivers: every drop the bot learns of (a Transactions row, a Done tap's drop)
  puts the player on waivers in `state/league.json` (`waivers`): an add of him is
  a claim that plays from 2 days after the drop (assumed from "1 day" waivers;
  verify against the "W (date)" Yahoo shows). Drops seen before 2026-10-01 aren't there.

## Past incidents
| Date | What | Fix |
|---|---|---|
| 2026-09-29 | Two runs started 3 s apart. The second checked out the pre-push commit, handled a Skip tap twice, and crashed on Telegram's "message is not modified"; its state commit then conflicted | Checkout `ref: main`; relabeling buttons is best-effort; Telegram errors carry the reason, not the URL |
| 2026-10-01 | Matchup screenshots of the week's Totals view listed Schenn (dropped that day) without a slot: he was saved to my roster and counted as an add (3 used in week 1). Murashov was missing from them because Nico had dropped him; the repair wrongly put him back, fixed by a second one | Slotless rows are skipped; screenshots showing fewer players than I have keep the missing ones (and say how to record a real drop); a player mine within 30 days isn't a new add (`seen_mine`, cleared by any drop of mine); `state/repairs.py`, each repair once |
| 2026-10-05 | A website Transactions screenshot: three rows each took the next row's player (its team or date unread). Stolarz went on my roster as an add (my 3rd goalie, so no goalie warnings), Nikishin to Gwp, Lindholm to Lazy Lew. Team screenshots then couldn't remove him (14 shown, 15 kept), and Celebrini read twice, first without a slot, cleared every slot | A row is one Yahoo move; moves apply idempotently; adds of players another team holds are refused; duplicate screenshot rows merge their slots; `/notmine`; repair `2026-10-05 stolarz misread` |
| 2026-10-05 | Two runs (19:31, 20:30 UTC) "cancelled" after exactly 15 min without ever getting a GitHub runner (no steps, no logs), so GitHub's failure email; others that evening waited 5-10 min for one. Nothing lost: the relay keeps Telegram updates until a run acks them, and the next run answered | Job limit 30 min (it counted the wait); the `main.py` step keeps a 12-min limit for a hang |
| 2026-09-29 - 10-01 | No evening briefings. GitHub ran the `*/30` schedule only ~3 times a day (e.g. 15:33, 20:29, 00:06 UTC on Sep 30), never inside the 21:00-23:00 Helsinki window | The relay's Cloudflare crons start the runs; GitHub's cron stays as backup |

## Calendar assumptions to verify
- Week 19 = Feb 1-14 2027 (the double week): confirmed in Yahoo 2026-10-01. Still to check
  (Yahoo's settings or help): do the weekly add limit and the goalie minimum apply per
  calendar week or per matchup in week 19?
- Playoff opponents (weeks 24-26) must be named with `/opp Team Name`.
