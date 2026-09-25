# Fantasy NHL Assistant GM

A free, background Assistant GM for a 16-team, category-based Yahoo NHL fantasy
league. It watches your roster, the real NHL schedule, and confirmed starting
goalies, and sends a Telegram message only when there's something worth acting
on. It never touches your Yahoo roster itself - notify-only.

Runs on a schedule via GitHub Actions (free tier), so it works even when your
PC is off.

## Status: Phase 1

This repo is being built in two phases:

- **Phase 1 (this code)**: all the infrastructure, plus every check that
  doesn't depend on the league's final scoring rules - injury/IR slot
  mismatches, empty active lineup slots, and starting-goalie alerts.
- **Phase 2 (later)**: once the league's categories/roster rules are
  confirmed, a category-based z-score value model gets layered on top for
  durable waiver-wire suggestions, value-based lineup swaps, and a
  streaming engine (short-term free-agent adds driven by favorable
  schedule swings - heavy game weeks and confirmed-starting free-agent
  goalies). See the design in the project's plan file for details -
  nothing in Phase 1 needs to change for it.

## How it decides what to tell you

- **Starting goalies**: cross-references your rostered goalies against the
  real NHL schedule (official, free) and DailyFaceoff's public
  starting-goalies page (unofficial best-effort, since no free official API
  publishes confirmed starters ahead of game time). Only flags it when
  there's a mismatch worth acting on: a benched goalie who's confirmed to
  start, or an active goalie who's confirmed *not* to start.
- **Injury/IR**: flags a rostered player tagged O/IR by Yahoo who isn't
  sitting in one of your league's IR slots, when a slot is actually open.
- **Empty lineup slots**: flags any active slot that's sitting empty.

## One-time setup

### 1. Yahoo Developer app

1. Go to <https://developer.yahoo.com/apps/create/>.
2. Create an app with **Fantasy Sports** API access, **Read** permission
   only (this project never writes to Yahoo).
3. Note the **Client ID** and **Client Secret**.

### 2. Authorize once, locally

```bash
pip install -r requirements.txt
export YAHOO_CLIENT_ID=xxx        # or `set` on Windows cmd, $env: on PowerShell
export YAHOO_CLIENT_SECRET=xxx
python scripts/setup_yahoo_oauth.py
```

A browser window opens asking you to approve access; paste the verifier
code Yahoo shows you back into the terminal. The script then prints:

- Your NHL league key(s) - copy the right one for `YAHOO_LEAGUE_KEY`.
- The full token JSON - copy it for `YAHOO_OAUTH_JSON`.

### 3. Telegram bot

1. Message [@BotFather](https://t.me/BotFather) on Telegram, `/newbot`, and
   note the bot token.
2. Send your new bot any message, then visit
   `https://api.telegram.org/bot<TOKEN>/getUpdates` in a browser to find
   your numeric `chat.id`.

### 4. GitHub repo secrets

In this repo's Settings -> Secrets and variables -> Actions, add:

| Secret | Value |
|---|---|
| `YAHOO_CLIENT_ID` | from step 1 |
| `YAHOO_CLIENT_SECRET` | from step 1 |
| `YAHOO_OAUTH_JSON` | printed by `setup_yahoo_oauth.py` |
| `YAHOO_LEAGUE_KEY` | printed by `setup_yahoo_oauth.py`, e.g. `453.l.12345` |
| `TELEGRAM_BOT_TOKEN` | from step 3 |
| `TELEGRAM_CHAT_ID` | from step 3 |
| `GH_PAT` | a fine-grained PAT, scoped to this repo only, with **Secrets: write** permission - needed so a scheduled run can persist a rotated Yahoo refresh token |

### 5. Enable the schedule

The workflow at `.github/workflows/assistant_gm.yml` runs every 4 hours by
default (`workflow_dispatch` also lets you trigger it manually from the
Actions tab). Adjust the cron once you know your league's actual waiver
day / lineup lock times.

## Local development

```bash
pip install -r requirements.txt
python -m pytest              # unit tests, no credentials needed
DRY_RUN=1 python main.py       # prints recommendations instead of sending Telegram
```

`DRY_RUN=1` requires the same env vars as production (Yahoo + Telegram
creds) except it never calls Telegram and never writes `state/last_run.json`,
so you can re-run it freely while checking that things look sane.

## Notes / known rough edges

- **DailyFaceoff scraping** (`clients/goalie_client.py`): DailyFaceoff has no
  official API. This was built and tested against its real page structure,
  but during the 2026 preseason lull the feed was empty, so the exact field
  names for a live goalie entry are a best-effort guess - check the logs on
  your first few real runs and adjust `_parse_entry` if it logs a schema
  warning. It fails soft either way (skips the DailyFaceoff signal, doesn't
  crash the run).
- **Yahoo player status codes**: `engine/roster_checks.py` treats
  `IR`/`IR-LT`/`IR-NR`/`O` as IR-eligible. Confirm this matches what your
  league actually uses once you have real injured players on your roster.
