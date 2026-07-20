"""
transactions.py
----------------
Classification logic for MLB Stats API transaction records, separate from
the raw fetch (mlb_api.py) and the tweet text (tweet_formatter.py).

The /transactions endpoint gives us free-text fields (typeDesc,
description) rather than a clean enum, so classification is necessarily
keyword-based. We additionally cross-reference fromTeam/toTeam against
the live affiliate ladder (see mlb_api.get_affiliate_ladder) to tell a
true level-change "promotion" from a same-level trade or a depth-chart
shuffle.

This module also extracts *injury details* from IL transaction
descriptions (extract_il_details). MLB's descriptions follow a loose
pattern like:

  "Brooklyn Cyclones placed RHP Wyatt Hudepohl on the 7-day injured
   list retroactive to July 10, 2026. Right shoulder strain."

or sometimes "... on the 7-day injured list with a right elbow sprain."
The specific injury is included only when MLB publishes it (for minor
leaguers it often isn't), so everything here is best-effort: whatever
detail exists gets surfaced in the tweet, and the tweet degrades to a
plain IL notice when it doesn't.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Optional

CATEGORY_PROMOTED_FROM_CYCLONES = "PROMOTED_FROM_CYCLONES"   # left Brooklyn, went UP
CATEGORY_DEMOTED_FROM_CYCLONES = "DEMOTED_FROM_CYCLONES"     # left Brooklyn, went DOWN
CATEGORY_PROMOTED_TO_CYCLONES = "PROMOTED_TO_CYCLONES"       # arrived in Brooklyn, came from a LOWER level
CATEGORY_DEMOTED_TO_CYCLONES = "DEMOTED_TO_CYCLONES"         # arrived in Brooklyn, came from a HIGHER level (rehab/demotion)
CATEGORY_PLACED_ON_IL = "PLACED_ON_IL"
CATEGORY_ACTIVATED_FROM_IL = "ACTIVATED_FROM_IL"
CATEGORY_SIGNED = "SIGNED"
CATEGORY_RELEASED = "RELEASED"
CATEGORY_OTHER = "OTHER"

# ---------------------------------------------------------------------------
# IL detail extraction
# ---------------------------------------------------------------------------

_IL_DAYS_RE = re.compile(r"(\d+)[- ]day injured list", re.IGNORECASE)
_RETRO_RE = re.compile(r"retroactive to ([A-Za-z]+ \d{1,2}(?:, \d{4})?)", re.IGNORECASE)
_INJURY_WITH_RE = re.compile(
    r"injured list[^.]*?\bwith\b\s+(?:a |an )?([^.;]+)", re.IGNORECASE
)

# Words that make a trailing sentence look like an injury description
# rather than procedural boilerplate.
_INJURY_KEYWORDS = (
    "strain", "sprain", "surgery", "inflammation", "soreness", "fracture",
    "tightness", "contusion", "tommy john", "impingement", "tendinitis",
    "tendonitis", "discomfort", "injury", "concussion", "laceration",
)


def extract_il_details(description: Optional[str]) -> Dict[str, str]:
    """
    Best-effort parse of an IL transaction description. Returns any of:
      {"il_days": "7", "retro_date": "July 10, 2026", "injury": "right shoulder strain"}
    Keys are simply absent when the description doesn't carry that detail.
    """
    out: Dict[str, str] = {}
    if not description:
        return out

    m = _IL_DAYS_RE.search(description)
    if m:
        out["il_days"] = m.group(1)

    m = _RETRO_RE.search(description)
    if m:
        out["retro_date"] = m.group(1)

    m = _INJURY_WITH_RE.search(description)
    if m:
        out["injury"] = m.group(1).strip().rstrip(",")
    else:
        # Injury sometimes arrives as its own trailing sentence:
        # "... injured list retroactive to July 10, 2026. Right shoulder strain."
        sentences = [s.strip() for s in description.split(".") if s.strip()]
        for sentence in sentences[1:]:
            lower = sentence.lower()
            if len(sentence) <= 60 and any(k in lower for k in _INJURY_KEYWORDS):
                out["injury"] = sentence
                break

    return out


def _team_rank_lookup(ladder: Dict[int, Dict[str, Any]]) -> Dict[int, int]:
    """ladder is {sportId: {"teamId":.., "rank":..}} -> flip to {teamId: rank}."""
    return {info["teamId"]: info["rank"] for info in ladder.values() if info.get("teamId") is not None}


def classify_transaction(
    tx: Dict[str, Any], team_id: int, ladder: Optional[Dict[int, Dict[str, Any]]] = None
) -> Dict[str, Any]:
    """
    Returns a dict with at least:
      {"category": <one of the CATEGORY_* constants>,
       "player": str, "from_team": str|None, "to_team": str|None,
       "description": str}
    IL placements additionally carry "il_details" (see extract_il_details).
    """
    person = tx.get("person") or {}
    from_team = tx.get("fromTeam") or {}
    to_team = tx.get("toTeam") or {}
    description = tx.get("description") or tx.get("typeDesc") or ""
    type_desc = (tx.get("typeDesc") or "").lower()
    text = f"{type_desc} {description}".lower()

    result = {
        "player": person.get("fullName", "Unknown Player"),
        "from_team": from_team.get("name"),
        "to_team": to_team.get("name"),
        "description": description,
        "date": tx.get("date") or tx.get("effectiveDate") or tx.get("resolutionDate"),
    }

    # --- Injured list moves take priority over the generic from/to logic ---
    if "injured list" in text:
        if "activated" in text or "reinstated" in text:
            result["category"] = CATEGORY_ACTIVATED_FROM_IL
            return result
        if "placed" in text or "transferred" in text:
            result["category"] = CATEGORY_PLACED_ON_IL
            result["il_details"] = extract_il_details(description)
            return result

    if "released" in text or "outright" in text:
        result["category"] = CATEGORY_RELEASED
        return result

    if "signed" in text and not from_team and not to_team:
        result["category"] = CATEGORY_SIGNED
        return result

    # --- Level-change logic via the live affiliate ladder ---
    from_id = from_team.get("id")
    to_id = to_team.get("id")
    rank_by_team = _team_rank_lookup(ladder) if ladder else {}

    if from_id == team_id and to_id and to_id != team_id:
        from_rank = rank_by_team.get(from_id, 0)
        to_rank = rank_by_team.get(to_id)
        if to_rank is not None and to_rank > from_rank:
            result["category"] = CATEGORY_PROMOTED_FROM_CYCLONES
        elif to_rank is not None and to_rank < from_rank:
            result["category"] = CATEGORY_DEMOTED_FROM_CYCLONES
        else:
            result["category"] = CATEGORY_OTHER
        return result

    if to_id == team_id and from_id and from_id != team_id:
        to_rank = rank_by_team.get(to_id, 0)
        from_rank = rank_by_team.get(from_id)
        if from_rank is not None and from_rank > to_rank:
            result["category"] = CATEGORY_DEMOTED_TO_CYCLONES
        elif from_rank is not None and from_rank < to_rank:
            result["category"] = CATEGORY_PROMOTED_TO_CYCLONES
        else:
            result["category"] = CATEGORY_OTHER
        return result

    result["category"] = CATEGORY_OTHER
    return result
