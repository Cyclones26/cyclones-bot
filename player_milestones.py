"""
player_milestones.py
----------------------
Classification logic for the long-term Player Tracker (Update #4): diffs
one snapshot of a tracked player's current team/level/org/status against
the snapshot stored from the previous run, and decides whether anything
tweet-worthy happened. This plays the same role transactions.py plays for
Update #1, except it's org-agnostic -- it works the same whether the
player is still in the Mets system or has moved on entirely (see
mlb_api.get_person(), which is also org-agnostic).
"""

from __future__ import annotations

from typing import Any, Dict, Optional

MILESTONE_PROMOTED = "PLAYER_PROMOTED"
MILESTONE_DEMOTED = "PLAYER_DEMOTED"
MILESTONE_TRADED_ORG = "PLAYER_TRADED_ORG"
MILESTONE_LATERAL_MOVE = "PLAYER_LATERAL_MOVE"
MILESTONE_LEFT_AFFILIATED_BALL = "PLAYER_LEFT_AFFILIATED_BALL"
MILESTONE_MLB_DEBUT = "PLAYER_MLB_DEBUT"
MILESTONE_PLACED_ON_IL = "PLAYER_PLACED_ON_IL"
MILESTONE_ACTIVATED_FROM_IL = "PLAYER_ACTIVATED_FROM_IL"


def classify_player_change(old: Optional[Dict[str, Any]], new: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    `old` is the snapshot stored in tracked_players.json from the previous
    run (or None/un-initialized if this is the first time we've ever
    checked this player -- in that case we just seed silently and return
    None, since there's nothing to compare against yet).

    Returns {"category": <MILESTONE_*>, ...details} for the single most
    important change, or None if nothing tweet-worthy changed. Priority:
    MLB debut > left affiliated ball entirely > org trade > promotion/
    demotion/lateral move > best-effort IL status change.
    """
    if old is None or not old.get("_initialized"):
        return None

    # --- MLB debut takes priority over everything else ---
    if new.get("mlbDebutSeen") and not old.get("mlbDebutSeen"):
        return {
            "category": MILESTONE_MLB_DEBUT,
            "team_name": new.get("currentTeamName"),
        }

    old_team_id = old.get("currentTeamId")
    new_team_id = new.get("currentTeamId")

    # --- Left affiliated ball entirely: released, retired, indy/overseas ---
    # (best-effort: the MLB Stats API simply stops returning a currentTeam;
    # we can't distinguish "released" from "retired" from "signed overseas"
    # without more context, so the tweet stays deliberately neutral)
    if old_team_id and not new_team_id:
        return {
            "category": MILESTONE_LEFT_AFFILIATED_BALL,
            "old_team_name": old.get("currentTeamName"),
        }

    if new_team_id and old_team_id != new_team_id:
        old_org = old.get("parentOrgName")
        new_org = new.get("parentOrgName")
        old_rank = old.get("levelRank")
        new_rank = new.get("levelRank")

        if old_org and new_org and old_org != new_org:
            category = MILESTONE_TRADED_ORG
        elif old_rank is not None and new_rank is not None and new_rank > old_rank:
            category = MILESTONE_PROMOTED
        elif old_rank is not None and new_rank is not None and new_rank < old_rank:
            category = MILESTONE_DEMOTED
        else:
            category = MILESTONE_LATERAL_MOVE

        return {
            "category": category,
            "old_team_name": old.get("currentTeamName"),
            "new_team_name": new.get("currentTeamName"),
            "old_org": old_org,
            "new_org": new_org,
        }

    # --- Same team: best-effort IL status change ---
    old_status = old.get("lastStatus") or "ACTIVE"
    new_status = new.get("lastStatus") or "ACTIVE"
    if old_status != new_status:
        if new_status == "IL":
            return {"category": MILESTONE_PLACED_ON_IL, "team_name": new.get("currentTeamName")}
        if old_status == "IL" and new_status == "ACTIVE":
            return {"category": MILESTONE_ACTIVATED_FROM_IL, "team_name": new.get("currentTeamName")}

    return None
