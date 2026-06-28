#!/usr/bin/env python3
"""
player_tracker.py
--------------------
Update #4: long-term, individual Player Tracker.

Unlike the other three scripts (which are scoped to "what happened to the
509 roster recently"), this one follows specific players indefinitely,
even after they're promoted out of Brooklyn entirely -- e.g. you call this
up once Mitch Voit is added to the watchlist, and it keeps tabs on him at
Binghamton, then Syracuse, then the Mets, then (best-effort) wherever he
ends up if he's ever traded or released, for as long as you keep running
this script.

Each run:
  1. SEED: pulls the {config.TRACKER_SEED_ROSTER_TYPE} (default
     "fullSeason") roster for TEAM_ID and adds any personId not already on
     the watchlist. Once added, a player is never removed -- this is a
     one-way ratchet, by design (you asked to track the 2026 roster
     "forever").
  2. REFRESH: for every watchlisted player, calls the org-agnostic
     mlb_api.get_person() to find their *current* team/level/org, and
     diffs that against the snapshot saved last run (player_milestones.py
     does the diffing). A real change tweets a milestone:
       - promoted / demoted within the same org
       - traded to a different org
       - left affiliated ball entirely (released/retired/overseas --
         best-effort, the API just stops returning a currentTeam)
       - MLB debut (special, extra-celebratory template)
       - placed on / activated from the IL (best-effort, via the current
         team's roster status field, since the player endpoint itself
         doesn't carry this)
  3. PROGRESS: once a week (gated like weekly_summary.py, by date not by
     cron, so a manual workflow_dispatch re-run the same week is a no-op),
     tweets a short "how's he doing" stat line for every tracked player
     who has *graduated off* the Cyclones roster and is still active
     somewhere, IF they clear the same usage thresholds as
     weekly_summary.py. Current Cyclones are deliberately skipped here --
     weekly_summary.py already covers them, and this avoids double-posting.

Usage:
    python player_tracker.py
    DRY_RUN=true python player_tracker.py
"""

from __future__ import annotations

import datetime as dt
import logging
import sys
import time
from typing import Any, Dict, Optional

import config
import mlb_api
import player_milestones as pm_mod
import player_tracker_state
import tweet_formatter
import twitter_client
import sheet_logger

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("player_tracker")

POLITE_DELAY_SECONDS = 0.2

# Per-run caches (not persisted) so we don't re-fetch the same team's
# metadata/roster-status once for every player who happens to share it.
_team_info_cache: Dict[int, Dict[str, Any]] = {}
_status_cache: Dict[int, Dict[int, str]] = {}


def _get_team_info(team_id: int) -> Dict[str, Any]:
    if team_id not in _team_info_cache:
        try:
            _team_info_cache[team_id] = mlb_api.get_team(team_id)
        except Exception as exc:
            logger.warning("Could not fetch team %s metadata: %s", team_id, exc)
            _team_info_cache[team_id] = {}
    return _team_info_cache[team_id]


def _get_player_status(team_id: int, person_id: int) -> str:
    """
    Best-effort IL detection: the /people endpoint doesn't carry roster
    status, so we check the player's *current team's* full roster for a
    status code/description that looks IL-related. Degrades to "ACTIVE"
    on any failure or ambiguity rather than risk a false IL tweet.
    """
    if team_id not in _status_cache:
        status_map: Dict[int, str] = {}
        try:
            roster = mlb_api.get_roster(team_id, roster_type="fullRoster")
        except Exception as exc:
            logger.warning("Could not fetch roster status for team %s: %s", team_id, exc)
            roster = []
        for entry in roster:
            pid = (entry.get("person") or {}).get("id")
            if pid is None:
                continue
            status = entry.get("status") or {}
            code = (status.get("code") or "").upper()
            desc = (status.get("description") or "").lower()
            is_il = code.startswith("IL") or code.startswith("D") or "injured" in desc or "disabled" in desc
            status_map[pid] = "IL" if is_il else "ACTIVE"
        _status_cache[team_id] = status_map
    return _status_cache[team_id].get(person_id, "ACTIVE")


def seed_watchlist(players: Dict[str, Any]) -> int:
    """Adds any never-before-seen Cyclone to the watchlist. Never removes anyone."""
    try:
        roster = mlb_api.get_roster(config.TEAM_ID, roster_type=config.TRACKER_SEED_ROSTER_TYPE)
    except Exception as exc:
        logger.warning("Could not fetch seed roster (%s); skipping seeding this run.", exc)
        return 0

    added = 0
    today = dt.date.today().isoformat()
    for entry in roster:
        person = entry.get("person") or {}
        person_id = person.get("id")
        if person_id is None:
            continue
        key = str(person_id)
        if key in players:
            continue
        players[key] = {
            "name": person.get("fullName", "Unknown"),
            "_initialized": False,
            "addedDate": today,
        }
        added += 1
        logger.info("Added new player to watchlist: %s (id=%s)", person.get("fullName"), person_id)
    return added


def refresh_player(person_id: int, old_snapshot: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Fetches the current snapshot for one player and returns it (does NOT
    write to the watchlist -- caller decides whether/when to commit it,
    so a failed tweet can leave the old snapshot in place for a retry).
    Returns None if the lookup itself failed (network error, deleted
    personId, etc.) -- caller should just skip this player this run.
    """
    try:
        person_data = mlb_api.get_person(person_id)
    except Exception as exc:
        logger.warning("Could not fetch person %s: %s", person_id, exc)
        return None
    if not person_data:
        return None

    current_team = person_data.get("currentTeam") or {}
    current_team_id = current_team.get("id")
    current_team_name = current_team.get("name")

    sport_id = None
    parent_org_name = None
    if current_team_id:
        team_info = _get_team_info(current_team_id)
        sport_id = (team_info.get("sport") or {}).get("id")
        # MiLB teams carry parentOrgName (e.g. "New York Mets"); an MLB
        # club has no parent, so treat its own name as "the organization"
        # for trade-detection purposes.
        parent_org_name = team_info.get("parentOrgName") or team_info.get("name")

    level_rank = config.SPORT_ID_LEVEL_RANK.get(sport_id, old_snapshot.get("levelRank"))

    status_str = "ACTIVE"
    if current_team_id:
        status_str = _get_player_status(current_team_id, person_id)

    return {
        "name": person_data.get("fullName") or old_snapshot.get("name", "Unknown"),
        "currentTeamId": current_team_id,
        "currentTeamName": current_team_name,
        "sportId": sport_id,
        "levelRank": level_rank,
        "parentOrgName": parent_org_name,
        "primaryPosition": (person_data.get("primaryPosition") or {}).get("abbreviation"),
        "mlbDebutSeen": bool(person_data.get("mlbDebutDate")),
        "lastStatus": status_str,
        "addedDate": old_snapshot.get("addedDate", dt.date.today().isoformat()),
        "lastCheckedAt": dt.datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "_initialized": True,
    }


def process_milestones(players: Dict[str, Any]) -> int:
    posted = 0
    for person_id_str, old_snapshot in list(players.items()):
        time.sleep(POLITE_DELAY_SECONDS)
        new_snapshot = refresh_player(int(person_id_str), old_snapshot)
        if new_snapshot is None:
            continue  # lookup failed; leave watchlist entry untouched, retry next run

        milestone = pm_mod.classify_player_change(old_snapshot, new_snapshot)
        player_name = new_snapshot["name"]

        if milestone is None:
            players[person_id_str] = new_snapshot
            continue

        logger.info("Milestone for %s: %s", player_name, milestone["category"])
        if milestone["category"] == pm_mod.MILESTONE_MLB_DEBUT:
            tweet_text = tweet_formatter.format_player_debut_tweet(player_name, milestone.get("team_name"))
        else:
            tweet_text = tweet_formatter.format_player_milestone_tweet(player_name, milestone)

        try:
            twitter_client.post_tweet(tweet_text)
            sheet_logger.log_tweet(
                script="player_tracker",
                category="MILESTONE",
                subject=player_name,
                tweet_text=tweet_text,
            )
        except twitter_client.TweetPostError as exc:
            logger.error("Failed to post milestone for %s: %s", player_name, exc)
            continue  # keep old_snapshot so next run retries the same diff

        players[person_id_str] = new_snapshot
        posted += 1

    return posted


def process_progress_updates(players: Dict[str, Any], tracker_state: Dict[str, Any]) -> int:
    end_date = dt.date.today() - dt.timedelta(days=1)
    start_date = end_date - dt.timedelta(days=6)
    start_str, end_str = start_date.isoformat(), end_date.isoformat()

    if tracker_state.get("last_progress_window_end") == end_str:
        logger.info("Already posted progress updates through %s, skipping.", end_str)
        return 0

    posted = 0
    for person_id_str, snapshot in players.items():
        team_id = snapshot.get("currentTeamId")
        if not team_id or team_id == config.TEAM_ID:
            # No current team (left the game) or still a current Cyclone
            # (weekly_summary.py already covers current-roster hot streaks).
            continue

        sport_id = snapshot.get("sportId")
        if not sport_id:
            continue

        is_pitcher = snapshot.get("primaryPosition") == "P"
        group = "pitching" if is_pitcher else "hitting"

        time.sleep(POLITE_DELAY_SECONDS)
        try:
            stat = mlb_api.get_player_stats_by_date_range(
                int(person_id_str), group, start_str, end_str, sport_id, config.SEASON
            )
        except Exception as exc:
            logger.warning("Skipping progress check for %s (%s)", snapshot.get("name"), exc)
            continue

        if is_pitcher:
            ip_str = stat.get("inningsPitched", "0.0") or "0.0"
            whole, _, frac = str(ip_str).partition(".")
            outs = int(whole or 0) * 3 + int(frac or 0)
            if outs < config.WEEKLY_MIN_OUTS_PITCHED:
                continue
        else:
            at_bats = int(stat.get("atBats", 0) or 0)
            if at_bats < config.WEEKLY_MIN_AT_BATS:
                continue

        level_name = config.SPORT_ID_LEVEL_NAME.get(sport_id, "")
        tweet_text = tweet_formatter.format_player_progress_tweet(
            snapshot["name"], snapshot.get("currentTeamName", "their club"), level_name, is_pitcher, stat
        )

        try:
            twitter_client.post_tweet(tweet_text)
            sheet_logger.log_tweet(
                script="player_tracker",
                category="PROGRESS UPDATE",
                subject=snapshot.get("name", "Unknown"),
                tweet_text=tweet_text,
            )
        except twitter_client.TweetPostError as exc:
            logger.error("Failed to post progress update for %s: %s", snapshot.get("name"), exc)
            continue

        posted += 1

    tracker_state["last_progress_window_end"] = end_str
    return posted


def run() -> int:
    tracker_state = player_tracker_state.load_tracker_state()
    players = tracker_state["players"]

    added = seed_watchlist(players)
    logger.info("Watchlist size: %d (added %d new this run).", len(players), added)

    milestones_posted = process_milestones(players)
    progress_posted = process_progress_updates(players, tracker_state)

    tracker_state["players"] = players
    player_tracker_state.save_tracker_state(tracker_state)

    logger.info(
        "Done. %d milestone tweet(s), %d progress tweet(s), watching %d player(s).",
        milestones_posted, progress_posted, len(players),
    )
    return 0


if __name__ == "__main__":
    sys.exit(run())
