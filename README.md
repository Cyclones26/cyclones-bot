# Brooklyn Cyclones X Bot

Automated X (Twitter) bot for the Brooklyn Cyclones (High-A, New York Mets,
MLB Stats API team ID `509`), covering four update types:

1. **Roster Transaction Alerts** — promotions, demotions, IL moves.
2. **Daily Post-Game Stat Lines** — top hitter/pitcher from the box score.
3. **Weekly Performance Summaries** — hot streaks over the last 7 days.
4. **Player Tracker** — long-term, individual development tracking. Seeds
   itself from the 2026 Cyclones roster and then follows each of those
   players *indefinitely*: promotions, demotions, trades, MLB debuts, even
   a best-effort attempt to keep following someone after they leave the
   Mets organization entirely — plus a weekly "how's he doing now" stat
   line once a player has graduated off the Brooklyn roster. See §1.5.

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
| **Transactions** | `GET /transactions?teamId=509&startDate=YYYY-MM-DD&endDate=YYYY-MM-DD` | The core feed for roster alerts. Returns `person`, `fromTeam`, `toTeam`, `typeDesc`, `description`, `id`. |
| **Schedule** | `GET /schedule?teamId=509&sportId=13&date=YYYY-MM-DD&hydrate=team,linescore,decisions` | Finds yesterday's `gamePk`(s) + final score + W/SV decision IDs. |
| **Box score** | `GET /game/{gamePk}/boxscore` | The core feed for daily stat lines — full batting/pitching lines for every player. |
| Player stats by date range | `GET /people/{personId}/stats?stats=byDateRange&group=hitting&startDate=&endDate=&season=&sportId=` | The core feed for weekly hot streaks. `group=pitching` for pitchers. |
| Player game log (fallback) | `GET /people/{personId}/stats?stats=gameLog&group=hitting&season=&sportId=` | Use if `byDateRange` ever breaks — sum games whose date falls in your window. Already wired up as `mlb_api.get_player_gamelog()`. |
| **Org-agnostic player lookup** | `GET /people/{personId}?hydrate=currentTeam,team` | The core feed for the Player Tracker. Returns whichever team currently employs this person — any organization, any level — plus `mlbDebutDate` once it exists. This is what lets the tracker keep following a player after he's traded or released, without needing to guess which org's roster to check. |
| Full-season roster (tracker seed) | `GET /teams/509/roster?rosterType=fullSeason` | Includes active + IL + restricted, so anyone who suits up for Brooklyn in 2026 gets added to the watchlist — not just whoever happens to be active the moment the seed runs. |

**Heads up:** this API is *unofficial* — it's not in MLB's published developer
portal, it powers MLB.com/the MLB app, and field names occasionally shift.
Every parser in this project reads with `.get()` and degrades gracefully
rather than crashing a scheduled run. Before going live, run
`python verify_team.py` style sanity checks (or just `curl` the team and
schedule endpoints yourself) to confirm `509` and `sportId=13` still match
"Brooklyn Cyclones" / "High-A" for the season you're running — affiliate
shuffles happen.

---

## 1.5 How the Player Tracker works (Update #4)

This is the "track Mitch Voit forever" feature. It's intentionally a
separate script with its own state file, because it operates on a
different axis than the other three: they're all scoped to "what
happened on the 509 roster," while this one follows specific people
regardless of which roster they end up on.

**Seeding.** Every run, it pulls `rosterType=fullSeason` for team 509 and
adds any player not already on the watchlist (`tracked_players.json`).
This is a one-way ratchet — once added, a player is never removed, even
after he's promoted, traded, released, or retired. You don't maintain a
manual list; the 2026 roster *is* the list.

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
  guaranteed to catch all of them.

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

## 2. Project layout

```
cyclones_bot/
├── config.py                # all settings, pulled from env vars
├── mlb_api.py                # MLB Stats API HTTP wrapper
├── transactions.py           # classifies promotion/demotion/IL from raw tx data
├── player_milestones.py      # classifies a tracked player's snapshot-to-snapshot diff
├── boxscore.py                # finds top hitter/pitcher in a box score
├── tweet_formatter.py         # turns structured data into tweet text + emojis
├── twitter_client.py          # tweepy wrapper, with DRY_RUN support
├── state_store.py             # JSON file tracking what's already been tweeted (Updates 1-3)
├── player_tracker_state.py    # JSON file tracking watchlisted players (Update 4)
├── roster_alerts.py           # Update #1 entry point
├── daily_recap.py             # Update #2 entry point
├── weekly_summary.py          # Update #3 entry point
├── player_tracker.py          # Update #4 entry point
├── test_offline.py            # offline fixture-based sanity check (no network)
├── requirements.txt
├── .env.example
├── state.json                 # committed starting state (all empty)
├── tracked_players.json       # committed starting watchlist (empty until first seed run)
└── .github/workflows/          # GitHub Actions schedules for all four scripts
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
DRY_RUN=true python daily_recap.py --date 2026-06-20   # backfill a known game day
DRY_RUN=true python weekly_summary.py
DRY_RUN=true python player_tracker.py   # first run just seeds the watchlist, no tweets
```

Once those look right, set `DRY_RUN=false` (or remove it) and run for real.

---

## 4. Scheduling

### GitHub Actions (recommended — free, no server to babysit)

Workflows are already in `.github/workflows/`:
- `roster_alerts.yml` — every 3 hours
- `daily_recap.yml` — daily at 12:00 UTC
- `weekly_summary.yml` — Mondays at 13:00 UTC
- `player_tracker.yml` — Thursdays at 14:00 UTC (offset from the others so
  it doesn't race them over commits or the X rate limit)

Setup:
1. Push this repo to GitHub.
2. **Settings → Secrets and variables → Actions** → add `X_API_KEY`,
   `X_API_SECRET`, `X_ACCESS_TOKEN`, `X_ACCESS_SECRET`.
3. **Settings → Actions → General → Workflow permissions** → "Read and
   write permissions" (the workflows commit `state.json` (Updates 1-3) or
   `tracked_players.json` (Update 4) back to the repo after each run so
   the bot never double-posts — GitHub Actions runners are thrown away
   after every job, so state has to live in the repo itself, not on disk).
4. Use the "Run workflow" button (`workflow_dispatch`) to trigger a manual
   test run before trusting the cron. For `player_tracker.yml` specifically,
   that first manual run just seeds the watchlist from the current
   roster — it won't tweet anything until a *second* run sees a real
   change, so don't be alarmed when run #1 is silent on X.

### PythonAnywhere (alternative — simplest if you don't want GitHub Actions)

1. Upload the project folder (Files tab) or `git clone` it from a Bash console.
2. `pip install -r requirements.txt --user` in a Bash console.
3. **Tasks** tab → add four scheduled tasks (free accounts get one task,
   paid "Hacker" tier gets more — see PythonAnywhere's current pricing):
   - `python3.11 /home/you/cyclones_bot/roster_alerts.py`
   - `python3.11 /home/you/cyclones_bot/daily_recap.py`
   - `python3.11 /home/you/cyclones_bot/weekly_summary.py`
   - `python3.11 /home/you/cyclones_bot/player_tracker.py`
4. Set the times in PythonAnywhere's scheduler (it's UTC).
5. Set env vars either in a `.env` file in the project folder
   (`python-dotenv` picks it up automatically) or by exporting them at the
   top of each task's command, e.g.
   `export X_API_KEY=... && python3.11 .../roster_alerts.py`.
6. Because the disk persists between runs here (unlike GitHub Actions),
   `state.json` just works — no commit-back step needed.

---

## 5. The X API "free tier" reality check (read this before you build further)

You asked whether the free X API tier is workable, or whether this needs
Make.com/Zapier instead. Two things changed this calculus that are easy to
miss if your mental model of the API is from a year or two ago:

- **As of February 9, 2026, X retired self-serve Free/Basic/Pro subscriptions
  for new developers and moved to pay-per-use**: ~$0.015 per post created
  (jumping to $0.20 if the post contains a URL), ~$0.005 per post *read*,
  with a 2M-read/month cap before Enterprise (~$42k/mo) is required.
  Existing Basic ($200/mo, 50k posts) and Pro ($5,000/mo) subscribers keep
  their legacy plans, but new signups can't buy them anymore.
- **This bot doesn't need to read anything from X.** All three scripts
  only ever call `client.create_tweet()` — the data comes from the MLB
  Stats API, not from X. That's the detail that matters: the legacy free
  tier (and the new pay-per-use free allotment) is specifically *write*-
  capable with negligible read access, which is exactly the shape this bot
  needs.

**What that means for your actual usage**: roughly 1 roster alert every
few days + 1 daily recap (~30/month in season) + 4 weekly summaries =
well under 100 posts/month. On pay-per-use pricing that's roughly
$0.50–$2/month even before any free allotment. **Pure code is not blocked
by X's API tier for this use case** — the friction people usually mean by
"free tier limitations" is about *reading* tweets/timelines/mentions at
scale, which this project never does.

The thing actually worth double-checking before you commit to this: verify
current pricing/tiers yourself at <https://developer.x.com/en/portal/products>
right before signing up, since X has changed this terminology and these
numbers more than once in the last two years.

---

## 6. If you'd rather not run code: Make.com / Zapier blueprint

Even though the X side isn't the blocker, a no-code path is still
reasonable if you don't want to maintain Python/GitHub Actions. Here's how
the three updates map onto Make.com modules (Zapier's equivalent modules —
Webhooks/Code by Zapier, Filter, Paths, Storage by Zapier, Twitter —
are functionally the same shape).

**Be honest with yourself about where no-code gets awkward**: scenarios
1 and 3 below are genuinely easy in Make. Scenario 2 (finding the *top*
hitter/pitcher out of a ~20-player box score) requires sorting an array
and grabbing the max — Make can do this with its Array Aggregator module
sorted descending + a "first item" grab, but it's fiddly compared to one
`sort()` call in Python. If you only build one of the three in no-code,
build #1 and #3 there and keep #2 as the Python script.

### Scenario 1 — Roster Transaction Alerts
1. **Schedule** trigger, every 3 hours.
2. **HTTP → Make a request**: `GET https://statsapi.mlb.com/api/v1/transactions?teamId=509&startDate={{formatDate(addDays(now;-3);"YYYY-MM-DD")}}&endDate={{formatDate(now;"YYYY-MM-DD")}}`
3. **JSON → Parse JSON** the response body.
4. **Iterator** over `transactions[]`.
5. **Data Store → Search records**: check if this transaction `id` is
   already stored (your "seen" table) — **Filter** to continue only if not found.
6. **Router** with 3 routes filtered on `typeDesc`/`description` containing
   "Injured List", or `toTeam.id`/`fromTeam.id` matching `509`, to pick a
   tweet template per branch (mirrors `transactions.py`'s logic).
7. **Twitter/X → Create a Tweet** with the composed text.
8. **Data Store → Add record**: save the transaction `id` so step 5 skips it next time.

### Scenario 2 — Daily Post-Game Stat Lines
1. **Schedule** trigger, daily ~8am local.
2. **HTTP**: `GET /schedule?teamId=509&sportId=13&date={{yesterday}}&hydrate=team,linescore,decisions` → **Filter**: `status.abstractGameState = Final`.
3. **HTTP**: `GET /game/{{gamePk}}/boxscore`.
4. **JSON → Parse JSON**, then **Array Aggregator** twice — once over the
   batters with `stats.batting.atBats > 0` (sort by a custom "score"
   field you compute with a **Set variable** step beforehand: `hits + 3*HR + RBI`),
   once over pitchers similarly — each sorted descending, taking the
   first item.
   *(This is the step that's genuinely easier in Python — see above.)*
5. **Text aggregator** to compose the tweet string with emojis.
6. **Twitter/X → Create a Tweet**.
7. **Data Store**: mark `gamePk` as done.

### Scenario 3 — Weekly Performance Summaries
1. **Schedule** trigger, weekly (Monday).
2. **HTTP**: `GET /teams/509/roster?rosterType=active` → **Iterator** over roster.
3. Inside the iterator, **HTTP**: `GET /people/{{personId}}/stats?stats=byDateRange&group=hitting&startDate={{7 days ago}}&endDate={{yesterday}}&season=2026&sportId=13`.
4. **Filter**: `atBats >= 10`.
5. **Array Aggregator**: collect all qualifying hitters, sorted by `avg` descending; take the top 1-2.
6. Repeat 3-5 for pitchers (`group=pitching`, filter on innings pitched, sort by `era` ascending).
7. **Text aggregator** → **Twitter/X → Create a Tweet**.

For both tools, the "Twitter"/"X" native module handles OAuth for you —
no manual key-juggling like the Python path, which is the main ergonomic
win of going no-code.

---

## 7. Verification before going live

- `python test_offline.py` — exercises the classification, box-score
  scoring, and tweet-formatting logic against fixed sample payloads
  (no network) and asserts on the output. Re-run after any code change.
- Run every script once with `DRY_RUN=true` against live MLB data and
  read the printed tweets for tone/length before flipping `DRY_RUN=false`.
- Spot-check `mlb_api.get_team(509)` and `mlb_api.get_affiliate_ladder(121)`
  once per season (affiliations and team IDs can change) to make sure the
  level-ranking logic in `transactions.py` still resolves correctly.
- For the Player Tracker specifically: run `player_tracker.py` twice in a
  row with `DRY_RUN=true` shortly after your first real season roster is
  set. The first run should only log "added new player to watchlist" with
  no tweets; the second run should also be silent (nothing changed yet) —
  that confirms seeding and diffing both work before you're relying on it
  to catch a real promotion weeks later.

---

## Sources

- [Public MLB API documentation (pseudo-r)](https://github.com/pseudo-r/Public-MLB-API) — endpoint shapes, sportId values, parameter reference.
- [toddrob99/MLB-StatsAPI endpoints.py](https://github.com/toddrob99/MLB-StatsAPI/blob/master/statsapi/endpoints.py) — full endpoint catalog cross-reference.
- [X (Twitter) API Pricing 2026 — GetXAPI](https://www.getxapi.com/twitter-api-pricing) — 2026 pay-per-use rates and legacy tier closure dates.
- [Understanding the read limit for Twitter API's Free Tier — X Developer Community](https://devcommunity.x.com/t/understanding-the-read-limit-for-twitter-apis-free-tier/193867)
