#!/usr/bin/env python3
"""
daily_recap.py
----------------
Update #2: Daily Post-Game Stat Lines.

Run this once a morning (see .github/workflows/daily_recap.yml), after
last night's box score has finalized. Each run:
  1. Resolves the Cyclones' sportId (High-A) from /teams/{teamId}.
  2. Pulls yesterday's schedule entry/entries for the team.
  3. Skips any game whose gamePk is already in state["tweeted_game_pks"]
     and any game that isn't in a "Final" state yet.
  4. Pulls the box score (+ decisions, for W/SV credit), finds the top
     hitter and top pitcher, and tweets a recap.

Usage:
    python daily_recap.py
    DRY_RUN=true python daily_recap.py
    python daily_recap.py --date 2026-06-24   # backfill a specific date
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import sys

import boxscore
import config
import mlb_api
import state_store
import tweet_formatter
import twitter_client
import sheet_logger

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("daily_recap")


def _build_result_line(game: dict) -> str:
    teams = game.get("teams", {})
    home = teams.get("home", {})
    away = teams.get("away", {})
    home_name = (home.get("team") or {}).get("name", "Home")
    away_name = (away.get("team") or {}).get("name", "Away")
    home_score = home.get("score")
    away_score = away.get("score")

    cyclones_side = "home" if home.get("team", {}).get("id") == config.TEAM_ID else "away"
    cyclones_won = teams.get(cyclones_side, {}).get("isWinner", False)
    result_word = "W" if cyclones_won else "L"

    return f"FINAL ({result_word}): {away_name} {away_score} @ {home_name} {home_score}"


def _get_decision_ids(game: dict) -> tuple:
    decisions = game.get("decisions") or {}
    winner = decisions.get("winner") or {}
    save = decisions.get("save") or {}
    return winner.get("id"), save.get("id")


def process_date(date_str: str, state: dict) -> int:
    team = mlb_api.get_team(config.TEAM_ID)
    sport_id = team.get("sport", {}).get("id") or 13  # 13 = High-A fallback

    games = mlb_api.get_schedule_for_date(config.TEAM_ID, sport_id, date_str)
    if not games:
        logger.info("No games found for %s.", date_str)
        return 0

    tweeted_pks = set(state.get("tweeted_game_pks", []))
    posted = 0

    for game in games:
        game_pk = game.get("gamePk")
        status = (game.get("status") or {}).get("abstractGameState")
        if game_pk in tweeted_pks:
            logger.info("Game %s already tweeted, skipping.", game_pk)
            continue
        if status != "Final":
            logger.info("Game %s is not Final yet (status=%s), skipping.", game_pk, status)
            continue

        logger.info("Processing final game %s on %s...", game_pk, date_str)
        box = mlb_api.get_boxscore(game_pk)
        win_id, save_id = _get_decision_ids(game)

        hitter = boxscore.top_hitter(box, config.TEAM_ID)
        pitcher = boxscore.top_pitcher(box, config.TEAM_ID, win_pitcher_id=win_id, save_pitcher_id=save_id)
        result_line = _build_result_line(game)

        tweet_text = tweet_formatter.format_daily_recap_tweet(result_line, hitter, pitcher)

        try:
            twitter_client.post_tweet(tweet_text)
            sheet_logger.log_tweet(
                script="daily_recap",
                category="GAME RECAP",
                subject=date_str,
                tweet_text=tweet_text,
            )
        except twitter_client.TweetPostError as exc:
            logger.error("Failed to post recap for game %s: %s", game_pk, exc)
            continue

        tweeted_pks.add(game_pk)
        posted += 1

    state["tweeted_game_pks"] = list(tweeted_pks)
    return posted


def run(date_str: str = None) -> int:
    if date_str is None:
        yesterday = dt.date.today() - dt.timedelta(days=1)
        date_str = yesterday.isoformat()

    state = state_store.load_state()
    posted = process_date(date_str, state)
    state_store.save_state(state)

    logger.info("Done. Posted %d recap(s) for %s.", posted, date_str)
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Tweet a daily Cyclones recap.")
    parser.add_argument("--date", help="YYYY-MM-DD to process instead of yesterday (for backfills/testing).")
    args = parser.parse_args()
    sys.exit(run(args.date))
