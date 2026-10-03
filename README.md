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
  and power-play changes show up within days). Past seasons are aged:
  young players improve and veterans decline (fit on 2024-25, checked on
  2025-26). Backtested on 2025-26: lower
  error than season-to-date or "last 10 games" (`python -m scripts.backtest`).
- **Goalies:** getting the start is worth ~10x more than the matchup, so
  start odds come first (DailyFaceoff confirmations, else recent start
  share with back-to-back logic), then a matchup model (team strength,
  opponent, home ice).
- **Tonight's lineup:** an exact optimizer over Yahoo position eligibility,
  counting only players who play tonight, discounted for injuries and
  game-time decisions (DailyFaceoff line charts).

## What you get

- **Evening briefing** between 19:30 and 20:30 Finland time (earlier only if
  a game starts within the hour): the lineup changes worth making tonight, with the
  expected point gain. Sent only if a change is worth at least 0.5 points.
  At most one follow-up if new information (goalie confirmation, injury)
  makes a clearly better lineup. Nothing is sent 23:00-08:00.
- **Done / Skip buttons.** Tap Done after you've made the change in Yahoo -
  that's how the assistant knows your lineup (no Yahoo API access).
- **Weekly matchup plan** at noon on each fantasy week's first day (or any
  time with `/week`). It's built to win the week, not just score points:
  - expected score vs this week's opponent and your chance of winning
  - lineup games left for each side
  - whether you'll make the 3-goalie-game minimum
  - the add/drops worth making, each with Done, Taken and Skip buttons.

  Every add is valued in **win-pts** (percentage points of a weekly win): what
  it does to this week's win odds, plus its later points at what a point is
  worth in a typical week. It's made when that beats the **add price**, set so
  that spending at that bar (at most 2 a week) uses the 36 adds at the right
  pace, 6 kept for the playoffs. So a close week with good streamers gets 2
  adds, a lopsided one none. This week's adds go to moves that pay this week;
  a keeper that adds nothing now is flagged to make on Monday. Your streaming
  spots (the 3 skaters nearest waiver level, plus your weakest goalie) are
  valued only for the ~3 weeks a streamer stays, not the season.
  An injured free agent worth holding can be stashed straight into an empty
  IR slot: no drop until he's back.
  `python -m scripts.explain_week` shows every candidate with its value and
  verdict.
- `/opp`, then paste your opponent's Yahoo team page (any copy works), to
  refresh their roster. Other teams start from the draft results.
  `/taken Name` tells the bot that a suggested free agent has been taken.
- `/trade Knight for Bouchard` (or `Knight, Tuch for Makar`) judges a trade:
  points per week for you and for them over the 6 weeks after it clears,
  with open spots filled from free agents and at least two goalies kept, plus
  both rosters' F/D/G balance. `/trade` alone suggests 3-4 trades the other
  manager could plausibly accept (takes a few minutes).
- `/roster` in the chat shows the roster it thinks you have. If it's wrong,
  send screenshots of the Yahoo app's Team tab (scroll so every player is in
  one of them; overlap is fine), or `/myteam` and paste the page's text. It
  replaces your roster and slots and replies with what changed. Screenshots
  are read with free offline OCR; if a set looks incomplete it asks for the
  rest, or `/save` keeps what it read. `/opp` takes screenshots too.
- Free agents carry Yahoo's position eligibility (seeded from a Yahoo export,
  then updated from every screenshot), so a streamer is slotted the way Yahoo
  would let you.
- **Transactions screenshots** keep every roster current: League > Transactions
  in the app or on the website, or the league chat's "Gwp added ..." messages.
  The bot applies each team's adds, drops and trades (each once, even when
  screenshots overlap or show the same move in two places), and warns if new
  ones don't reach back to the last it saw. An add
  recommendation also has a **Taken** button: one tap and the bot suggests
  the next best.
- **Matchup screenshots** are the quickest update: screenshots of the Yahoo
  app's Matchup tab (scrolled through) refresh your roster and slots, your
  opponent's roster and the live score at once, then the bot replans the week.
  Taken before the day's first puck, Yahoo's score replaces the bot's own
  box-score tally. Ideal on Monday and again mid-week.
- If a run fails, you get one alert that day.

From Wednesday the plan comes again with a stance: chase when behind (with the
add that would swing the odds most, and what it costs), protect when ahead, or
"this week looks lost/won" when it's decided (an add then barely moves the odds,
so it rarely beats the price).

The plan comes with charts: a decision map (each candidate add in win-pts, this
week's against later, with the add price as a diagonal: above it, worth an
add); a schedule grid for this week and next
(who starts, games lost to a full lineup, open slots by position, and the best
streamer per position drawn on the nights he'd fill); and for each recommended
add, its points gain week by week and the add budget.

The same numbers are on a phone-first web dashboard (GitHub Pages, rebuilt with
each plan): tap any add on the decision map, the grid or the table for its
week-by-week gain and what it does to the add budget.

Coming next: IR management and a Sunday report.

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
4. **The league:** every team's roster (Yahoo's draft results, one
   `[Team name]` line before each team) and your schedule
   (`SCHEDULE` in `config/league.py`):
   ```bash
   python -m scripts.seed_league data/league/draft_2026.txt
   ```
   Commit `state/league.json`. Free agents are everyone on an NHL roster
   who isn't on one of these teams.
5. The workflow `.github/workflows/assistant_gm.yml` runs every 30 minutes
   during the afternoon and evening, and commits its state back to the repo.

### Instant replies (optional)

Without this, the bot reads your messages at the next half-hourly run. With
it, a free Cloudflare Worker (`relay/`) receives each message as Telegram's
webhook, queues it, and starts the workflow at once: replies in about half a
minute, any time of day. Messages wait in the queue until a run has read them,
so a failed or cancelled run loses nothing.

1. `cd relay && npm install && npx wrangler login` (free Cloudflare account).
2. `npx wrangler d1 create assistant-gm-relay`, put the printed
   `database_id` in `relay/wrangler.toml`, then
   `npx wrangler d1 execute assistant-gm-relay --remote --file schema.sql`.
3. A GitHub fine-grained token for this repo only, with **Actions: Read and
   write** (github.com -> Settings -> Developer settings -> Fine-grained
   tokens): `npx wrangler secret put GITHUB_TOKEN`. In Windows PowerShell,
   where Ctrl+V may not paste, type `Get-Clipboard | npx wrangler secret put
   GITHUB_TOKEN` first, then copy the token, then press Enter.
4. Two random secrets, the same value in Cloudflare and GitHub:
   `WEBHOOK_SECRET` (GitHub name `TELEGRAM_WEBHOOK_SECRET`) and `RELAY_TOKEN`.
5. `npx wrangler deploy`, and set the GitHub secret `RELAY_URL` to the
   printed `https://assistant-gm-relay.<you>.workers.dev`.

The next run points Telegram's webhook at the relay. If instant replies stop
working (e.g. the GitHub token expired: renew it and repeat step 3), you get
one alert a day and messages are still read at the half-hourly runs. To go
back to polling, delete the `RELAY_URL` secret: the next run removes the
webhook.

## Local use

```bash
pip install -r requirements.txt
python -m pytest
python main.py --dry-run --force                                   # tonight's plan, sends nothing
python main.py --dry-run --force --now 2026-01-17T18:00:00+02:00    # replay a past night
python -m scripts.xfp_table                                       # current xFP for every skater
python -m scripts.explain_week                                    # every add/drop this week's plan weighed
python -m scripts.compare_yahoo paste.txt                         # a copied Yahoo player list vs our projections
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
