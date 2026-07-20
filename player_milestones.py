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

Also home to the pure "big game" feat detectors (multi-HR games, 4-hit
games, 10-K starts) that player_tracker.py runs over each tracked
player's game log.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

MILESTONE_PROMOTED = "PLAYER_PROMOTED"
MILESTONE_DEMOTED = "PLAYER_DEMOTED"
MILESTONE_TRADED_ORG = "PLAYER_TRADED_ORG"
MILESTONE_LATERAL_MOVE = "PLAYER_LATERAL_MOVE"
MILESTONE_LEFT_AFFILIATED_BALL = "PLAYER_LEFT_AFFILIATED_BALL"
MILESTONE_MLB_DEBUT = "PLAYER_MLB_DEBUT"
MILESTONE_PLACED_ON_IL = "PLAYER_PLACED_ON_IL"
MILESTONE_ACTIVATED_FROM_IL = "PLAYER_ACTIVATED_FROM_IL"
MILESTONE_REHAB_ASSIGNMENT = "PLAYER_REHAB_ASSIGNMENT"
MILESTONE_REHAB_RETURN = "PLAYER_REHAB_RETURN"

# sportId 16 = Rookie/Complex leagues (FCL/ACL), where MiLB rehab
# assignments almost always start.
_COMPLEX_LEAGUE_SPORT_ID = 16


def classify_player_change(old: Optional[Dict[str, Any]], new: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    `old` is the snapshot stored in tracked_players.json from the previous
    run (or None/un-initialized if this is the first time we've ever
    checked this player -- in that case we just seed silently and return
    None, since there's nothing to compare against yet).

    Returns {"category": <MILESTONE_*>, ...details} for the single most
    important change, or None if nothing tweet-worthy changed. Priority:
    MLB debut > left affiliated ball entirely > rehab assignment/return >
    org trade > promotion/demotion/lateral move > best-effort IL status
    change.
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
        same_org = bool(old_org and new_org and old_org == new_org)

        # --- Rehab assignment: was on the IL, now suddenly "assigned" to
        # the org's complex-league club. Without this check the generic
        # logic below would mislabel it a demotion. ---
        if (
            same_org
            and (old.get("lastStatus") == "IL" or old.get("onRehab"))
            and new.get("sportId") == _COMPLEX_LEAGUE_SPORT_ID
            and (old_rank or 0) > 0
        ):
            return {
                "category": MILESTONE_REHAB_ASSIGNMENT,
                "old_team_name": old.get("currentTeamName"),
                "new_team_name": new.get("currentTeamName"),
            }

        # --- Rehab return: previously flagged onRehab, now back at a
        # higher level. Without this it would read as a "promotion". ---
        if (
            same_org
            and old.get("onRehab")
            and old_rank is not None
            and new_rank is not None
            and new_rank > old_rank
        ):
            return {
                "category": MILESTONE_REHAB_RETURN,
                "old_team_name": old.get("currentTeamName"),
                "new_team_name": new.get("currentTeamName"),
            }

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


# --------------------------------------------------------------------------
# "Big game" feat detection (pure functions over a game-log response)
# --------------------------------------------------------------------------

def detect_game_feats(
    game_splits: List[Dict[str, Any]], is_pitcher: bool, since_date: str
) -> List[Dict[str, str]]:
    """
    Scans a game log (mlb_api.get_player_gamelog splits) for standout
    single-game performances strictly AFTER `since_date` (ISO date), so a
    game is never tweeted twice. Returns [{"date": ..., "desc": ...}]
    sorted oldest-first.

    Thresholds (deliberately high -- these should feel like events):
      hitters:  2+ HR in a game, or 4+ hits in a game
      pitchers: 10+ strikeouts in a start
    """
    feats: List[Dict[str, str]] = []
    for split in game_splits:
        date = split.get("date") or ""
        if not date or date <= since_date:
            continue
        stat = split.get("stat") or {}
        if is_pitcher:
            k = int(stat.get("strikeOuts", 0) or 0)
            if k >= 10:
                ip = stat.get("inningsPitched", "?")
                feats.append({"date": date, "desc": f"{k} strikeouts over {ip} IP"})
        else:
            hr = int(stat.get("homeRuns", 0) or 0)
            hits = int(stat.get("hits", 0) or 0)
            bits = []
            if hr >= 2:
                bits.append(f"{hr} home runs")
            if hits >= 4:
                bits.append(f"a {hits}-hit game")
            if bits:
                desc = " and ".join(bits)
                rbi = int(stat.get("rbi", 0) or 0)
                if rbi >= 4:
                    desc += f" with {rbi} RBI"
                feats.append({"date": date, "desc": desc})
    feats.sort(key=lambda f: f["date"])
    return feats


def season_hr_total(game_splits: List[Dict[str, Any]]) -> int:
    return sum(int((s.get("stat") or {}).get("homeRuns", 0) or 0) for s in game_splits)


def first_hr_date(game_splits: List[Dict[str, Any]]) -> Optional[str]:
    dated = sorted((s for s in game_splits if s.get("date")), key=lambda s: s["date"])
    for split in dated:
        if int((split.get("stat") or {}).get("homeRuns", 0) or 0) >= 1:
            return split["date"]
    return None
