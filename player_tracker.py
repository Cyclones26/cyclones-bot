#!/usr/bin/env python3
"""
player_tracker.py
--------------------
Update #4: long-term, individual Player Tracker.

Unlike the roster/weekly scripts (which are scoped to "what happened to
the Cyclones roster recently"), this one follows specific players
indefinitely, even after they're promoted out of Brooklyn entirely --
e.g. once Mitch Voit is on the watchlist, it keeps tabs on him at
Binghamton, then Syracuse, then the Mets, then (best-effort) wherever he
ends up if he's ever traded or released, for as long as you keep running
this script.

Each run:
  0. PURGE: removes anyone in config.DO_NOT_TRACK_IDS from the watchlist
     (MLB rehab vets who technically appeared on the Cyclones roster but
     aren't development prospects -- e.g. A.J. Minter). Listing an id in
     config.py is all it takes; no manual JSON edits needed.
  1. SEED: pulls the config.TRACKER_SEED_ROSTER_TYPE (default
     "fullSeason") roster for TEAM_ID and adds any personId not already
     on the watchlist (and not in DO_NOT_TRACK_IDS). Once added, a
     player is never removed -- a one-way ratchet, by design.
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
         team's roster status field). For IL placements we additionally
         look up the player's recent transaction record and, when MLB's
         description names the injury ("right shoulder strain",
         "retroactive to July 10"), weave those details into the tweet.
         MLB doesn't always publish injury specifics for minor leaguers,
         so the tweet degrades to a plain IL notice when it doesn't.
  3. PROGRESS: once a week (gated by date, not by cron), tweets a short
     "how's he doing" stat line for every tracked player who has
     *graduated off* the Cyclones roster and is still active somewhere,
     IF they clear the same usage thresholds as weekly_summary.py.
     Current Cyclones are deliberately skipped here -- weekly_summary.py
     already covers them, and this avoids double-posting.

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
_tx_cache: Dict[int, list] = {}


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


def _lookup_il_transaction_note(person_id: int, team_id: Optional[int]) -> Optional[str]:
    """
    When a tracked player lands on the IL, try to find the matching
    transaction record from his current team's recent feed -- its
    description sometimes names the specific injury and the retroactive
    date, which makes for a much more informative tweet. Returns the raw
    description string, or None if nothing IL-ish is found (network
    hiccups, or MLB just didn't publish details).
    """
    if not team_id:
        return None
    if team_id not in _tx_cache:
        end = dt.date.today()
        start = end - dt.timedelta(days=config.IL_DETAIL_LOOKBACK_DAYS)
        try:
            _tx_cache[team_id] = mlb_api.get_transactions(
                team_id, start.isoformat(), end.isoformat()
            )
        except Exception as exc:
            logger.warning("Could not fetch transactions for team %s: %s", team_id, exc)
            _tx_cache[team_id] = []
    for tx in _tx_cache[team_id]:
        if (tx.get("person") or {}).get("id") != person_id:
            continue
        description = tx.get("description") or ""
        if "injured list" in description.lower():
            return description
    return None


def purge_do_not_track(players: Dict[str, Any]) -> int:
    """Removes anyone on the config.DO_NOT_TRACK_IDS blocklist from the watchlist."""
    removed = 0
    for person_id_str in list(players.keys()):
        try:
            person_id = int(person_id_str)
        except ValueError:
            continue
        if person_id in config.DO_NOT_TRACK_IDS:
            entry = players.pop(person_id_str)
            removed += 1
            logger.info(
                "Removed do-not-track player from watchlist: %s (id=%s)",
                entry.get("name", "Unknown"), person_id,
            )
    return removed


def seed_watchlist(players: Dict[str, Any]) -> int:
    """Adds any never-before-seen Cyclone to the watchlist (except DO_NOT_TRACK_IDS)."""
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
        if person_id in config.DO_NOT_TRACK_IDS:
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

    # Manually-requested alumni (config.EXTRA_TRACK_IDS): seeded exactly
    # like a rosteree; the first refresh fills in name/team/level.
    for person_id in config.EXTRA_TRACK_IDS:
        key = str(person_id)
        if key in players:
            continue
        players[key] = {
            "name": f"Player {person_id}",
            "_initialized": False,
            "addedDate": today,
        }
        added += 1
        logger.info("Added manually-tracked player id=%s to watchlist.", person_id)
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

    snapshot = {
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
        # Carried-over tracker state (rehab flag, feat dedupe, HR counter).
        "onRehab": old_snapshot.get("onRehab", False),
        "seasonHRTotal": old_snapshot.get("seasonHRTotal", 0),
        "lastFeatDate": old_snapshot.get("lastFeatDate"),
    }

    # Stamp the starting point the first time we ever see this player --
    # the season-end recap diffs current level against these.
    if old_snapshot.get("_initialized"):
        for key in ("initialTeamName", "initialLevelRank", "initialMlbDebutSeen"):
            if key in old_snapshot:
                snapshot[key] = old_snapshot[key]
    if "initialLevelRank" not in snapshot:
        if old_snapshot.get("_initialized"):
            # Pre-update snapshot with no initial* fields: this player was
            # seeded from the Brooklyn roster before the recap feature
            # existed, so his starting point is the Cyclones -- not
            # wherever he happens to be now.
            snapshot["initialTeamName"] = config.TEAM_NAME
            snapshot["initialLevelRank"] = config.SPORT_ID_LEVEL_RANK.get(13, 2)
            snapshot["initialMlbDebutSeen"] = False
        else:
            snapshot["initialTeamName"] = current_team_name
            snapshot["initialLevelRank"] = level_rank
            snapshot["initialMlbDebutSeen"] = snapshot["mlbDebutSeen"]

    return snapshot


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

        # Keep the rehab flag coherent: set it when a rehab assignment
        # starts, clear it when the player returns or moves on for real.
        if milestone["category"] == pm_mod.MILESTONE_REHAB_ASSIGNMENT:
            new_snapshot["onRehab"] = True
        elif milestone["category"] in (
            pm_mod.MILESTONE_REHAB_RETURN,
            pm_mod.MILESTONE_TRADED_ORG,
            pm_mod.MILESTONE_PROMOTED,
            pm_mod.MILESTONE_DEMOTED,
            pm_mod.MILESTONE_LATERAL_MOVE,
            pm_mod.MILESTONE_LEFT_AFFILIATED_BALL,
        ):
            new_snapshot["onRehab"] = False

        if milestone["category"] == pm_mod.MILESTONE_MLB_DEBUT:
            tweet_text = tweet_formatter.format_player_debut_tweet(player_name, milestone.get("team_name"))
        elif milestone["category"] == pm_mod.MILESTONE_PLACED_ON_IL:
            injury_note = _lookup_il_transaction_note(
                int(person_id_str), new_snapshot.get("currentTeamId")
            )
            tweet_text = tweet_formatter.format_player_milestone_tweet(
                player_name, milestone, injury_note=injury_note
            )
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

        season_stat = None
        try:
            season_stat = mlb_api.get_player_season_stats(
                int(person_id_str), group, config.SEASON, sport_id
            )
        except Exception as exc:
            logger.warning("No season stats for %s (%s)", snapshot.get("name"), exc)

        level_name = config.SPORT_ID_LEVEL_NAME.get(sport_id, "")
        tweet_text = tweet_formatter.format_player_progress_tweet(
            snapshot["name"], snapshot.get("currentTeamName", "their club"), level_name,
            is_pitcher, stat, season_stat=season_stat,
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


def process_feats(players: Dict[str, Any]) -> int:
    """
    Scans each tracked player's game log for standout single games
    (thresholds live in player_milestones.detect_game_feats) since the
    last check, plus "first HR of the season". Capped at
    config.FEAT_MAX_TWEETS_PER_RUN so a backlog can't flood the timeline.
    """
    posted = 0
    for person_id_str, snap in players.items():
        if posted >= config.FEAT_MAX_TWEETS_PER_RUN:
            break
        sport_id = snap.get("sportId")
        if not snap.get("currentTeamId") or not sport_id:
            continue

        is_pitcher = snap.get("primaryPosition") == "P"
        group = "pitching" if is_pitcher else "hitting"
        since = snap.get("lastFeatDate") or snap.get("addedDate") or dt.date.today().isoformat()

        time.sleep(POLITE_DELAY_SECONDS)
        try:
            splits = mlb_api.get_player_gamelog(int(person_id_str), group, config.SEASON, sport_id)
        except Exception as exc:
            logger.warning("Skipping feat check for %s (%s)", snap.get("name"), exc)
            continue

        feats = pm_mod.detect_game_feats(splits, is_pitcher, since)

        if not is_pitcher:
            hr_total = pm_mod.season_hr_total(splits)
            if snap.get("seasonHRTotal", 0) == 0 and hr_total > 0:
                hr_date = pm_mod.first_hr_date(splits)
                if hr_date and hr_date > since:
                    feats.insert(0, {"date": hr_date, "desc": "his first home run of the season"})
            snap["seasonHRTotal"] = hr_total

        snap["lastFeatDate"] = dt.date.today().isoformat()

        level_name = config.SPORT_ID_LEVEL_NAME.get(sport_id, "")
        for feat in feats:
            if posted >= config.FEAT_MAX_TWEETS_PER_RUN:
                break
            tweet_text = tweet_formatter.format_player_feat_tweet(
                snap["name"], snap.get("currentTeamName") or "his club",
                level_name, feat["date"], feat["desc"],
            )
            try:
                twitter_client.post_tweet(tweet_text)
                sheet_logger.log_tweet(
                    script="player_tracker",
                    category="BIG GAME",
                    subject=snap.get("name", "Unknown"),
                    tweet_text=tweet_text,
                )
            except twitter_client.TweetPostError as exc:
                logger.error("Failed to post feat for %s: %s", snap.get("name"), exc)
                continue
            posted += 1

    return posted


def process_season_recap(players: Dict[str, Any], tracker_state: Dict[str, Any]) -> int:
    """
    Once per season, on the first run on/after config.SEASON_RECAP_START
    (MM-DD), posts a development wrap built from the watchlist snapshots.
    """
    today = dt.date.today()
    try:
        month, day = (int(x) for x in config.SEASON_RECAP_START.split("-"))
        recap_start = dt.date(today.year, month, day)
    except ValueError:
        logger.warning("Bad SEASON_RECAP_START %r; skipping recap.", config.SEASON_RECAP_START)
        return 0

    if today < recap_start or tracker_state.get("last_season_recap") == config.SEASON:
        return 0

    posted = 0
    for tweet_text in tweet_formatter.format_season_recap_tweets(players, config.SEASON):
        try:
            twitter_client.post_tweet(tweet_text)
            sheet_logger.log_tweet(
                script="player_tracker",
                category="SEASON RECAP",
                subject=str(config.SEASON),
                tweet_text=tweet_text,
            )
        except twitter_client.TweetPostError as exc:
            logger.error("Failed to post season recap: %s", exc)
            return posted  # retry next run; state flag not set
        posted += 1

    tracker_state["last_season_recap"] = config.SEASON
    return posted


def run() -> int:
    tracker_state = player_tracker_state.load_tracker_state()
    players = tracker_state["players"]

    removed = purge_do_not_track(players)
    added = seed_watchlist(players)
    logger.info(
        "Watchlist size: %d (added %d, purged %d do-not-track this run).",
        len(players), added, removed,
    )

    milestones_posted = process_milestones(players)
    feats_posted = process_feats(players)
    progress_posted = process_progress_updates(players, tracker_state)
    recap_posted = process_season_recap(players, tracker_state)

    tracker_state["players"] = players
    player_tracker_state.save_tracker_state(tracker_state)

    sheet_logger.sync_tracked_players(players)

    logger.info(
        "Done. %d milestone, %d big-game, %d progress, %d recap tweet(s); watching %d player(s).",
        milestones_posted, feats_posted, progress_posted, recap_posted, len(players),
    )
    return 0


if __name__ == "__main__":
    sys.exit(run())
