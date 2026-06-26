#!/usr/bin/env python3
"""
weekly_summary.py
-------------------
Update #3: Weekly Performance Summaries ("hot streaks over the last 7 days").

Run this once a week (see .github/workflows/weekly_summary.yml). Each run:
  1. Pulls the active roster for TEAM_ID.
  2. For each position player, pulls hitting stats aggregated over the
     last 7 days (stats=byDateRange) and keeps anyone clearing
     config.WEEKLY_MIN_AT_BATS.
  3. For each pitcher, does the same for pitching stats, gated by
     config.WEEKLY_MIN_OUTS_PITCHED.
  4. Ranks hitters by batting average (ties broken by OPS/HR) and
     pitchers by ERA, and tweets the top performers.

Note: MLB's "byDateRange" stats endpoint is unofficial/undocumented, like
the rest of this API. If it ever stops returning data, switch to
mlb_api.get_player_gamelog() and sum the games whose date falls in
[start_date, end_date] yourself -- the function is already in mlb_api.py,
just not wired up by default here to keep this script simple.

Usage:
    python weekly_summary.py
    DRY_RUN=true python weekly_summary.py
"""

from __future__ import annotations

import datetime as dt
import logging
import sys
import time
from typing import Any, Dict, List

import config
import mlb_api
import state_store
import tweet_formatter
import twitter_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("weekly_summary")

POLITE_DELAY_SECONDS = 0.2  # be a good citizen of a free, unofficial API


def _safe_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _is_pitcher(roster_entry: Dict[str, Any]) -> bool:
    return (roster_entry.get("position") or {}).get("abbreviation") == "P"


def collect_hot_hitters(roster: List[Dict[str, Any]], sport_id: int, start_date: str, end_date: str) -> List[Dict[str, Any]]:
    hitters = []
    for entry in roster:
        if _is_pitcher(entry):
            continue
        person = entry.get("person") or {}
        person_id = person.get("id")
        if not person_id:
            continue

        time.sleep(POLITE_DELAY_SECONDS)
        try:
            stat = mlb_api.get_player_stats_by_date_range(
                person_id, "hitting", start_date, end_date, sport_id, config.SEASON
            )
        except Exception as exc:
            logger.warning("Skipping %s (hitting fetch failed: %s)", person.get("fullName"), exc)
            continue

        at_bats = int(stat.get("atBats", 0) or 0)
        if at_bats < config.WEEKLY_MIN_AT_BATS:
            continue

        hitters.append(
            {
                "name": person.get("fullName", "Unknown"),
                "atBats": at_bats,
                "hits": int(stat.get("hits", 0) or 0),
                "homeRuns": int(stat.get("homeRuns", 0) or 0),
                "rbi": int(stat.get("rbi", 0) or 0),
                "avg": _safe_float(stat.get("avg", 0)),
                "ops": _safe_float(stat.get("ops", 0)),
            }
        )

    hitters.sort(key=lambda h: (h["avg"], h["ops"], h["homeRuns"]), reverse=True)
    return hitters


def collect_hot_pitchers(roster: List[Dict[str, Any]], sport_id: int, start_date: str, end_date: str) -> List[Dict[str, Any]]:
    pitchers = []
    for entry in roster:
        if not _is_pitcher(entry):
            continue
        person = entry.get("person") or {}
        person_id = person.get("id")
        if not person_id:
            continue

        time.sleep(POLITE_DELAY_SECONDS)
        try:
            stat = mlb_api.get_player_stats_by_date_range(
                person_id, "pitching", start_date, end_date, sport_id, config.SEASON
            )
        except Exception as exc:
            logger.warning("Skipping %s (pitching fetch failed: %s)", person.get("fullName"), exc)
            continue

        ip_str = stat.get("inningsPitched", "0.0") or "0.0"
        whole, _, frac = str(ip_str).partition(".")
        outs = int(whole or 0) * 3 + int(frac or 0)
        if outs < config.WEEKLY_MIN_OUTS_PITCHED:
            continue

        pitchers.append(
            {
                "name": person.get("fullName", "Unknown"),
                "inningsPitched": ip_str,
                "strikeOuts": int(stat.get("strikeOuts", 0) or 0),
                "era": _safe_float(stat.get("era", 99.99)),
            }
        )

    pitchers.sort(key=lambda p: p["era"])
    return pitchers


def run() -> int:
    state = state_store.load_state()

    end_date = dt.date.today() - dt.timedelta(days=1)  # through yesterday
    start_date = end_date - dt.timedelta(days=6)        # 7-day window inclusive
    start_str, end_str = start_date.isoformat(), end_date.isoformat()

    if state.get("last_weekly_summary_end_date") == end_str:
        logger.info("Already posted a weekly summary through %s, skipping.", end_str)
        return 0

    team = mlb_api.get_team(config.TEAM_ID)
    sport_id = team.get("sport", {}).get("id") or 13

    logger.info("Fetching active roster for team %s...", config.TEAM_ID)
    roster = mlb_api.get_roster(config.TEAM_ID, roster_type="active")

    logger.info("Computing hot hitters/pitchers for %s -> %s...", start_str, end_str)
    hot_hitters = collect_hot_hitters(roster, sport_id, start_str, end_str)
    hot_pitchers = collect_hot_pitchers(roster, sport_id, start_str, end_str)

    if not hot_hitters and not hot_pitchers:
        logger.info("No qualifying hitters/pitchers this week (min AB/IP not met) -- skipping tweet.")
        return 0

    tweet_text = tweet_formatter.format_weekly_summary_tweet(start_str, end_str, hot_hitters, hot_pitchers)

    try:
        twitter_client.post_tweet(tweet_text)
    except twitter_client.TweetPostError as exc:
        logger.error("Failed to post weekly summary: %s", exc)
        return 1

    state["last_weekly_summary_end_date"] = end_str
    state_store.save_state(state)

    logger.info("Done. Posted weekly summary for %s -> %s.", start_str, end_str)
    return 0


if __name__ == "__main__":
    sys.exit(run())
