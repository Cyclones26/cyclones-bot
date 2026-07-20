# Brooklyn Cyclones X Bot

Automated X (Twitter) bot for the Brooklyn Cyclones (High-A, New York Mets,
MLB Stats API team ID `509`), focused entirely on **player development**:

1. **Roster Transaction Alerts** — promotions, demotions, IL moves (with
   injury details when MLB publishes them).
2. **Weekly Performance Summaries** — which current Cyclones are trending
   up over the last 7 days.
3. **Player Tracker** — long-term, individual development tracking. Seeds
   itself from the 2026 Cyclones roster and then follows each of those
   players *indefinitely*: promotions, demotions, trades, MLB debuts,
   rehab assignments, "big game" performances, even a best-effort attempt
   to keep following someone after they leave the Mets organization
   entirely — plus a weekly "how's he doing now" stat line (7-day +
   season-to-date) once a player has graduated off the Brooklyn roster,
   and a season-end development wrap. See §1.5 and §1.7.

> **July 2026 format change:** the bot no longer tweets game results. The
> old Daily Post-Game Recap (`daily_recap.py` / `boxscore.py` /
> `daily_recap.yml`) has been removed, and every remaining template was
> rewritten around the player's development journey rather than the
> scoreboard.

All MLB data comes from the public, unofficial MLB Stats API
(`statsapi.mlb.com`) — no key required. Posting goes through the official
X API v2 via `tweepy`.

---

## 1. Endpoints used

Base URL: `https://statsapi.mlb.com/api/v1` (no auth, no published rate
limit — be a reasonable citizen of it anyway).

| Purpose | Endpoint | Notes |
|---|---|---|
| Team metadata / level | `GET /teams/{teamId}` | `teamId=509`. Returns `sport.id` (13 = High-A) so the rest of the code never hardcodes the level. |
| Affiliate ladder | `GET /teams/affiliates?teamIds=121&sportId=1` | `121` = NY Mets. Returns every affiliate (AAA/AA/HighA/A) so promotion vs. demotion is resolved live instead of hardcoding Binghamton's/Syracuse's team IDs. |
| Active roster | `GET /teams/509/roster?rosterType=active` | Used by the weekly summary to know who to check stats for. |
| **Transactions** | `GET /transactions?teamId=509&startDate=YYYY-MM-DD&endDate=YYYY-MM-DD` | The core feed for roster alerts. Returns `person`, `fromTeam`, `toTeam`, `typeDesc`, `description`, `id`. The free-text `description` is also where MLB (sometimes) names the specific injury on an IL move — see §1.6. |
| Player stats by date range | `GET /people/{personId}/stats?stats=byDateRange&group=hitting&startDate=&endDate=&season=&sportId=` | The core feed for weekly hot streaks. `group=pitching` for pitchers. |
| Player game log (fallback) | `GET /people/{personId}/stats?stats=gameLog&group=hitting&season=&sportId=` | Use if `byDateRange` ever breaks — sum games whose date falls in your window. Already wired up as `mlb_api.get_player_gamelog()`. |
| **Org-agnostic player lookup** | `GET /people/{personId}?hydrate=currentTeam,team` | The core feed for the Player Tracker. Returns whichever team currently employs this person — any organization, any level — plus `mlbDebutDate` once it exists. This is what lets the tracker keep following a player after he's traded or released, without needing to guess which org's roster to check. |
| Full-season roster (tracker seed) | `GET /teams/509/roster?rosterType=fullSeason` | Includes active + IL + restricted, so anyone who suits up for Brooklyn in 2026 gets added to the watchlist — not just whoever happens to be active the moment the seed runs. |

**Heads up:** this API is *unofficial* — it's not in MLB's published developer
portal, it powers MLB.com/the MLB app, and field names occasionally shift.
Every parser in this project reads with `.get()` and degrades gracefully
rather than crashing a scheduled run. Before going live, confirm `509` and
`sportId=13` still match "Brooklyn Cyclones" / "High-A" for the season
you're running — affiliate shuffles happen.

---

## 1.5 How the Player Tracker works

This is the "track Mitch Voit forever" feature. It's intentionally a
separate script with its own state file, because it operates on a
different axis than the other two: they're scoped to "what happened on
the 509 roster," while this one follows specific people regardless of
which roster they end up on.

**Seeding.** Every run, it pulls `rosterType=fullSeason` for team 509 and
adds any player not already on the watchlist (`tracked_players.json`).
This is a one-way ratchet — once added, a player is never removed, even
after he's promoted, traded, released, or retired. You don't maintain a
manual list; the 2026 roster *is* the list.

**The do-not-track list (July 2026).** The one exception to the ratchet:
`config.DO_NOT_TRACK_IDS`. `rosterType=fullSeason` sweeps in MLB veterans
on short rehab assignments (A.J. Minter, Robert Stock, Adbert Alzolay,
Kevin Herget, Grae Kessinger, Joe Jacques) who aren't Brooklyn
development prospects. Any personId in that set is (a) skipped during
seeding and (b) purged from the existing watchlist at the start of every
run — so adding an id to `config.py` (or the `DO_NOT_TRACK_IDS` env var,
comma-separated) is all it takes; no manual JSON surgery.

**Milestone detection.** Each run, every watchlisted player gets looked up
via the org-agnostic `/people/{id}?hydrate=currentTeam` endpoint, and the
result is diffed against the snapshot saved last run
(`player_milestones.classify_player_change()`). A real change posts one
of:
- **Promoted / demoted** — level changed within the same organization.
- **Traded** — the player's organization (`parentOrgName`) changed.
- **MLB debut** — `mlbDebutDate` newly present; gets its own
  extra-celebratory template and takes priority over everything else that
  run.
- **Left affiliated ball** — `currentTeam` disappeared entirely
  (released, retired, or signed overseas/independent — the API can't
  tell these apart, so the tweet stays neutral).
- **Placed on / activated from the IL** — best-effort only. The
  `/people` endpoint doesn't carry roster status, so this checks the
  player's *current team's* `rosterType=fullRoster` for a status code
  that looks IL-related. Good enough to catch most cases, not
  guaranteed to catch all of them. IL placements also get the injury-
  detail treatment described in §1.6.

If posting a milestone tweet fails (rate limit, transient API error),
that player's snapshot is deliberately **not** updated, so the same diff
gets retried next run instead of silently being lost.

**Weekly progress updates.** Once a week (gated by date, like
`weekly_summary.py`, so a manual re-run mid-week doesn't double-post),
every tracked player who has left the Cyclones roster — but is still
playing somewhere — gets checked against the same usage thresholds as
the weekly hot-streaks tweet (`WEEKLY_MIN_AT_BATS` / `WEEKLY_MIN_OUTS_PITCHED`)
over the trailing 7 days, and a short stat-line tweet goes out if he
qualifies. Current Cyclones are skipped here on purpose —
`weekly_summary.py` already covers them, and skipping avoids posting the
same player's stats twice.

**The honest limitation:** "best-effort outside the org" means exactly
that. The MLB Stats API only knows about affiliated MLB/MiLB baseball.
If a tracked player signs in Japan/Korea, goes independent, or simply
retires, `currentTeam` just stops resolving — the tracker correctly flags
that as "left affiliated ball" but can't follow him any further, because
there's no API call that would tell it where to look next.

---

## 1.6 IL injury details — what's possible and what isn't

When a player goes on the IL, the tweet now includes whatever detail MLB
publishes, pulled from the `/transactions` `description` field, e.g.:

> 🩹 INJURY UPDATE
>
> Tanner Witt has been placed on the injured list.
> 7-day IL (right elbow sprain), retroactive to July 10
>
> Get well soon. 💪

`transactions.extract_il_details()` parses three things out of the
free-text description when present: the IL length ("7-day"), the
retroactive date, and the injury itself (either inline — "...with a right
elbow sprain" — or as a trailing sentence — "...injured list. Right
shoulder strain."). `roster_alerts.py` gets this for free since it
already reads the transaction feed; `player_tracker.py` additionally
looks up the player's current team's recent transactions when its
roster-status diff detects an IL move, so even a tracked player who got
hurt at Binghamton or Syracuse gets the detail when it exists.

**The honest limitation:** MLB frequently does *not* publish the injury
type for minor leaguers — many MiLB IL transactions read simply "placed
on the 7-day injured list" with no cause. There is no MLB Stats API
endpoint that exposes MiLB injury specifics beyond this description
field. When the detail isn't there, the tweet degrades to a plain IL
notice rather than guessing.

---

## 1.7 Development extras (July 2026 update #2)

**Season-to-date lines.** Weekly progress tweets for graduates now show
both the trailing 7 days and the player's season line at his current
level (`stats=season`), so followers see the arc, not just the week.

**Big-game alerts.** Each run scans every tracked player's game log
(`stats=gameLog`) for standout single games since the last check:
2+ HR games, 4+ hit games, 10+ K starts, and a hitter's first HR of the
season. Capped at `FEAT_MAX_TWEETS_PER_RUN` (default 5) per run so a
backlog can't flood the timeline, and deduped per-player via a
`lastFeatDate` stamp in `tracked_players.json`.

**Rehab tracking.** A tracked player who was on the IL and then pops up
on the org's complex-league club (sportId 16) is classified as a
**rehab assignment** — not the "demotion" the raw level-diff would
suggest — and his return to a higher level posts a **road back
complete** tweet instead of a fake "promotion". The `onRehab` flag
persists in the snapshot between runs.

**Manual alumni tracking.** Set the `EXTRA_TRACK_IDS` env var (or
GitHub Actions secret/variable) to a comma-separated list of MLB
personIds to follow past Cyclones who graduated before the bot existed.
They're seeded like rosterees on the next run. `DO_NOT_TRACK_IDS` wins
if an id appears in both.

**Season-end wrap.** On the first run on/after `SEASON_RECAP_START`
(default `09-20`), the tracker posts a one-time recap: how many tracked
players climbed a level, debuted in MLB, were traded, or moved on —
plus a "biggest climbers" list. Starting points are stamped per player
(`initialTeamName`/`initialLevelRank`) the first time they're checked.

---

## 2. Project layout

```
cyclones_bot/
├── config.py                # all settings, pulled from env vars (incl. DO_NOT_TRACK_IDS)
├── mlb_api.py                # MLB Stats API HTTP wrapper
├── transactions.py           # classifies promotion/demotion/IL + extracts injury details
├── player_milestones.py      # classifies a tracked player's snapshot-to-snapshot diff
├── tweet_formatter.py         # turns structured data into tweet text + emojis
├── twitter_client.py          # tweepy wrapper, with DRY_RUN support
├── state_store.py             # JSON file tracking what's already been tweeted
├── player_tracker_state.py    # JSON file tracking watchlisted players
├── sheet_logger.py            # optional Google Sheet logging of every tweet
├── roster_alerts.py           # Update #1 entry point
├── weekly_summary.py          # Update #3 entry point
├── player_tracker.py          # Update #4 entry point
├── test_offline.py            # offline fixture-based sanity check (no network)
├── requirements.txt
├── .env.example
├── state.json                 # committed dedupe state
├── tracked_players.json       # committed watchlist
└── .github/workflows/          # GitHub Actions schedules for all three scripts
```

---

## 3. Setup

```bash
git clone <your repo>
cd cyclones_bot
pip install -r requirements.txt
cp .env.example .env
# edit .env with your real X API keys, leave DRY_RUN=true for now
```

Get X API credentials at <https://developer.x.com>:
1. Create a Project + App.
2. App settings → **User authentication settings** → enable OAuth 1.0a,
   set **App permissions to "Read and write"** (critical — the default is
   read-only and `create_tweet` will fail with a 403).
3. **Keys and tokens** tab → generate API Key & Secret, and Access Token &
   Secret *after* setting Read/Write permissions (regenerate them if you
   changed permissions after first creating the tokens).

Test without posting:
```bash
DRY_RUN=true python test_offline.py     # no network, validates the logic
DRY_RUN=true python roster_alerts.py    # hits the real MLB API, prints tweets
DRY_RUN=true python weekly_summary.py
DRY_RUN=true python player_tracker.py   # first run seeds the watchlist
```

Once those look right, set `DRY_RUN=false` (or remove it) and run for real.

---

## 4. Scheduling

### GitHub Actions (recommended — free, no server to babysit)

Workflows are already in `.github/workflows/`:
- `roster_alerts.yml` — every 3 hours
- `weekly_summary.yml` — Mondays at 13:00 UTC
- `player_tracker.yml` — Thursdays at 14:00 UTC (offset from the others so
  it doesn't race them over commits or the X rate limit)

Setup:
1. Push this repo to GitHub.
2. **Settings → Secrets and variables → Actions** → add `X_API_KEY`,
   `X_API_SECRET`, `X_ACCESS_TOKEN`, `X_ACCESS_SECRET`.
3. **Settings → Actions → General → Workflow permissions** → "Read and
   write permissions" (the workflows commit `state.json` or
   `tracked_players.json` back to the repo after each run so
   the bot never double-posts — GitHub Actions runners are thrown away
   after every job, so state has to live in the repo itself, not on disk).
4. Use the "Run workflow" button (`workflow_dispatch`) to trigger a manual
   test run before trusting the cron.
5. After adding anyone to `DO_NOT_TRACK_IDS`, trigger `player_tracker.yml`
   manually once — the purge happens at the start of the run, and the
   cleaned `tracked_players.json` gets committed back automatically.

### PythonAnywhere (alternative — simplest if you don't want GitHub Actions)

1. Upload the project folder (Files tab) or `git clone` it from a Bash console.
2. `pip install -r requirements.txt --user` in a Bash console.
3. **Tasks** tab → add three scheduled tasks (free accounts get one task,
   paid "Hacker" tier gets more — see PythonAnywhere's current pricing):
   - `python3.11 /home/you/cyclones_bot/roster_alerts.py`
   - `python3.11 /home/you/cyclones_bot/weekly_summary.py`
   - `python3.11 /home/you/cyclones_bot/player_tracker.py`
4. Set the times in PythonAnywhere's scheduler (it's UTC).
5. Set env vars either in a `.env` file in the project folder
   (`python-dotenv` picks it up automatically) or by exporting them at the
   top of each task's command.
6. Because the disk persists between runs here (unlike GitHub Actions),
   `state.json` just works — no commit-back step needed.

---

## 5. The X API "free tier" reality check

- **As of February 9, 2026, X retired self-serve Free/Basic/Pro subscriptions
  for new developers and moved to pay-per-use**: ~$0.015 per post created
  (jumping to $0.20 if the post contains a URL), ~$0.005 per post *read*,
  with a 2M-read/month cap before Enterprise (~$42k/mo) is required.
  Existing Basic ($200/mo, 50k posts) and Pro ($5,000/mo) subscribers keep
  their legacy plans, but new signups can't buy them anymore.
- **This bot doesn't need to read anything from X.** Every script
  only ever calls `client.create_tweet()` — the data comes from the MLB
  Stats API, not from X. The legacy free tier (and the new pay-per-use
  minimal allotment) is specifically *write*-capable, which is exactly
  the shape this bot needs.

At this bot's volume (well under 100 posts/month, now that game recaps
are gone) pay-per-use pricing works out to well under $2/month. Verify
current pricing at <https://developer.x.com/en/portal/products> — X has
changed these numbers more than once.

---

## 6. Verification before going live

- `python test_offline.py` — exercises the classification, IL-detail
  extraction, and tweet-formatting logic against fixed sample payloads
  (no network) and asserts on the output. Re-run after any code change.
- Run every script once with `DRY_RUN=true` against live MLB data and
  read the printed tweets for tone/length before flipping `DRY_RUN=false`.
- Spot-check `mlb_api.get_team(509)` and `mlb_api.get_affiliate_ladder(121)`
  once per season (affiliations and team IDs can change) to make sure the
  level-ranking logic in `transactions.py` still resolves correctly.
- For the Player Tracker specifically: run `player_tracker.py` twice in a
  row with `DRY_RUN=true`. The first run should log any watchlist
  seeding/purging with no tweets; the second run should be silent
  (nothing changed yet) — that confirms seeding, purging, and diffing all
  work before you're relying on it to catch a real promotion weeks later.

---

## Sources

- [Public MLB API documentation (pseudo-r)](https://github.com/pseudo-r/Public-MLB-API) — endpoint shapes, sportId values, parameter reference.
- [toddrob99/MLB-StatsAPI endpoints.py](https://github.com/toddrob99/MLB-StatsAPI/blob/master/statsapi/endpoints.py) — full endpoint catalog cross-reference.
- [X (Twitter) API Pricing 2026 — GetXAPI](https://www.getxapi.com/twitter-api-pricing) — 2026 pay-per-use rates and legacy tier closure dates.
