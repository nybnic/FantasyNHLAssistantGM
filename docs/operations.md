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

## Secrets
| Where | Name | Used for |
|---|---|---|
| GitHub | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | Sending, reading |
| GitHub | `RELAY_URL`, `RELAY_TOKEN`, `TELEGRAM_WEBHOOK_SECRET` | Relay (optional) |
| Cloudflare | `GITHUB_TOKEN` (fine-grained, Actions r/w), `RELAY_TOKEN`, `WEBHOOK_SECRET` | Relay |

GitHub masks secrets in logs, but anything the bot *sends to Telegram* isn't
masked: `notify/telegram.TelegramError` and the failure alert strip the token.

## Dashboard (GitHub Pages)
`site/index.html` is ours; `site/data.json` is written by every weekly plan
(`bot/weekly.write_dashboard`, from the same `engine/report.py` views as the charts)
and committed with the state. The workflow's `deploy-dashboard` job publishes
`site/` when `data.json` changed. One-time setup: repo Settings > Pages >
Source: GitHub Actions. The page is public (like `state/`) and marked
noindex. Preview a dry run's copy: `python -m http.server 8765 --directory data/charts`.

## State files (bot-owned, committed every run)
| File | Holds | Changed by |
|---|---|---|
| `state/roster.json` | My players and Yahoo slots | Done taps (lineups, adds), `/myteam` pastes and screenshots, matchup screenshots, `scripts/seed_roster.py` |
| `state/positions.json` | Yahoo position eligibility per player (free agents use it) | Every Yahoo screenshot or paste; seeded once by `scripts/seed_positions.py` |
| `state/league.json` | The other 15 rosters + a `taken` list | `/opp` pastes, matchup screenshots, `/taken`, `scripts/seed_league.py` |
| `state/gm_state.json` | Telegram offset, pending recs, decisions, the adds ledger (`adds`: what the add budget counts), each day's rosters before its first puck (`day_rosters`: banked points), each week's plans and result (`results`), sent briefings/plans, the latest matchup score, transactions applied, and each week's add candidates (`add_pools`, which the add price is solved over) | Every run |

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
  Other teams' pickups are unknown until a League > Transactions screenshot
  (`bot/ingest.finish_transactions`: applied oldest first, each once, with a warning
  when new screenshots don't reach back to the last ones seen), the Taken
  button on an add, or `/taken`.
- Waivers: every drop the bot learns of (a Transactions row, a Done tap's drop)
  puts the player on waivers in `state/league.json` (`waivers`): an add of him is
  a claim that plays from 2 days after the drop (assumed from "1 day" waivers;
  verify against the "W (date)" Yahoo shows). Drops seen before 2026-10-01 aren't there.

## Past incidents
| Date | What | Fix |
|---|---|---|
| 2026-09-29 | Two runs started 3 s apart. The second checked out the pre-push commit, handled a Skip tap twice, and crashed on Telegram's "message is not modified"; its state commit then conflicted | Checkout `ref: main`; relabeling buttons is best-effort; Telegram errors carry the reason, not the URL |
| 2026-09-29 - 10-01 | No evening briefings. GitHub ran the `*/30` schedule only ~3 times a day (e.g. 15:33, 20:29, 00:06 UTC on Sep 30), never inside the 21:00-23:00 Helsinki window | The relay's Cloudflare crons start the runs; GitHub's cron stays as backup |

## Calendar assumptions to verify
- Week 19 = Feb 1-14 2027 (the double week): confirmed in Yahoo 2026-10-01. Still to check
  (Yahoo's settings or help): do the weekly add limit and the goalie minimum apply per
  calendar week or per matchup in week 19?
- Playoff opponents (weeks 24-26) must be named with `/opp Team Name`.
