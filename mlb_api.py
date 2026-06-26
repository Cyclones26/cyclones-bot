"""
mlb_api.py
----------
Thin, dependency-light wrapper around the public MLB Stats API
(https://statsapi.mlb.com/api/v1). No API key is required for any of these
endpoints -- they power MLB.com / the MLB app and are widely used by the
fan-stats community. They are *unofficial* (not in MLB's published
developer portal), so endpoints can occasionally change shape; every
parser below reads defensively with .get() so a missing field degrades
gracefully instead of crashing your scheduled job.

Endpoint reference (all relative to MLB_API_BASE = statsapi.mlb.com/api/v1):

  GET /teams/{teamId}
      -> team metadata: name, sportId (level), league, parentOrgId.

  GET /teams/affiliates?teamIds={mlbClubId}&sportId=1
      -> every affiliate (AAA/AA/HighA/A/Rookie) of a parent MLB club.
         Used to dynamically resolve "is this transaction a promotion or
         a demotion?" instead of hardcoding affiliate team IDs that can
         change between seasons.

  GET /teams/{teamId}/roster?rosterType=active
      -> current active roster (40-man/active list). rosterType can also
         be "fullRoster", "40Man", "depthChart", etc.

  GET /transactions?teamId={teamId}&startDate=YYYY-MM-DD&endDate=YYYY-MM-DD
      -> promotions, demotions, IL moves, releases, signings for the team
         in the given date range. This is the core feed for Roster Alerts.

  GET /schedule?teamId={teamId}&sportId={sportId}&date=YYYY-MM-DD&hydrate=team,linescore,decisions
      -> games for a given team/date. Use this to discover last night's
         gamePk(s) before pulling the boxscore.

  GET /game/{gamePk}/boxscore
      -> full box score (every batter/pitcher stat line) for one game.
         This is the core feed for the Daily Post-Game Stat Line.

  GET /game/{gamePk}/linescore
      -> inning-by-inning score, useful for a "Final: 5-3 W" summary line.

  GET /people/{personId}/stats?stats=byDateRange&group=hitting&startDate=&endDate=&season=&sportId=
      -> aggregated hitting/pitching totals for one player over an
         arbitrary date window. This is the core feed for the Weekly
         Performance Summary ("hot streak over the last 7 days").
         If this ever 400s (MLB has been known to deprecate stats= values
         without notice), get_player_gamelog() below is the fallback path:
         pull the full game log and sum the games that fall in range.

  GET /people/{personId}/stats?stats=gameLog&group=hitting&season=&sportId=
      -> every individual game log entry for a player this season.

  GET /people/{personId}?hydrate=currentTeam,team
      -> player bio + whatever team currently employs them (currentTeam),
         their primary position, and mlbDebutDate (present only once they've
         debuted in the majors). Crucially, this is NOT scoped to one
         organization -- it works the same whether the player is still in
         the Mets system or has been traded/released/signed elsewhere. This
         is the core feed for the long-term Player Tracker (see
         player_tracker.py): diff currentTeam/level across runs to detect
         promotions, demotions, trades, releases, and MLB debuts for any
         player on the watchlist, indefinitely.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

import requests

import config

logger = logging.getLogger("mlb_api")

_session = requests.Session()
_session.headers.update({"User-Agent": "BrooklynCyclonesBot/1.0 (fan project)"})


def _get(path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """GET a path under MLB_API_BASE with simple retry/backoff."""
    url = f"{config.MLB_API_BASE}{path}"
    last_exc: Optional[Exception] = None
    for attempt in range(1, config.REQUEST_RETRIES + 1):
        try:
            resp = _session.get(url, params=params, timeout=config.REQUEST_TIMEOUT)
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as exc:
            last_exc = exc
            logger.warning("GET %s attempt %d/%d failed: %s", url, attempt, config.REQUEST_RETRIES, exc)
            time.sleep(1.5 * attempt)
    raise RuntimeError(f"Failed to GET {url} after {config.REQUEST_RETRIES} attempts: {last_exc}")


# --------------------------------------------------------------------------
# Team / affiliate metadata
# --------------------------------------------------------------------------

def get_team(team_id: int) -> Dict[str, Any]:
    data = _get(f"/teams/{team_id}")
    teams = data.get("teams") or []
    return teams[0] if teams else {}


def get_affiliate_ladder(parent_mlb_team_id: int) -> Dict[int, Dict[str, Any]]:
    """
    Returns {sportId: {"teamId": int, "name": str, "rank": int}} for every
    affiliate of the given MLB club, so callers can compare levels without
    hardcoding minor-league team IDs (those can change when an org
    switches MiLB partners).
    """
    data = _get("/teams/affiliates", params={"teamIds": parent_mlb_team_id, "sportId": 1})
    ladder: Dict[int, Dict[str, Any]] = {}
    for team in data.get("teams", []):
        sport_id = (team.get("sport") or {}).get("id")
        if sport_id is None:
            continue
        ladder[sport_id] = {
            "teamId": team.get("id"),
            "name": team.get("name"),
            "rank": config.SPORT_ID_LEVEL_RANK.get(sport_id, -1),
        }
    return ladder


def get_roster(team_id: int, roster_type: str = "active") -> List[Dict[str, Any]]:
    data = _get(f"/teams/{team_id}/roster", params={"rosterType": roster_type})
    return data.get("roster", [])


# --------------------------------------------------------------------------
# Individual player lookups (Player Tracker)
# --------------------------------------------------------------------------

def get_person(person_id: int) -> Dict[str, Any]:
    """
    Org-agnostic player lookup. Returns whatever team currently employs this
    person (any organization, any level) plus bio fields including
    mlbDebutDate once it exists. Used by player_tracker.py to follow a
    player's career indefinitely, even after they leave the Mets system.

    If the player has no current team (released, retired, playing
    independent/overseas ball outside MLB/MiLB's system), `currentTeam`
    will simply be absent -- callers should treat that as "can't track
    further" rather than an error, since that's the inherent limit of an
    MLB-affiliated-ball-only API.
    """
    data = _get(f"/people/{person_id}", params={"hydrate": "currentTeam,team"})
    people = data.get("people") or []
    return people[0] if people else {}


# --------------------------------------------------------------------------
# Transactions (Roster Alerts)
# --------------------------------------------------------------------------

def get_transactions(team_id: int, start_date: str, end_date: str) -> List[Dict[str, Any]]:
    data = _get(
        "/transactions",
        params={"teamId": team_id, "startDate": start_date, "endDate": end_date},
    )
    return data.get("transactions", [])


# --------------------------------------------------------------------------
# Schedule & box scores (Daily Stat Lines)
# --------------------------------------------------------------------------

def get_schedule_for_date(team_id: int, sport_id: int, date_str: str) -> List[Dict[str, Any]]:
    data = _get(
        "/schedule",
        params={
            "teamId": team_id,
            "sportId": sport_id,
            "date": date_str,
            "hydrate": "team,linescore,decisions",
        },
    )
    games: List[Dict[str, Any]] = []
    for date_block in data.get("dates", []):
        games.extend(date_block.get("games", []))
    return games


def get_boxscore(game_pk: int) -> Dict[str, Any]:
    return _get(f"/game/{game_pk}/boxscore")


def get_linescore(game_pk: int) -> Dict[str, Any]:
    return _get(f"/game/{game_pk}/linescore")


# --------------------------------------------------------------------------
# Player stats (Weekly Summaries)
# --------------------------------------------------------------------------

def get_player_stats_by_date_range(
    person_id: int, group: str, start_date: str, end_date: str, sport_id: int, season: int
) -> Dict[str, Any]:
    """
    Preferred path for weekly hot-streak math: ask MLB to aggregate a
    player's hitting/pitching totals over an arbitrary date window for us.
    `group` is "hitting" or "pitching".
    """
    data = _get(
        f"/people/{person_id}/stats",
        params={
            "stats": "byDateRange",
            "group": group,
            "startDate": start_date,
            "endDate": end_date,
            "sportId": sport_id,
            "season": season,
        },
    )
    splits = (data.get("stats") or [{}])[0].get("splits") or []
    return splits[0].get("stat", {}) if splits else {}


def get_player_gamelog(person_id: int, group: str, season: int, sport_id: int) -> List[Dict[str, Any]]:
    """
    Fallback path: full game-by-game log. Use this if byDateRange ever
    stops returning data (filter `splits[].date` to your window yourself).
    """
    data = _get(
        f"/people/{person_id}/stats",
        params={"stats": "gameLog", "group": group, "season": season, "sportId": sport_id},
    )
    splits = (data.get("stats") or [{}])[0].get("splits") or []
    return splits
