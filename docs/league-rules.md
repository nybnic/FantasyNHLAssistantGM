# League rules and scoring: "Not for everyone!"

Yahoo Fantasy Hockey, head-to-head **points**, 16 teams (league id 66759).
Our team: **Nico's Groovy Team**. The code's copy of these rules is
`config/league.py`. When the commissioner changes anything, update both in the
same commit (`tests/test_league_rules.py` checks that the scoring tables match).

Final settings as of the draft, Sep 28 2026.

## Scoring

### Skaters
| Stat | Code | Points |
|---|---|---|
| Goal | `g` | 4 |
| Assist | `a` | 2.75 |
| Plus/minus | `pm` | 0.5 |
| Penalty minute | `pim` | 0.3 |
| Power-play goal | `ppg` | 0.75 |
| Power-play assist | `ppa` | 0.5 |
| Shorthanded goal | `shg` | 1.75 |
| Shorthanded assist | `sha` | 1.5 |
| Game-winning goal | `gwg` | 2 |
| Shot on goal | `sog` | 0.5 |
| Faceoff won | `fow` | 0.1 |
| Hit | `hit` | 0.6 |
| Blocked shot | `blk` | 0.8 |

**Bonuses stack.** A power-play goal also counts as a goal (and as a shot),
so its value adds up:

| Event | Points |
|---|---|
| Even-strength goal (incl. its shot) | 4 + 0.5 = **4.5** |
| Power-play goal | 4 + 0.75 + 0.5 = **5.25** |
| Shorthanded goal | 4 + 1.75 + 0.5 = **6.25** |
| Game-winning power-play goal | 4 + 0.75 + 2 + 0.5 = **7.25** |
| Power-play assist | 2.75 + 0.5 = **3.25** |
| Shorthanded assist | 2.75 + 1.5 = **4.25** |

Plus/minus scores both ways: -3 in a game is -1.5.

### Goalies
| Stat | Code | Points |
|---|---|---|
| Game started | `gs` | 1 |
| Win | `w` | 4 |
| Goal against | `ga` | -1 |
| Save | `sv` | 0.35 |
| Shutout | `so` | 5 |

Only starts earn the game-started point. Relief appearances still score their
saves, goals against and a possible win.

### Worked examples
| Line | Points |
|---|---|
| Skater: 1 PP goal, 1 assist, +1, 3 shots, 2 hits, 1 block | 4 + 0.75 + 2.75 + 0.5 + 1.5 + 1.2 + 0.8 = **11.5** |
| Center, a quiet night: 0 points, 2 shots, 12 faceoffs won, 1 hit | 1 + 1.2 + 0.6 = **2.8** |
| Defenseman, no points: 1 shot, 3 hits, 4 blocks, -1 | 0.5 + 1.8 + 3.2 - 0.5 = **5.0** |
| Goalie win: 30 saves, 2 goals against | 1 + 4 + 10.5 - 2 = **13.5** |
| Goalie shutout: 25 saves | 1 + 4 + 8.75 + 5 = **18.75** |
| Goalie loss: 25 saves, 5 goals against | 1 + 8.75 - 5 = **4.75** |

### Where the points come from (2025-26 game logs)
Share of all fantasy points for the rosterable pool (the top 64 C, 96 W and
96 D by points per game; 2,624 goalie starts):

| Group | Avg per game | Range | Biggest sources |
|---|---|---|---|
| Centers | 6.2 | 4.8-10.0 | goals 23%, assists 23%, shots 19%, hits 11%, **faceoffs 10%**, blocks 8% |
| Wingers | 5.3 | 4.0-8.5 | goals 25%, assists 22%, shots 22%, hits 15%, blocks 8% |
| Defense | 4.6 | 3.6-6.7 | **blocks 27%**, assists 23%, shots 18%, hits 14%, goals 10% |
| Goalies | 8.8 per start | 10th-90th pct 3.0-14.5 | saves 96%, win 22%, start 11%, shutout 2%, goals against -32% |

What this means:
- **Peripherals are about half the game:** shots, hits, blocks and faceoffs are
  45-48% of a forward's points and 59% of a defenseman's. A physical,
  shot-blocking defenseman or a faceoff center with modest scoring is often
  worth more than a points-only player.
- **Goalie value is volume.** A start averages 8.8 points and only about 2% of
  starts go negative, so getting a start matters far more than the matchup.
- Plus/minus, PIM, the PP/SH bonuses and GWG are each under 3%: they're noise.

## Roster and lineup
| Slot | Count |
|---|---|
| C | 2 |
| LW | 2 |
| RW | 2 |
| D | 4 |
| G | 2 |
| Bench (BN) | 2 |
| IR | 1 (statuses IR, IR-LT, IR-NR) |
| IR+ | 1 (also DTD and O) |

- 12 starters, 14 active spots, 16 roster spots in total. The draft was 14 rounds.
- Positions follow Yahoo eligibility (e.g. C,LW). A player can fill any slot he's eligible for.
- **Lineups are daily** ("Daily - Today"): changes apply the same day, and each
  player locks at his own game's start, so late games stay editable.
- Only players in starting slots score. Bench and IR players score nothing.

## Goalie minimum
**At least 3 goalie games per week** in active G slots, or **all goalie points
that week are zeroed**. Relief appearances count toward the 3, but only while
the goalie is in a G slot. Missing it almost certainly loses the matchup, which
is why we carry 3 goalies (decision log in `engine/CLAUDE.md`).

## Transactions
- **Adds:** max **2 per week**, **36 per season**.
- **Waivers:** a continual rolling list. Dropped players sit on waivers for 1 day;
  a successful claim moves you to the back of the priority order. Free agents
  not on waivers can be added instantly.
- After the draft, every undrafted player was on waivers until **Sep 30 2026**.
- An injured player can be added straight to an IR slot.
- **Trade deadline:** Mar 3 2027.

## Season calendar
- Fantasy weeks run **Monday-Sunday on NHL (Eastern) dates**.
- **Week 1: Tue Sep 29 - Sun Oct 4 2026** (a short week, confirmed in Yahoo).
- 26 weeks ending **Sun Apr 4 2027** (the NHL season runs to Apr 10; its last
  week doesn't count). That requires one two-week week, *assumed* to be
  **week 19 = Feb 1-14 2027**, spanning the early-February break. **Verify in Yahoo.**
- **Regular season:** weeks 1-23. **Playoffs:** weeks 24-26, 8 teams.

## Our regular-season schedule
| Wk | Opponent | Wk | Opponent | Wk | Opponent |
|---|---|---|---|---|---|
| 1 | Bahelin Boys | 9 | Bellova | 17 | Retrot Chicken Wings |
| 2 | Retrot Chicken Wings | 10 | Lazy Lew | 18 | Löllöt Höntsääjät |
| 3 | Löllöt Höntsääjät | 11 | Pastasauce | 19 | Jättiläisentie Giants |
| 4 | Jättiläisentie Giants | 12 | Gwp | 20 | Viktorios |
| 5 | Viktorios | 13 | Randy | 21 | HC Bulju |
| 6 | HC Bulju | 14 | Vanilla Thunder | 22 | HAN-NES |
| 7 | HAN-NES | 15 | Vantaa | 23 | Bottom three |
| 8 | Bottom three | 16 | Bahelin Boys | | |

## Not recorded yet (check League -> Settings in Yahoo)
- Tiebreakers for a tied week and for standings.
- Playoff seeding, reseeding and byes; whether there's a consolation bracket.
- Whether the 36-add limit covers the playoffs (the bot assumes yes and keeps 6 for them).
- Trade review (commissioner or league vote) and any veto period.
- Whether the draft shared picks or used keepers (not relevant in-season).
