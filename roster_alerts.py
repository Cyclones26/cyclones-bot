#!/usr/bin/env python3
"""
roster_alerts.py
------------------
Update #1: Roster Transaction Alerts.

Run this every few hours (see .github/workflows/roster_alerts.yml). Each
run:
  1. Pulls the live Mets affiliate ladder so we can tell levels apart.
  2. Pulls /transactions for TEAM_ID over the last
     config.TRANSACTION_LOOKBACK_DAYS days (wider than 1 day so a missed
     run still catches up).
  3. Skips any transaction["id"] already in state["seen_transaction_ids"].
  4. Classifies + tweets each new one, then records its id so it's never
     posted twice.

Usage:
    python roster_alerts.py              # live (unless DRY_RUN=true)
    DRY_RUN=true python roster_alerts.py # prints tweets instead of posting
"""

from __future__ import annotations

import datetime as dt
import logging
import sys

import config
import mlb_api
import state_store
import transactions as tx_mod
import tweet_formatter
import twitter_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("roster_alerts")


def run() -> int:
    state = state_store.load_state()
    seen_ids = set(state.get("seen_transaction_ids", []))

    today = dt.date.today()
    start_date = (today - dt.timedelta(days=config.TRANSACTION_LOOKBACK_DAYS)).isoformat()
    end_date = today.isoformat()

    logger.info("Fetching affiliate ladder for parent club %s...", config.PARENT_MLB_TEAM_ID)
    try:
        ladder = mlb_api.get_affiliate_ladder(config.PARENT_MLB_TEAM_ID)
    except Exception as exc:
        logger.warning("Could not fetch affiliate ladder (%s); falling back to sportId-only ranking.", exc)
        ladder = {}

    logger.info("Fetching transactions for team %s from %s to %s...", config.TEAM_ID, start_date, end_date)
    txs = mlb_api.get_transactions(config.TEAM_ID, start_date, end_date)
    # Filter to only transactions directly involving the Cyclones (teamId must be
    # fromTeam or toTeam — the API sometimes returns tangential records where 509
    # appears in metadata but the move itself has nothing to do with Brooklyn).
    txs = [
        t for t in txs
        if (t.get("fromTeam") or {}).get("id") == config.TEAM_ID
        or (t.get("toTeam") or {}).get("id") == config.TEAM_ID
    ]
    logger.info("Found %d transaction(s) in window.", len(txs))

    new_count = 0
    for tx in txs:
        tx_id = tx.get("id")
        if tx_id is None or tx_id in seen_ids:
            continue

        classified = tx_mod.classify_transaction(tx, config.TEAM_ID, ladder)
        tweet_text = tweet_formatter.format_transaction_tweet(classified)

        logger.info("New transaction #%s [%s]: %s", tx_id, classified["category"], classified["player"])
        try:
            twitter_client.post_tweet(tweet_text)
        except twitter_client.TweetPostError as exc:
            logger.error("Failed to post transaction #%s: %s", tx_id, exc)
            continue  # leave it unseen so the next run retries

        seen_ids.add(tx_id)
        new_count += 1

    state["seen_transaction_ids"] = list(seen_ids)
    state_store.save_state(state)

    logger.info("Done. Posted %d new transaction alert(s).", new_count)
    return 0


if __name__ == "__main__":
    sys.exit(run())
