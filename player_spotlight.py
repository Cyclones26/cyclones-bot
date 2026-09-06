#!/usr/bin/env python3
"""
player_spotlight.py
---------------------
Biweekly "get to know a Cyclone (or alum)" bio post.

Every other Tuesday, picks the next player from the full Player Tracker
watchlist (tracked_players.json -- current roster AND alumni who've moved
up or out) and tweets a short bio: current team/level, birthday (live from
MLB's Stats API), plus whatever hand-entered High School / Draft-or-Signed
info / Signed Date / Fun Fact is filled in on the "Player Bios" sheet tab
(see bio_sheet.py).

Rotation:
  - Players are sorted by (addedDate, name) for a stable order that's
    consistent across runs even as the watchlist grows.
  - Starting from spotlight_state.json's rotation_index, walks forward
    (wrapping around) until it finds a player who actually HAS bio data
    filled in -- so partially-populated sheets don't produce blank tweets.
    If nobody has bio data yet, the run skips cleanly and logs a reminder
    to fill in the sheet.
  - On success, rotation_index advances past the posted player so next
    run starts fresh (no repeats until the whole list has cycled).

Cadence:
  - The GitHub Actions workflow's cron fires every Tuesday, but this script
    only actually posts if config.SPOTLIGHT_MIN_DAYS_BETWEEN days have
    passed since the last spotlight post (see config.py for why this beats
    computing week parity).

Usage:
    python player_spotlight.py
    DRY_RUN=true python player_spotlight.py
"""

from __future__ import annotations

import datetime as dt
import logging
import sys
from typing import Any, Dict, List, Tuple

import bio_sheet
import config
import mlb_api
import player_tracker_state
import sheet_logger
import spotlight_state as ss_mod
import tweet_formatter
import twitter_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("player_spotlight")


def _format_birthday(birth_date: str) -> str:
    """'2001-11-17' -> 'November 17, 2001'. Returns '' on any parse failure."""
    if not birth_date:
        return ""
    try:
        parsed = dt.datetime.strptime(birth_date, "%Y-%m-%d")
    except ValueError:
        return ""
    return parsed.strftime("%B %-d, %Y") if hasattr(parsed, "strftime") else ""


def _ordered_watchlist(players: Dict[str, Any]) -> List[Tuple[str, Dict[str, Any]]]:
    """Stable rotation order: earliest-added first, name as a tiebreaker."""
    return sorted(
        players.items(),
        key=lambda item: (item[1].get("addedDate") or "", item[1].get("name") or ""),
    )


def _has_bio_content(bio: Dict[str, str]) -> bool:
    return bool(bio.get("high_school") or bio.get("draft_info") or bio.get("signed_date"))


def pick_next_player(
    ordered: List[Tuple[str, Dict[str, Any]]],
    bios: Dict[str, Dict[str, str]],
    start_index: int,
):
    """
    Walks forward from start_index (wrapping) for one full lap, returning
    (list_index, person_id, snapshot, bio) for the first player with actual
    bio content, or None if nobody qualifies.
    """
    n = len(ordered)
    if n == 0:
        return None
    for step in range(n):
        idx = (start_index + step) % n
        person_id, snap = ordered[idx]
        bio = bios.get(person_id)
        if bio and _has_bio_content(bio):
            return idx, person_id, snap, bio
    return None


def run() -> int:
    state = ss_mod.load_spotlight_state()

    today = dt.date.today()
    last_posted = state.get("last_posted_date")
    if last_posted:
        try:
            last_posted_date = dt.date.fromisoformat(last_posted)
            days_since = (today - last_posted_date).days
        except ValueError:
            days_since = config.SPOTLIGHT_MIN_DAYS_BETWEEN  # bad data -- don't block forever
        if days_since < config.SPOTLIGHT_MIN_DAYS_BETWEEN:
            logger.info(
                "Only %d day(s) since last spotlight post (need %d); skipping this run.",
                days_since, config.SPOTLIGHT_MIN_DAYS_BETWEEN,
            )
            ss_mod.save_spotlight_state(state)
            return 0

    tracker_state = player_tracker_state.load_tracker_state()
    players = tracker_state.get("players", {})
    if not players:
        logger.warning("Watchlist is empty (tracked_players.json has no players yet); skipping.")
        ss_mod.save_spotlight_state(state)
        return 0

    bios = bio_sheet.get_player_bios()
    ordered = _ordered_watchlist(players)

    pick = pick_next_player(ordered, bios, state.get("rotation_index", 0))
    if pick is None:
        logger.warning(
            "No players in the watchlist have bio data in the '%s' sheet tab yet; "
            "skipping. Fill in a row (keyed by Person ID) to start getting posts.",
            config.PLAYER_BIOS_SHEET_NAME,
        )
        ss_mod.save_spotlight_state(state)
        return 0

    idx, person_id, snap, bio = pick

    # Live lookup for the freshest birthday + current team/level/position --
    # the watchlist snapshot can be a few days stale between tracker runs.
    try:
        person = mlb_api.get_person(int(person_id))
    except Exception as exc:
        logger.warning("Could not fetch live person data for %s (%s); using cached snapshot.", person_id, exc)
        person = {}

    name = person.get("fullName") or snap.get("name", "Unknown")
    position = (person.get("primaryPosition") or {}).get("abbreviation") or snap.get("primaryPosition")
    current_team = (person.get("currentTeam") or {}).get("name") or snap.get("currentTeamName") or "the organization"
    sport_id = snap.get("sportId")
    level_name = config.SPORT_ID_LEVEL_NAME.get(sport_id, "") if sport_id else ""
    birthday_str = _format_birthday(person.get("birthDate", ""))

    tweet_text = tweet_formatter.format_player_spotlight_tweet(
        player_name=name,
        team_name=current_team,
        level_name=level_name,
        position=position,
        birthday_str=birthday_str,
        high_school=bio.get("high_school") or None,
        draft_info=bio.get("draft_info") or None,
        signed_date=bio.get("signed_date") or None,
        fun_fact=bio.get("fun_fact") or None,
    )

    try:
        twitter_client.post_tweet(tweet_text)
    except twitter_client.TweetPostError as exc:
        logger.error("Failed to post spotlight for %s: %s", name, exc)
        return 1  # don't advance state -- retry the same player next run

    sheet_logger.log_tweet(
        script="player_spotlight",
        category="SPOTLIGHT",
        subject=name,
        tweet_text=tweet_text,
    )

    state["rotation_index"] = (idx + 1) % len(ordered)
    state["last_posted_person_id"] = person_id
    state["last_posted_date"] = today.isoformat()
    ss_mod.save_spotlight_state(state)

    logger.info("Posted spotlight for %s. Next rotation index: %d.", name, state["rotation_index"])
    return 0


if __name__ == "__main__":
    sys.exit(run())
