"""
sheet_logger.py
----------------
Logs every posted tweet to a Google Sheet for easy review.

Each row: Timestamp (UTC) | Script | Category | Subject | Tweet Text

Requires:
  - GOOGLE_CREDENTIALS env var: the full JSON of a service account key
    that has Editor access to the sheet.
  - GOOGLE_SHEET_ID env var: the ID portion of the sheet URL.

Failures are logged as warnings and never raise -- a sheet-write error
should never crash the bot or prevent state from being saved.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
from typing import Optional

logger = logging.getLogger("sheet_logger")

_client = None   # lazy-initialised gspread client
_sheet  = None   # lazy-initialised worksheet


def _get_sheet():
    """Lazy-init: returns the first worksheet, or None on any error."""
    global _client, _sheet
    if _sheet is not None:
        return _sheet

    creds_json = os.environ.get("GOOGLE_CREDENTIALS")
    sheet_id   = os.environ.get("GOOGLE_SHEET_ID")

    if not creds_json or not sheet_id:
        logger.debug("GOOGLE_CREDENTIALS or GOOGLE_SHEET_ID not set -- sheet logging disabled.")
        return None

    try:
        import gspread
        from google.oauth2.service_account import Credentials

        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ]
        info = json.loads(creds_json)
        creds = Credentials.from_service_account_info(info, scopes=scopes)
        _client = gspread.authorize(creds)
        spreadsheet = _client.open_by_key(sheet_id)
        _sheet = spreadsheet.sheet1

        # Write header row if the sheet is empty
        if not _sheet.row_values(1):
            _sheet.append_row(
                ["Timestamp (UTC)", "Script", "Category", "Subject", "Tweet Text"],
                value_input_option="RAW",
            )

    except Exception as exc:
        logger.warning("Could not initialise Google Sheet logger: %s", exc)
        return None

    return _sheet


def log_tweet(
    script: str,
    category: str,
    subject: str,
    tweet_text: str,
) -> None:
    """
    Append one row to the Google Sheet.

    Args:
        script:     e.g. "roster_alerts", "daily_recap", "weekly_summary", "player_tracker"
        category:   e.g. "PLACED_ON_IL", "PROMOTED_FROM_CYCLONES", "recap", "weekly", "milestone"
        subject:    player name, game date, or other short identifier
        tweet_text: the full text that was posted to X
    """
    sheet = _get_sheet()
    if sheet is None:
        return

    timestamp = dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    try:
        sheet.append_row(
            [timestamp, script, category, subject, tweet_text],
            value_input_option="RAW",
        )
        logger.debug("Logged tweet to sheet: [%s] %s / %s", timestamp, script, category)
    except Exception as exc:
        logger.warning("Failed to log tweet to sheet: %s", exc)


def sync_tracked_players(players: dict) -> None:
    """
    Refreshes the 'Tracked Players' worksheet so it always shows the
    current watchlist.  Clears the tab and rewrites it from scratch so
    stale rows are never left behind.

    Columns: Person ID | Name | Added Date | Current Team | Level | Org | Position | Last Status

    Failures are silently logged -- never raises.
    """
    try:
        import gspread
        creds_json = os.environ.get("GOOGLE_CREDENTIALS")
        sheet_id   = os.environ.get("GOOGLE_SHEET_ID")
        if not creds_json or not sheet_id:
            logger.warning("GOOGLE_CREDENTIALS or GOOGLE_SHEET_ID not set; skipping tracked-players sync.")
            return

        import json
        from google.oauth2.service_account import Credentials

        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ]
        creds  = Credentials.from_service_account_info(json.loads(creds_json), scopes=scopes)
        client = gspread.authorize(creds)
        ss     = client.open_by_key(sheet_id)

        # Get or create the "Tracked Players" worksheet
        try:
            ws = ss.worksheet("Tracked Players")
        except gspread.exceptions.WorksheetNotFound:
            ws = ss.add_worksheet(title="Tracked Players", rows=500, cols=8)

        # Build rows
        header = ["Person ID", "Name", "Added Date", "Current Team", "Level", "Org", "Position", "Last Status"]
        rows = [header]
        for person_id, snap in sorted(players.items(), key=lambda x: (x[1].get("name") or "")):
            rows.append([
                person_id,
                snap.get("name", ""),
                snap.get("addedDate", ""),
                snap.get("currentTeamName", ""),
                snap.get("levelRank", ""),
                snap.get("parentOrgName", ""),
                snap.get("primaryPosition", ""),
                snap.get("lastStatus", ""),
            ])

        # Clear and rewrite
        ws.clear()
        ws.update(rows, value_input_option="RAW")
        logger.info("Synced %d tracked player(s) to sheet.", len(players))

    except Exception as exc:
        logger.warning("sheet_logger.sync_tracked_players failed: %s", exc)
