# Fantasy NHL Assistant GM

A free background assistant for a 16-team Yahoo H2H points league ("Not for
everyone!"). It does the daily analysis and messages you on Telegram only
when there's something worth doing, so you don't have to check your roster
every day. It never touches Yahoo itself: you make the moves.

## How it decides

Every decision is scored in one currency: **expected fantasy points (xFP)**.

- **Skaters:** projected stats per game = skill (per-minute rates for every
  scoring stat, blending past seasons, DailyFaceoff's preseason projections
  and this season) x role (ice time, weighted toward recent games, so line
  and power-play changes show up within days). Backtested on 2025-26: lower
  error than season-to-date or "last 10 games" (`python -m scripts.backtest`).
- **Goalies:** getting the start is worth ~10x more than the matchup, so
  start odds come first (DailyFaceoff confirmations, else recent start
  share with back-to-back logic), then a matchup model (team strength,
  opponent, home ice).
- **Tonight's lineup:** an exact optimizer over Yahoo position eligibility,
  counting only players who play tonight, discounted for injuries and
  game-time decisions (DailyFaceoff line charts).

## What you get

- **Evening briefing** at 21:00 Finland time, or an hour before the first
  puck drop if earlier: the lineup changes worth making tonight, with the
  expected point gain. Sent only if a change is worth at least 0.5 points.
  At most one follow-up if new information (goalie confirmation, injury)
  makes a clearly better lineup. Nothing is sent 23:00-08:00.
- **Done / Skip buttons.** Tap Done after you've made the change in Yahoo -
  that's how the assistant knows your lineup (no Yahoo API access).
- `/roster` in the chat shows the roster it thinks you have.
- If a run fails, you get one alert that day.

Coming next (see the plan): add/drop and streaming advice with a budget for
your 36 season adds, the 3-goalie-game weekly minimum, IR management, and a
Sunday report.

## Setup

1. **Telegram bot:** message [@BotFather](https://t.me/BotFather), send
   `/newbot`, and keep the token. Send your bot any message, then open
   `https://api.telegram.org/bot<TOKEN>/getUpdates` to find your chat id.
2. **GitHub secrets** (Settings -> Secrets and variables -> Actions):
   `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`.
3. **Your roster:** list your players in a text file, one per line (see
   `scripts/seed_roster.py` for the format), then:
   ```bash
   python -m scripts.seed_roster my_team.txt
   ```
   Commit `state/roster.json`. The first briefing lists a full lineup; tap
   Done once it matches Yahoo, and from then on you only get changes.
4. The workflow `.github/workflows/assistant_gm.yml` runs every 30 minutes
   during the afternoon and evening, and commits its state back to the repo.

## Local use

```bash
pip install -r requirements.txt
python -m pytest
python main.py --dry-run --force                                   # tonight's plan, sends nothing
python main.py --dry-run --force --now 2026-01-17T18:00:00+02:00    # replay a past night
python -m scripts.xfp_table                                       # current xFP for every skater
```

## Data sources (all free)

- NHL APIs: schedule, rosters, game-level stats.
- DailyFaceoff: starting goalies, line charts and injuries, and the
  customizable projections (powered by 5v5hockey). These are unofficial
  pages; each fails soft, so a change on their side degrades one signal
  instead of breaking a run.

No Yahoo API: Yahoo's Fantasy Sports API now needs manual approval, so the
assistant never reads or writes Yahoo. It learns your roster from
`scripts/seed_roster.py` and your Done taps (`league/roster.py` is the one
place that would change if API access were ever added).
