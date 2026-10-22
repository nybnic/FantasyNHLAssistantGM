# Design guidelines: how the bot looks

Decided with Nico on 2026-10-22, from mockups on real data
(https://claude.ai/artifact/J7qnYDAATTuCVNvaZaEL8v: card B, "night", chosen). Applies to the
Telegram text, the Telegram cards and the dashboard. The decision log entry: `engine/CLAUDE.md`,
2026-10-22 "The look".

## Principles
1. **Three depths.** Telegram text = the action (read in seconds); the card image = the glance;
   the dashboard = deciding and digging (a sheet per move and per player).
2. **The action is the hero.** Today's move is the biggest thing on any screen it appears on.
3. **Every mark prints its number.** No number only in a hover: a phone has none. A tap pins it.
4. **One vocabulary everywhere** (below), so a dot or a colour means the same on every screen.
5. **One design, one source of words.** Telegram images are screenshots of `site/index.html`
   (`?card`, `?card=move&key=`, `?card=result`); every sentence comes from `bot/messages.py`.

## The vocabulary
| Mark | Means |
|---|---|
| Blue | You. Orange: them. Aqua: the plan, an add, a value gained |
| `+` badge / `➕`, `−` badge / `➖` | The add / the drop |
| Filled dot | He dresses that night (in the best lineup) |
| Ring | He plays but sits: no slot free |
| Pie | A goalie's game, filled by his chance to start |
| Dashed aqua ring | A free agent who'd dress |
| Grey dot | A game whose lineup spot isn't solved (moves outside the plan) |
| Solid / dark bar | Banked so far / still to come |
| White line | The add price ("an add costs 13") |
| Day chip | `TODAY · THU 22` in aqua; a later day `MON 26` in light blue |

## Palette (night, dark only)
| Token | Hex | Use |
|---|---|---|
| surface | `#0B1422` | page, card background |
| card | `#13213A` | cards, tiles, tab bar |
| tile (now) | `#182A47` | today's move, the projected part of the race |
| grid | `#1E2E48` | rules, row lines |
| text / text-2 | `#FFFFFF` / `#B7C2D3` | text / secondary text |
| muted | `#6B7A90` | marks only, never text |
| blue (ink) | `#4D9BF0` (`#7DB8FF`) | you; fills (text) |
| orange (ink) | `#F07A45` (`#FFA57D`, score `#FFB08C`) | them |
| aqua | `#2CC28B` | plan, adds, values |
| dark fills | `#22508A`, `#7A3A1E` | "still to come" bars |
| kept | `#5A3A2A` | adds kept for the playoffs |

Text at WCAG 4.5:1 on every background, large figures and marks 3:1 (`tests/test_site.py`).
Team colours (`TEAM_COLORS`) are dark enough for white initials.

## Type and size
- **Barlow Condensed 700** for figures and labels that are numbers or days (scores, %, `+19`,
  day chips, week titles); **Inter** for everything else. Tabular numerals for figures.
- **Telegram cards:** 390 px wide (Telegram shows them at ~80%), **no text under 13 px**, sized to
  their content: the plan card at most 1.75 tall to 1 wide, the move and result cards 2.4.
- Dashboard: text 12 px or more; touch targets 44 px (the tab bar 54).

## Components (site/index.html)
- **Ring** (`ring`): the win odds, blue clockwise from the top, a white tick at 50%.
- **Score bars** (`scoreBars`): so far (solid) and to come (dark), both teams on one scale.
- **Face** (`avatar`): the NHL headshot (assets.nhle.com, loaded, never stored) on the team colour,
  the initials until it loads or if it can't; a `+`/`−` badge.
- **Face-off tile** (`todayTile`): add, arrow, drop, the value top right, the plan's own words below.
- **Plan rows** (`planRows`): day chip, `+ Add − Drop`, value.
- **Week tiles** (`weekTiles`): odds, opponent, margin; this week outlined.
- **Dots** (`dot`, `gameDots`): the vocabulary above.
- Cards: radius 16, no borders; tiles inside cards on the page colour.

## Telegram
- **Text** in HTML mode (`html=True` on the builders in `bot/messages.py`): the matchup in bold,
  the score in `<code>`, 🎯 the odds; the moves grouped by day (📅 the day in bold, ➕ **add** ➖ drop,
  then why); 💡 the week's best stream, 🔭 weeks ahead, 🎟️ budget, 🏆 odds, 🔄 a change, ⚠️ warnings.
  Escape every name and reason (`_esc`); real arrows (→). An HTML caption is never cut.
- **The plan card** (`nightCard`): week and time; ring + score bars; today's face-off; later moves;
  the three weeks. The "changed" and "this week alone" lines stay in the text.
- **A move's card** (`moveCard`, sent on its day with Done / Other drop / Taken / Skip, a
  **Why?** link to `#move=KEY` and Dashboard): the face-off with fantasy pts a game so far and
  projected (not ice time or games played); each game from the move's day through the week
  after next as a dot; where the value comes from (this week, each week ahead with its odds,
  later) against the price; the 4 biggest stats for a like-for-like swap.
- **Monday's result** (`resultCard`): WIN/LOSS, the score, Monday's forecast, the race day by day
  with the best night named, the moves and adds left.
- Fallbacks: no browser, or the move not in the data: the matplotlib chart / the text alone.

## The dashboard
- Five tabs in a bar at the bottom, remembered and linkable (`#today` ... `#watch`):
  **Today** (ring and scores, the plan, the race, why the win %, the nights, the weeks),
  **Matchup** (player by player with faces, a bar and dots; points by stat you-vs-them),
  **Moves** (top 8 as bars by when they pay vs the price; every move; the budget as 36 tokens),
  **Schedule** (a week at a time for this week and the next two; my lineup night by night,
  with the plan or as is, or by slot; could-stream free agents; then every team's games, last),
  **Watch** (the breakout watch).
- **"With plan / As is"** switches everything it touches: each player's nights (`plan_cells`), drops
  ending on their day, adds starting on theirs, the sums, and each week's odds (`win_plan`).
- **A move's sheet** opens with its move card, then only what the card leaves out (points to
  win-pts by week, flags, availability, season so far). A player's sheet heads with his face.
- **The race** is anchored on Yahoo's score from each matchup screenshot; box scores only split a
  gap between two screenshots (`weekly.race_days`).
- Install to the home screen (`site/manifest.webmanifest`, the ring as icon, `scripts/make_icons.py`).

## Before shipping a change to the look
1. `python -m pytest` with a browser (`python -m playwright install chromium`, or
   `CHROMIUM_EXECUTABLE`): the cards' readability (13 px, shape, moves named), every tab draws
   (no error, nothing wider than 390 px, a tap pins a number, a move's link opens its sheet),
   the contrast test.
2. `python -m scripts.snapshots`: look at every card and tab at 390 px.
3. Headshots and logos load only from GitHub or a phone (a cloud session can't reach them).
4. View data is a report change (`engine/report.py`, `bot/weekly.week_views` /
   `dashboard_data`), never a change to how moves are valued (the engine freeze).
