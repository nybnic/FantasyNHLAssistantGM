# Roadmap

Pick the top item unless Nico names another. Each item has a short "done when".
Move finished items to the log at the bottom, with the commit.

## Next
1. **`/myteam` paste**: paste my Yahoo team page to resync my roster *and slots*
   (moves made outside the bot, IR changes).
   Done when: slots are read from the paste (C/LW/RW/D/G/BN/IR/IR+), and a pasted
   roster replaces `state/roster.json` with a diff reply ("added X, dropped Y").
2. **Mid-week risk advice**: from Wednesday on, use points so far to advise
   protecting a lead (steady players, safe goalie starts) or chasing when behind
   (volume, extra goalie starts).
   Done when: `/week` mid-week shows the live score and one clear risk
   recommendation, and a test covers ahead vs behind.
3. **Keep all rosters fresh cheaply**: a paste of Yahoo's league Transactions
   page updates every team's adds and drops at once.
   Done when: pasting a transactions list moves players between teams and the
   pool in `state/league.json`, and the reply lists the changes.
4. **IR management**: flag an IR-eligible injured player sitting in an active
   slot, and a returning player who needs a spot (who to drop).
   Done when: the weekly plan and the evening briefing include an IR line when relevant.
5. **Sunday report**: the week's result vs projection, decisions taken or
   skipped, and the add budget.
   Done when: sent Sunday evening, with the same numbers `explain_week` would show.

## Later
- Streaming-goalie advice in light-schedule weeks (only if the 3-goalie policy changes).
- Yahoo projected GP as a durability input (needs Nico's OK to commit Yahoo data, or a paste command).
- Verify the week-19 dates in Yahoo, and the two remaining "Not recorded yet" rules in `docs/league-rules.md` (regular-season ties and standings tiebreakers, consolation bracket).
- Late regular season: weigh playoff seeding (the higher seed wins tied playoff weeks) when the bot plans weeks 20-23.
- Calibrate `MODEL_SD_SHARE` and the add threshold once 4-6 weeks of real results exist.
- Next year's draft: restore the tooling from tag `draft-2026`.

## Log
| Date | Done | Commit |
|---|---|---|
| 2026-09-29 | Weekly matchup plan, league data, /opp /taken /week, age curve | 812f9e6 |
| 2026-09-29 | Duplicate-tap race fix, token-safe errors | ec1ceda |
| 2026-09-29 | CLAUDE.md docs, explain_week, compare_yahoo | 75f63ec |
| 2026-09-29 | Draft tooling removed (tag `draft-2026`) | 5b5df12 |
| 2026-09-29 | League rules doc + test keeping it in sync with config | fe22d39 |
| 2026-09-29 | Yahoo settings page transcribed into the rules doc | e331164 |
| 2026-09-29 | `/trade` command (and `main.py --dry-run --trade "A for B"`) | 74fa6db |
| 2026-09-29 | `/trade` suggestions, roster balance and short-of-starters check | (this commit) |
