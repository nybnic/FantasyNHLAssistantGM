# Data: what's needed, what's stored, what it's for

The bot has no Yahoo access: it knows the league only through Nico's taps and
screenshots. Everything else it fetches itself. This page lists both, where
each piece is stored, and what uses it. Keep it current when a source or a
stored field changes.

## 1. What Nico sends

| When | What | Feeds |
|---|---|---|
| Every recommendation | Done / Other drop / Taken / Skip | My roster, the add budget (36 a season, 2 a week), the add scorecard |
| **Monday, before the first game** | League > **All Matchups**, scrolled through all 8 matchups (the new week at 0.00) | Yahoo's forecast for all 16 teams (kept to check against ours), the week's pairings (season odds) |
| Monday (optional) | The same, with the week picker on last week | Yahoo's final for every team: the results both forecasts are checked against |
| Monday | League > **Standings** | Playoff and title odds |
| Monday, and when the plan asks | League > **Transactions**, back to the date the plan names | The other 15 rosters, free agents, how much each team streams |
| Monday before games, then any time | My **Matchup** tab, scrolled through both rosters | Both rosters and slots, the live score (sharper P(win)), Yahoo's first forecast of my matchup, Yahoo's per-player projections |
| Playoffs (weeks 24-26) | `/opp Team Name` | Who I'm playing |

### Sending as many screenshots as you like
Duplicates and extras are safe; each kind is checked before it changes anything:
- **Transactions**: every move is keyed, so overlapping screenshots apply nothing twice; an add learned twice counts once.
- **Matchup**: rows are read once per player. The week picker's label decides the week; scrolled screenshots
  without it take the label of the one sent with them. Another week's matchup changes no roster or score:
  a past week's score is kept as Yahoo's final (`results[w].yahoo_final`), and the rows are archived.
  Before any games, the top card's numbers are read as Yahoo's projection with the score 0 - 0
  ("Games Played 0/43" for both).
- **All Matchups**: each team's latest score replaces the previous one, and the first one seen is kept
  (`first`). Unlabeled scrolled screenshots take the week of the labeled one before them.
- **Standings**: older standings (fewer weeks played) never replace newer ones; the same week's add up.
- **Team pages**: without `/opp`, a page goes to the roster sharing at least half its players, else mine
  (a full reset of my own roster counts no adds when most players are new).
- Not handled: a single day's Matchup view. Its per-player numbers are that day's; the archive marks rows
  from the Matchup Totals view (`view` = totals), and only those are Yahoo's week projections.

## 2. What the bot fetches itself
- **NHL API**: schedule, rosters, box scores and game logs (the results, re-fetchable any time), standings, past seasons' stats.
- **DailyFaceoff**: line charts and injuries, confirmed starting goalies, preseason projections.

Kept in `data/cache/gm` between runs (GitHub's cache, not committed; current data expires in hours).

## 3. What is stored, and what uses it

**Public repo, `state/`, committed by every run** (our own numbers, plus Yahoo team totals and position eligibility, approved):

| File / field | Holds | Used for |
|---|---|---|
| `roster.json`, `league.json`, `positions.json` | All 16 rosters, waivers, how current league moves are, Yahoo eligibility | Every lineup, add and trade decision |
| `gm_state.json` `adds`, `decisions` | Every add made, every tap | The add budget; how suggested adds turned out (`scripts/scorecard.py`) |
| `results[week]` | Our plan's forecast of my matchup (first and latest), the final, live checks (Yahoo's score and projection vs box scores), `yahoo_first`, `yahoo_final` | Checking P(win) and its spread (`scripts/check_sigma.py`) |
| `league_weeks[week]` | Pairings; each team's Yahoo score and projection (latest, and `first`); `ours`: our forecast of all 16 teams at the week's first plan | Season odds; scoring our forecasts against Yahoo's, 16 team-weeks a week (roadmap item 7) |
| `standings` | W-L-T and points for | Playoff and title odds (`engine/season.py`) |
| `add_pools` | Every add candidate each plan weighed | Solving the add price that paces 36 adds (`engine/addprice.py`) |
| `league_adds` | Other teams' pickups | Opponent streaming profiles (roadmap item 5) |
| `day_rosters` | Mine and my opponent's roster before each day's first puck | Scoring each day with that day's players |
| `xfp_log.csv` | Each Monday, every NHL player: points per game, ice time, durability; goalies' start share, save %, points per start; everyone's expected points in the week (`week_games`, `week_xfp`) | Projection accuracy player by player against box scores (roadmap item 2); where we differ from Yahoo |

**Private repo `nybnic/FantasyNHLAssistantGM-data`** (third-party data; the workflow checks it out into `archive/` and pushes):

| Folder | Holds | Used for |
|---|---|---|
| `dfo_lines/YYYY/DATE.csv` | DFO line charts, all 32 teams, once a game day | Whether line and PP roles predict points; stash candidates (roadmap item 8) |
| `yahoo_matchup/YYYY/TIME.csv` | Every player row of my matchup screenshots: view, slot, points so far, Yahoo's projection, both teams | Yahoo's player projections against ours and the results |

Not stored: the screenshot images (only what's read from them).

## 4. Checking forecasts after the fact
From week 2 on, each week has, before any games: our forecast of all 16 teams and of every player, and
Yahoo's forecast of all 16 teams (All Matchups) and of my matchup's players. The results come from box
scores, and from Yahoo's finals where screenshots give them. Not built yet: the script that scores both
forecasts (roadmap item 7, after 3-4 weeks). Week 1 has no pre-game Yahoo numbers.
