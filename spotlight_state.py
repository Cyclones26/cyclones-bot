"""
spotlight_state.py
--------------------
Persistent JSON store for the Player Spotlight (biweekly bio post -- see
player_spotlight.py). Separate file from tracked_players.json / state.json
so a spotlight-state bug can never corrupt the transaction/tracker state.

Shape of spotlight_state.json:
{
  "rotation_index": 3,            # position in the sorted watchlist to try next
  "last_posted_person_id": "672550",
  "last_posted_date": "2026-09-02",
  "last_run_at": "2026-09-02T14:00:03Z"
}

Committed back to the repo after every run (see
.github/workflows/player_spotlight.yml), same reasoning as
player_tracker_state.py: always stamp last_run_at so there's always a diff
for the workflow to commit, even on a run that skips posting (too soon
since the last spotlight, or no bio data available yet).
"""

from __future__ import annotations

import datetime as dt
import json
import os
from typing import Any, Dict

import config

DEFAULT_SPOTLIGHT_STATE: Dict[str, Any] = {
    "rotation_index": 0,
    "last_posted_person_id": None,
    "last_posted_date": None,
    "last_run_at": None,
}


def load_spotlight_state() -> Dict[str, Any]:
    if not os.path.exists(config.SPOTLIGHT_STATE_FILE):
        return dict(DEFAULT_SPOTLIGHT_STATE)
    try:
        with open(config.SPOTLIGHT_STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return dict(DEFAULT_SPOTLIGHT_STATE)
    merged = dict(DEFAULT_SPOTLIGHT_STATE)
    merged.update(data)
    return merged


def save_spotlight_state(state: Dict[str, Any]) -> None:
    state["last_run_at"] = dt.datetime.utcnow().isoformat(timespec="seconds") + "Z"
    with open(config.SPOTLIGHT_STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, sort_keys=True)
