"""
player_tracker_state.py
-------------------------
Persistent JSON store for the long-term Player Tracker (Update #4),
separate from state_store.py's state.json because this tracks individual
players forever rather than dedupe-ing one-off transactions/games.

Shape of tracked_players.json:
{
  "players": {
    "<personId>": {
      "name": "Mitch Voit",
      "currentTeamId": 1324,
      "currentTeamName": "Binghamton Rumble Ponies",
      "sportId": 12,
      "levelRank": 3,
      "parentOrgName": "New York Mets",
      "mlbDebutSeen": false,
      "lastStatus": "ACTIVE",          # best-effort IL tracking
      "addedDate": "2026-06-26",
      "lastCheckedAt": "2026-06-26T12:00:00Z"
    },
    ...
  },
  "last_run_at": "2026-06-26T12:00:03Z"
}

Like state_store.py, this is designed to be committed back to the repo
after every GitHub Actions run (see .github/workflows/player_tracker.yml)
and always stamps last_run_at so the workflow never goes 60 days without a
push (see state_store.py's save_state() for the full explanation of why
that matters).
"""

from __future__ import annotations

import datetime as dt
import json
import os
from typing import Any, Dict

import config

DEFAULT_TRACKER_STATE: Dict[str, Any] = {
    "players": {},
    "last_run_at": None,
}


def load_tracker_state() -> Dict[str, Any]:
    if not os.path.exists(config.TRACKER_STATE_FILE):
        return {"players": {}, "last_run_at": None}
    try:
        with open(config.TRACKER_STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return {"players": {}, "last_run_at": None}
    merged = dict(DEFAULT_TRACKER_STATE)
    merged.update(data)
    merged.setdefault("players", {})
    return merged


def save_tracker_state(state: Dict[str, Any]) -> None:
    # Same reasoning as state_store.save_state(): always stamp the run time
    # so there's always a diff for GitHub Actions to commit, even in a
    # stretch where every tracked player's status happens to be unchanged.
    state["last_run_at"] = dt.datetime.utcnow().isoformat(timespec="seconds") + "Z"
    with open(config.TRACKER_STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, sort_keys=True)
