"""
config.py
---------
Central configuration for the Brooklyn Cyclones X (Twitter) bot.

SECURITY NOTE: Never hardcode real API keys/secrets in this file.
Everything sensitive is pulled from environment variables, which you set:
  - Locally: a `.env` file (see .env.example) loaded by python-dotenv
  - On GitHub Actions: repo "Settings -> Secrets and variables -> Actions"
  - On PythonAnywhere: the "Environment variables" box on the Web/Tasks tab,
    or by exporting them in your scheduled task's bash wrapper.
"""

import os

try:
    # Loads a local .env file if present (no-op in CI, where secrets are
    # already injected as real environment variables).
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

# --------------------------------------------------------------------------
# Team identity
# --------------------------------------------------------------------------
# Brooklyn Cyclones, High-A affiliate of the New York Mets.
TEAM_ID = int(os.environ.get("MLB_TEAM_ID", "509"))
TEAM_NAME = os.environ.get("MLB_TEAM_NAME", "Brooklyn Cyclones")

# New York Mets MLB club id (used to look up the full affiliate ladder so we
# can tell "promotion" from "demotion" without hardcoding Binghamton's,
# Syracuse's, etc. team IDs -- those occasionally change and are safer to
# resolve live via /api/v1/teams/affiliates).
PARENT_MLB_TEAM_ID = int(os.environ.get("PARENT_MLB_TEAM_ID", "121"))

SEASON = int(os.environ.get("MLB_SEASON", "2026"))

# Fallback rank table if the live affiliate lookup ever fails. Higher number
# = higher level. Keyed by MLB "sportId" (confirmed via statsapi.mlb.com/api/v1/sports).
SPORT_ID_LEVEL_RANK = {
    1: 5,    # MLB
    11: 4,   # Triple-A
    12: 3,   # Double-A
    13: 2,   # High-A  <-- Brooklyn Cyclones play here
    14: 1,   # Single-A
    16: 0,   # Rookie / Complex League
}

# Human-readable labels for the same sportIds, for tweet text. This mapping
# is standardized across every MLB organization (not Mets-specific), which
# is what lets player_tracker.py follow a player to any other org's system.
SPORT_ID_LEVEL_NAME = {
    1: "MLB",
    11: "AAA",
    12: "AA",
    13: "High-A",
    14: "Single-A",
    16: "Rookie/Complex",
}

TEAM_HASHTAGS = os.environ.get("TEAM_HASHTAGS", "#Cyclones #LGM #MiLB")

# --------------------------------------------------------------------------
# MLB Stats API (public, no key required)
# --------------------------------------------------------------------------
MLB_API_BASE = "https://statsapi.mlb.com/api/v1"
REQUEST_TIMEOUT = 15  # seconds
REQUEST_RETRIES = 3

# --------------------------------------------------------------------------
# X (Twitter) API credentials
# --------------------------------------------------------------------------
# Create a Project + App at https://developer.x.com, generate "Access Token
# and Secret" with READ AND WRITE permissions (User authentication settings
# -> App permissions -> Read and write), then fill these via env vars.
X_API_KEY = os.environ.get("X_API_KEY", "YOUR_API_KEY_HERE")
X_API_SECRET = os.environ.get("X_API_SECRET", "YOUR_API_SECRET_HERE")
X_ACCESS_TOKEN = os.environ.get("X_ACCESS_TOKEN", "YOUR_ACCESS_TOKEN_HERE")
X_ACCESS_SECRET = os.environ.get("X_ACCESS_SECRET", "YOUR_ACCESS_TOKEN_SECRET_HERE")
# Bearer token is only needed if you later add read-only lookups; posting
# (create_tweet) uses the OAuth1 user-context credentials above.
X_BEARER_TOKEN = os.environ.get("X_BEARER_TOKEN", "YOUR_BEARER_TOKEN_HERE")

# When true, scripts print the tweet text instead of calling the X API.
# Use this for local testing so you don't burn real posts / spend money.
DRY_RUN = os.environ.get("DRY_RUN", "false").strip().lower() in ("1", "true", "yes")

# --------------------------------------------------------------------------
# State persistence (dedupe so we never post the same alert twice)
# --------------------------------------------------------------------------
STATE_FILE = os.environ.get("STATE_FILE", "state.json")

# How many days back to scan for transactions on each roster-alert run.
# Wider than 1 day on purpose so a missed/failed run still catches up.
TRANSACTION_LOOKBACK_DAYS = int(os.environ.get("TRANSACTION_LOOKBACK_DAYS", "3"))

# Minimum at-bats / innings pitched for someone to be eligible as the
# weekly "hot streak" hitter/pitcher (avoids crowning a 1-for-1 pinch hitter).
WEEKLY_MIN_AT_BATS = int(os.environ.get("WEEKLY_MIN_AT_BATS", "10"))
WEEKLY_MIN_OUTS_PITCHED = int(os.environ.get("WEEKLY_MIN_OUTS_PITCHED", "6"))  # 2.0 IP

# --------------------------------------------------------------------------
# Player Tracker (Update #4: long-term, individual player development)
# --------------------------------------------------------------------------
# Separate state file from STATE_FILE above -- this one tracks individual
# players (by MLB personId) indefinitely, across organizations, not just
# Cyclones-roster transactions/games. See player_tracker.py.
TRACKER_STATE_FILE = os.environ.get("TRACKER_STATE_FILE", "tracked_players.json")

# Which roster snapshot seeds the watchlist. "fullSeason" includes the
# active roster + IL/restricted/etc., so anyone who suits up for the 2026
# Cyclones gets tracked -- not just whoever happens to be active the moment
# this runs. Once added, a player is never removed from the watchlist, even
# after they leave the organization entirely.
TRACKER_SEED_ROSTER_TYPE = os.environ.get("TRACKER_SEED_ROSTER_TYPE", "fullSeason")
