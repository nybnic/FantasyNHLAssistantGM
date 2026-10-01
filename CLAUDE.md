# Fantasy NHL Assistant GM

A Telegram bot for one Yahoo H2H points league ("Not for everyone!", 16 teams).
The owner, Nico, manages "Nico's Groovy Team". **The goal is winning weekly
head-to-head matchups**, not maximizing season points. Every recommendation is
judged by what it does to this week's win odds, plus long-run roster value,
both in win probability: an add is made when it beats the add price
(`engine/addprice.py`), which paces the 36 adds.

Runs on GitHub Actions every 30 min (plus on each Telegram message via the
Cloudflare relay). Notify-only: there is no Yahoo API access, so Nico makes every
move in Yahoo, and the bot learns about them from Done/Taken taps and Yahoo app
screenshots (team pages, matchups, League > Transactions) or pasted pages.

## Map
| Path | What lives there |
|---|---|
| `main.py` | Entry point: Telegram commands and taps, the weekly plan step, the evening lineup step |
| `config/league.py` | **Single source of league rules**: scoring, slots, add limits, schedule, calendar |
| `model/` | Projections: skater xFP, goalie per-start model, team ratings. See `model/CLAUDE.md` |
| `engine/` | Decisions: daily lineup, availability, weekly matchup and add/drops. See `engine/CLAUDE.md` |
| `league/` | Rosters: mine (`roster.py`), other teams (`teams.py`), Yahoo text parser, fantasy weeks |
| `clients/` | Data: NHL APIs, DailyFaceoff (lines, goalies, projections), disk cache, screenshot OCR |
| `notify/telegram.py` | Bot API calls |
| `state/` | Bot-owned JSON, committed by every run (roster, league, gm_state) |
| `scripts/` | One-off tools: seeding, backtest, explain_week, compare_yahoo, xfp_table |
| `site/` | The dashboard: `index.html` (ours), `data.json` (written by each weekly plan), on GitHub Pages |
| `relay/` | Cloudflare Worker for instant Telegram replies |
| `docs/league-rules.md` | Full scoring and league rules, worked examples, where the points come from |
| `docs/operations.md` | Runtime, secrets, state ownership, past incidents |
| `docs/roadmap.md` | Prioritized backlog. Start a session here |

## Commands
```bash
python -m pytest                                  # all tests (fast, offline)
python main.py --dry-run --force                  # tonight's lineup + this week's plan, sends nothing
python main.py --dry-run --report 1               # week 1's result report (so far, if unfinished)
python main.py --dry-run --trade "Knight for Bouchard"   # what /trade would reply
python main.py --dry-run --trade                  # /trade alone: suggested trades (~1 min)
python -m scripts.explain_week                    # every add/drop the plan weighed, with verdicts
python -m scripts.scorecard                       # how the add suggestions turned out, made or skipped
python -m scripts.compare_yahoo paste.txt         # Yahoo projections vs ours, stat by stat
python -m scripts.backtest                        # model accuracy on 2025-26
python -m scripts.sim_add_policy                  # the add price vs the old rule, simulated seasons
python -m scripts.check_sigma                     # is P(win)'s spread right? 2025-26 replay
python -m scripts.fit_absence                     # how fast absent players return (availability curves)
```
Live runs need network. First runs fill `data/cache/` and take a few minutes.

## Rules
- **Evidence over intuition.** A model constant is either backtested (fit on one
  season, checked on the next) or labeled a judgment call in its comment. Report
  numbers, and say plainly when something is untested.
- **Settled decisions live in the decision log** (`engine/CLAUDE.md`). Don't reopen
  them without new evidence. Add an entry when Nico makes a call.
- **State is bot-owned.** `state/*.json` changes on every run, so run
  `git pull --rebase` before committing, and never hand-edit state while a
  run may be in flight.
- **Public repo.** No secrets, and no third-party data files (Yahoo exports,
  projection snapshots stay gitignored). One approved exception (Nico,
  2026-10-01): Yahoo position eligibility only, in `state/positions.json`
  (`scripts/seed_positions.py`). Error text must never contain the bot token.
- **Deploy = push to `main`.** The workflow runs from `main`. Nico has approved
  this flow: implement, run tests, dry run, then commit and push. Ask before
  destructive git (deleting branches or tags, force-push).
- **Telegram messages are for a phone.** Keep them short, lead with the action,
  and give numbers with context ("win 53% -> 61%").
- **Data from chat becomes data files.** When Nico pastes league data
  (screenshots, Yahoo pages), save it under `data/league/` or `state/`, not
  just in the conversation.

## Definition of done
1. Tests pass, with new behavior covered by a test.
2. A dry run (or `explain_week`) shows the real output, and it has been sanity-checked.
3. `docs/roadmap.md` is updated, plus the decision log if a rule changed.
4. The work is committed with a message that says why, and pushed.

## League facts worth knowing without reading code
Full rules: `docs/league-rules.md` (kept in sync with `config/league.py` by a test).
- Peripherals are about half of skater scoring: shots 0.5, hits 0.6, blocks 0.8,
  faceoffs 0.1. Blocks are the biggest single source for defensemen (27%).
- 12 starters (C2 LW2 RW2 D4 G2) + 2 bench + IR + IR+. Max 2 adds/week, 36/season.
- A goalie minimum of 3 games/week, or **all goalie points that week are zeroed**.
- Weeks run Mon-Sun (NHL dates). Week 1 was Sep 29 - Oct 4 2026. Week 19 is
  the double week (Feb 1-14 2027, confirmed in Yahoo).
