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
  seasons never expire; current data expires in hours).

## Secrets
| Where | Name | Used for |
|---|---|---|
| GitHub | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | Sending, reading |
| GitHub | `RELAY_URL`, `RELAY_TOKEN`, `TELEGRAM_WEBHOOK_SECRET` | Relay (optional) |
| Cloudflare | `GITHUB_TOKEN` (fine-grained, Actions r/w), `RELAY_TOKEN`, `WEBHOOK_SECRET` | Relay |

GitHub masks secrets in logs, but anything the bot *sends to Telegram* isn't
masked: `notify/telegram.TelegramError` and the failure alert strip the token.

## State files (bot-owned, committed every run)
| File | Holds | Changed by |
|---|---|---|
| `state/roster.json` | My players and Yahoo slots | Done taps (lineups, adds), `scripts/seed_roster.py` |
| `state/league.json` | The other 15 rosters + a `taken` list | `/opp` pastes, `/taken`, `scripts/seed_league.py` |
| `state/gm_state.json` | Telegram offset, pending recs, decisions (the add budget counts Done adds), sent briefings/plans | Every run |

## Keeping league data fresh (no Yahoo API)
- My roster: Done on recommendations. Moves the bot didn't suggest aren't known
  yet (see the roadmap: `/myteam`).
- Opponent: `/opp` + paste their Yahoo team page, ideally each Monday.
- Free agents: everyone on an NHL roster minus all known rosters minus `taken`.
  Other teams' pickups are unknown until pasted or `/taken`.

## Past incidents
| Date | What | Fix |
|---|---|---|
| 2026-09-29 | Two runs started 3 s apart. The second checked out the pre-push commit, handled a Skip tap twice, and crashed on Telegram's "message is not modified"; its state commit then conflicted | Checkout `ref: main`; relabeling buttons is best-effort; Telegram errors carry the reason, not the URL |
| 2026-09-29 - 10-01 | No evening briefings. GitHub ran the `*/30` schedule only ~3 times a day (e.g. 15:33, 20:29, 00:06 UTC on Sep 30), never inside the 21:00-23:00 Helsinki window | The relay's Cloudflare crons start the runs; GitHub's cron stays as backup |

## Calendar assumptions to verify
- Week 19 = Feb 1-14 2027 (the double week). Check Yahoo's matchup dates before February.
- Playoff opponents (weeks 24-26) must be named with `/opp Team Name`.
