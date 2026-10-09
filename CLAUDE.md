# Fantasy NHL Assistant GM

A Telegram bot for one Yahoo H2H points league ("Not for everyone!", 16 teams).
The owner, Nico, manages "Nico's Groovy Team". **The goal is winning weekly
head-to-head matchups** (then the playoffs and the title), not maximizing season
points. The bot's edge over data sites is the decision: it plays out every night
of this week's matchup and the next two, values every add/drop in win
probability, and sends **one plan**: which move, on which day, and why.

Runs on GitHub Actions every 30 min, 09-21 UTC (plus on each Telegram message via
the Cloudflare relay). Notify-only: there is no Yahoo API access, so Nico makes
every move in Yahoo, and the bot learns about them from Done/Taken/Skip taps and
Yahoo screenshots (team pages, matchups, transactions from the app, website or
league chat) or pasted pages.

## How a plan run works (start here)
inputs -> **Board** -> rule -> views. Direction and history: `docs/plan-2026-10-09.md`,
`docs/model-overview.pdf` (the model in 5 slides, `docs/model-overview.html`).
1. **Inputs** (`bot/weekly.week_inputs`): rosters by day, projections (`model/`), the
   NHL schedule, availability, the add budget, Yahoo's live score from screenshots.
2. **Every move valued** (`engine/matchup.candidate_moves`) in **win-pts** (1 = 0.01
   expected weekly wins): change in P(win) this week + in weeks +1 and +2 against
   their real opponents (`week_ahead`) + later points x a typical week's worth of a
   point. Each night is re-solved with the best lineup (C2 LW2 RW2 D4 G2), so a game
   on a night the slot is full counts zero. Players in IR slots coming back are mixed
   in (`ir_returns`), and projected missed games cost only the edge over a streamer
   (`matchup.durability`).
3. **The add price** (`engine/addprice.py`): the win-pts an add must buy (~10), set by
   simulating weeks so the 36 adds last (6 kept for the playoffs, at most 2 a week).
4. **The plan** (`engine/plan.py`): `compose` (best first, no shared add or drop; a
   keeper that does nothing this week waits for Monday only if another move can use
   this week's slot), `time_moves` (the exact day: the drop's games first, the add in
   before his; else the earliest, as waiting risks a claim), `decide` (the plan Nico
   has seen stays unless a move can't be made or a new one is 0.02 wins better).
5. **The Board** (`bot/board.py`, `state/board.json`): everything one plan run computed,
   every move with its status (plan / passes / fails) and why. `explain_week` prints it.
6. **Views**: `bot/messages.py` (the plan in 4-6 lines; a card per move on its day,
   once; a screenshot gets the score or the change), `site/data.json` + `site/index.html`
   (the dashboard: the plan, why the win %, player by player, points by stat, and a tap-open
   sheet per player and per move; the Board's `teams`/`players` from `engine/report.py`),
   its `?card` view screenshotted for Telegram (`notify/snapshot.py`).
   When: Monday and Wednesday noon and `/week` = the full plan; a screenshot or
   Taken/Skip tap = a check; once an evening = silent unless the plan changed.

## Map
| Path | What lives there |
|---|---|
| `main.py` | Entry point: the run, step by step (each isolated), and the failure alert |
| `bot/` | The steps: `ingest.py` (Telegram taps, commands, screenshots, Transactions), `weekly.py` (the plan run: inputs, search, keeping it, the plan check; results, Monday's report, dashboard data), `board.py` (the Board), `messages.py` (the plan's words), `daily.py` (briefing, /trade, data check), `common.py` (outbox, dates, free agents) |
| `config/league.py` | **Single source of league rules**: scoring, slots, add limits, schedule (every regular-season opponent), calendar |
| `model/` | Projections: skater xFP, goalie per-start model, team ratings. See `model/CLAUDE.md` (evidence table) |
| `engine/` | Decisions: `matchup.py` (the week, every move's value), `plan.py` (the plan), `addprice.py`, `lineup.py`, `availability.py`, `ir.py`, `season.py`, `trade.py`. See `engine/CLAUDE.md` (how the plan works, **the decision log**, judgment calls) |
| `league/` | Rosters: mine (`roster.py`), other teams (`teams.py`), Yahoo text parser, fantasy weeks |
| `clients/` | Data: NHL APIs, DailyFaceoff (lines, goalies, projections), disk cache, screenshot OCR |
| `notify/` | `telegram.py` (Bot API calls), `snapshot.py` (the dashboard card as the plan's image, headless Chromium), `charts.py` (matplotlib: the result chart, and the fallback image) |
| `state/` | Bot-owned JSON, committed by every run: roster, league, `ledger.json` (the season's record: adds, taps, league moves, results, forecasts), `gm_state.json` (run bookkeeping, incl. `plan`: the plan Nico has seen), `board.json` (the latest plan). One dict in memory (`state/gm_state.py`) |
| `scripts/` | Tools: explain_week, backtests and calibration checks, seeding, render_slides |
| `site/` | The dashboard: `index.html` (ours; `?card` is the Telegram image), `data.json` (written by each plan run), on GitHub Pages: https://nybnic.github.io/FantasyNHLAssistantGM/ |
| `relay/` | Cloudflare Worker for instant Telegram replies |
| `docs/league-rules.md` | Full scoring and league rules, worked examples, where the points come from |
| `docs/operations.md` | Runtime, secrets, state ownership, past incidents |
| `docs/data.md` | What Nico sends, what's fetched, what's stored where, and what uses it |
| `docs/roadmap.md` | Prioritized backlog and the log of what was done. Start a session here |

## Commands
```bash
python -m pytest                                  # all tests (fast, offline)
python main.py --dry-run --force                  # tonight's lineup + this week's plan, sends nothing (~1 min)
python main.py --dry-run --force --now 2026-10-09T06:50:07+00:00   # as of a moment (repeatable comparisons)
python main.py --dry-run --report 1               # week 1's result report (so far, if unfinished)
python main.py --dry-run --trade "Knight for Bouchard"   # what /trade would reply
python -m scripts.explain_week                    # the Board: every move weighed, its parts, status and why (--add NAME, --ir NAME, --player NAME)
python -m scripts.scorecard                       # how the add suggestions turned out, made or skipped
python -m scripts.compare_yahoo paste.txt         # Yahoo projections vs ours, stat by stat
python -m scripts.backtest                        # model accuracy (--season, --horizon)
python -m scripts.check_sigma                     # do projected margins come true? (--ahead 1|2, --season 20242025)
python -m scripts.check_gaps                      # do fringe players' projected gaps come true? by horizon
python -m scripts.sim_add_policy                  # the add price vs the old rule, simulated seasons
python -m scripts.fit_absence                     # how fast absent players return (availability curves)
python -m scripts.sim_goalies                     # two goalies or three: 2025-26 replayed under each policy
python -m scripts.fit_goalie_starts               # who starts next: last result, back-to-backs
python -m scripts.render_slides --png data/charts/slides   # docs/model-overview.pdf from its HTML
```
A dry run writes `data/charts/` (the charts, `board.json`, `data.json` and a copy of the
dashboard). Preview it: `python -m http.server 8765 --directory data/charts` (the
`dashboard-preview` entry in the local, untracked `.claude/launch.json`). The Telegram card and the slides
need Playwright's Chromium locally: `pip install -r requirements.txt` then
`python -m playwright install chromium` (without it the card falls back to matplotlib;
CI installs it, cached). Live runs need network; first runs fill `data/cache/`.

## Rules
- **Evidence over intuition.** A model constant is either backtested (fit on one
  season, checked on the next) or labeled a judgment call in its comment. Report
  numbers, and say plainly when something is untested. Before arguing about a
  recommendation, decompose it (`explain_week`, or project the move's parts):
  Kelly for Samuelsson read +40 until it turned out 70% rested on one untested input.
- **Settled decisions live in the decision log** (`engine/CLAUDE.md`). Don't reopen
  them without new evidence. Add an entry when Nico makes a call.
- **State is bot-owned.** `state/*.json` changes on every run, so run
  `git pull --rebase` before committing, and never hand-edit state while a
  run may be in flight (runs are serialized; repairs go in `state/repairs.py`).
- **Public repo.** No secrets, and no third-party data files (Yahoo exports,
  projection snapshots stay gitignored). One approved exception (Nico,
  2026-10-01): Yahoo position eligibility only, in `state/positions.json`
  (`scripts/seed_positions.py`). Third-party data we need to keep (DFO line charts)
  goes to the private `nybnic/FantasyNHLAssistantGM-data` (Nico, 2026-10-03),
  as do the per-player Yahoo projections read from matchup screenshots (Nico, 2026-10-03).
  Error text must never contain the bot token.
- **Deploy = push to `main`.** The workflow runs from `main`. Nico has approved
  this flow: implement, run tests, dry run, then commit and push. Ask before
  destructive git (deleting branches or tags, force-push).
- **Telegram messages are for a phone.** Lead with the action and its exact day
  ("Fri 9 Oct (today): X for Y"), keep the plan to a few lines, give numbers with
  context ("win 16% -> 22%", "+11 win-pts against the 10 an add costs"). One plan,
  stable: never resend what was sent; a change says why. Words live in
  `bot/messages.py` only, so the message, the card and the dashboard can't disagree.
- **Data from chat becomes data files.** When Nico pastes league data
  (screenshots, Yahoo pages), save it under `data/league/` or `state/`, not
  just in the conversation.

## Definition of done
1. Tests pass, with new behavior covered by a test.
2. A dry run (or `explain_week`) shows the real output, and it has been sanity-checked.
3. `docs/roadmap.md` is updated (its log too), plus the decision log if a rule changed.
4. The work is committed with a message that says why, and pushed.

## Where things stand (2026-10-09)
- Done: steps 1-4 of `docs/plan-2026-10-09.md` (the Board and ledger; weeks ahead vs real
  opponents and calibrated margins; the coherent, committed plan and short messages; the
  dashboard and its Telegram card), plus exact days, IR returns in every move, and
  durability over replacement (after Nico's review of the slides).
- Next: step 5, one replay harness on 2025-26 to score rule changes (wins, playoff and
  title odds, plan changes a week, adds used). First candidates: `LONG_RUN_DISCOUNT` 0.5,
  `plan.CHANGE_MARGIN` 0.02, keepers waiting for Wednesday.
- Untested, and labeled so: durability (DFO projected GP, not archived), the 0.5 long-run
  discount, the 0.02 change margin, opponents streaming (frozen rosters). The add price's
  logged pools for weeks 1-2 predate the 2026-10-09 valuation changes (recalibrate
  mid-November). IR stashes don't see the others' returns. A plan run takes ~1 min locally.
- Calibration so far: projected team margins come true at 0.80 (0.75 / 0.70 one and two
  weeks ahead); fringe players' per-game gaps at 1.02 now, ~0.75 by week 20 (both seasons).

## League facts worth knowing without reading code
Full rules: `docs/league-rules.md` (kept in sync with `config/league.py` by a test).
- Peripherals are about half of skater scoring: shots 0.5, hits 0.6, blocks 0.8,
  faceoffs 0.1. Blocks are the biggest single source for defensemen (27%).
- 12 starters (C2 LW2 RW2 D4 G2) + 2 bench + IR + IR+. Max 2 adds/week, 36/season:
  the season budget binds (~1.6 a week after the 6 kept for the playoffs), not the weekly cap.
- A goalie minimum of 3 games/week, or **all goalie points that week are zeroed**.
- Weeks run Mon-Sun (NHL dates). Week 1 was Sep 29 - Oct 4 2026. Week 19 is
  the double week (Feb 1-14 2027, confirmed in Yahoo). A move's day is an NHL date:
  in Helsinki, during that day, before the evening's games.

## Working here (this machine)
- Windows; the Bash tool is Git Bash. Its heredocs turn `\n` inside Python strings into
  real newlines: write multi-line edit scripts to a file with the Write tool instead.
- Scratch scripts that import the project need `PYTHONPATH=.` (or run as `python -m scripts.x`).
- `clients.names.normalize_name` drops digits ("Hurt 0" -> "hurt"): mind it in test fixtures.
