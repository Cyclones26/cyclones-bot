"""
bio_sheet.py
-------------
Reads the hand-maintained "Player Bios" worksheet (see
config.PLAYER_BIOS_SHEET_NAME) that supplies the data MLB's Stats API
doesn't have: high school, draft/signing info, signed date, and an
optional fun fact. Birthday is NOT stored here -- it's pulled live from
mlb_api.get_person() in player_spotlight.py so it's never out of date.

This module only ever READS the tab. It never clears or rewrites rows
(unlike sheet_logger.sync_tracked_players, which rebuilds its tab from
scratch every run) -- Ira edits this tab by hand, so the bot must never
touch it except to create it with a header row the first time it's
missing.

Row shape (header row is created automatically if the tab doesn't exist):
    Person ID | Name | High School | Draft / Signed Info | Signed Date | Fun Fact

Person ID matches the same MLB personId used as the key in
tracked_players.json and shown in the auto-synced "Tracked Players" tab --
that's the easiest way to find the right row to fill in.

Returns {} (and logs a warning) on any failure -- a missing/broken bio
sheet should degrade the spotlight tweet to bare-bones info, never crash
the run.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict

import config

logger = logging.getLogger("bio_sheet")

BIO_HEADER = ["Person ID", "Name", "High School", "Draft / Signed Info", "Signed Date", "Fun Fact"]


def _client_and_sheet_id():
    creds_json = os.environ.get("GOOGLE_CREDENTIALS")
    sheet_id = os.environ.get("GOOGLE_SHEET_ID")
    if not creds_json or not sheet_id:
        logger.warning("GOOGLE_CREDENTIALS or GOOGLE_SHEET_ID not set; no bio data available.")
        return None, None

    import gspread
    from google.oauth2.service_account import Credentials

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_info(json.loads(creds_json), scopes=scopes)
    client = gspread.authorize(creds)
    return client, sheet_id


def get_player_bios() -> Dict[str, Dict[str, str]]:
    """
    Returns {personId_str: {"high_school": ..., "draft_info": ...,
    "signed_date": ..., "fun_fact": ...}} for every row with a filled-in
    Person ID. Rows with a blank Person ID are skipped (so Ira can leave
    placeholder rows mid-edit without breaking a run).
    """
    try:
        import gspread

        client, sheet_id = _client_and_sheet_id()
        if client is None:
            return {}

        ss = client.open_by_key(sheet_id)
        try:
            ws = ss.worksheet(config.PLAYER_BIOS_SHEET_NAME)
        except gspread.exceptions.WorksheetNotFound:
            ws = ss.add_worksheet(title=config.PLAYER_BIOS_SHEET_NAME, rows=200, cols=len(BIO_HEADER))
            ws.append_row(BIO_HEADER, value_input_option="RAW")
            logger.info(
                "Created empty '%s' tab -- fill in rows to start getting spotlight posts.",
                config.PLAYER_BIOS_SHEET_NAME,
            )
            return {}

        records = ws.get_all_records()  # list of dicts keyed by header row
        bios: Dict[str, Dict[str, str]] = {}
        for row in records:
            person_id = str(row.get("Person ID", "")).strip()
            if not person_id:
                continue
            bios[person_id] = {
                "high_school": str(row.get("High School", "")).strip(),
                "draft_info": str(row.get("Draft / Signed Info", "")).strip(),
                "signed_date": str(row.get("Signed Date", "")).strip(),
                "fun_fact": str(row.get("Fun Fact", "")).strip(),
            }
        return bios

    except Exception as exc:
        logger.warning("bio_sheet.get_player_bios failed: %s", exc)
        return {}
