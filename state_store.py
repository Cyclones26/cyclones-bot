"""
state_store.py
---------------
Tiny JSON-file "database" so the bot never tweets the same thing twice.

Because the scripts are designed to run on ephemeral runners (GitHub
Actions spins up a brand-new VM every time), state.json must be checked
into the repo and re-committed after each run -- see the workflow YAML
files in .github/workflows/ for the "commit state back" step. If you run
this on PythonAnywhere instead, the file just lives on disk permanently
and this module needs no changes.
"""

import datetime as dt
import json
import os
from typing import Any, Dict

import config

DEFAULT_STATE: Dict[str, Any] = {
    "seen_transaction_ids": [],   # transaction["id"] values already tweeted
    "tweeted_game_pks": [],       # gamePk values already covered by a daily recap
    "last_weekly_summary_end_date": None,  # ISO date string, e.g. "2026-06-21"
    "last_run_at": None,          # ISO timestamp of the most recent successful run
}

# Cap list growth so the file doesn't grow forever over a season.
MAX_HISTORY_ITEMS = 1000


def load_state() -> Dict[str, Any]:
    if not os.path.exists(config.STATE_FILE):
        return dict(DEFAULT_STATE)
    try:
        with open(config.STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return dict(DEFAULT_STATE)
    merged = dict(DEFAULT_STATE)
    merged.update(data)
    return merged


def save_state(state: Dict[str, Any]) -> None:
    # Trim history lists so state.json doesn't grow unbounded.
    for key in ("seen_transaction_ids", "tweeted_game_pks"):
        if key in state and isinstance(state[key], list) and len(state[key]) > MAX_HISTORY_ITEMS:
            state[key] = state[key][-MAX_HISTORY_ITEMS:]

    # Always stamp the run time, even when nothing else changed. This
    # guarantees state.json has a diff every single run, so the GitHub
    # Actions "commit state back" step always has something to push.
    # That matters because GitHub auto-disables a repo's *scheduled*
    # workflows after 60 days with zero pushes -- without this stamp, a
    # quiet offseason stretch (no transactions, no games) could silently
    # kill your cron until you noticed and manually re-enabled it.
    state["last_run_at"] = dt.datetime.utcnow().isoformat(timespec="seconds") + "Z"

    with open(config.STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, sort_keys=True)
