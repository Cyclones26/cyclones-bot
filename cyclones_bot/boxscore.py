"""
boxscore.py
-----------
Parsing helpers for the /game/{gamePk}/boxscore response shape:

{
  "teams": {
    "away": {
      "team": {"id": 509, "name": "Brooklyn Cyclones"},
      "players": {
        "ID123456": {
          "person": {"id": 123456, "fullName": "Jane Doe"},
          "position": {"abbreviation": "LF"},
          "stats": {
            "batting": {"atBats":4,"hits":2,"doubles":1,"homeRuns":1,"rbi":3,...},
            "pitching": {"inningsPitched":"5.1","strikeOuts":7,"earnedRuns":1,...}
          },
          "seasonStats": {...}
        },
        ...
      }
    },
    "home": {...}
  }
}

We pick the "top" hitter/pitcher with simple, transparent scoring formulas
(not WPA/win-probability-grade sabermetrics) -- good enough for a fun,
fan-facing tweet. Swap in something fancier later if you want.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def find_side(boxscore: Dict[str, Any], team_id: int) -> Optional[str]:
    teams = boxscore.get("teams", {})
    for side in ("home", "away"):
        if (teams.get(side, {}).get("team") or {}).get("id") == team_id:
            return side
    return None


def _innings_pitched_to_outs(ip_str: str) -> float:
    """'5.1' -> 16 outs (5 full innings + 1 out). MLB encodes thirds as .1/.2."""
    if not ip_str:
        return 0.0
    try:
        whole, _, frac = ip_str.partition(".")
        outs = int(whole) * 3
        outs += int(frac) if frac else 0
        return float(outs)
    except (ValueError, TypeError):
        return 0.0


def get_players_for_side(boxscore: Dict[str, Any], side: str) -> List[Dict[str, Any]]:
    return list(boxscore.get("teams", {}).get(side, {}).get("players", {}).values())


def top_hitter(boxscore: Dict[str, Any], team_id: int) -> Optional[Dict[str, Any]]:
    side = find_side(boxscore, team_id)
    if side is None:
        return None

    best = None
    best_score = float("-inf")
    for player in get_players_for_side(boxscore, side):
        batting = (player.get("stats") or {}).get("batting") or {}
        at_bats = batting.get("atBats", 0)
        if not at_bats and not batting.get("hits"):
            continue  # didn't play / DNP

        hits = batting.get("hits", 0)
        doubles = batting.get("doubles", 0)
        triples = batting.get("triples", 0)
        hr = batting.get("homeRuns", 0)
        rbi = batting.get("rbi", 0)
        runs = batting.get("runs", 0)
        sb = batting.get("stolenBases", 0)

        score = hits + doubles + 2 * triples + 3 * hr + rbi + 0.5 * runs + 0.5 * sb
        if score > best_score:
            best_score = score
            best = {
                "name": (player.get("person") or {}).get("fullName", "Unknown"),
                "position": (player.get("position") or {}).get("abbreviation", ""),
                "atBats": at_bats,
                "hits": hits,
                "doubles": doubles,
                "triples": triples,
                "homeRuns": hr,
                "rbi": rbi,
                "runs": runs,
                "stolenBases": sb,
            }
    return best


def top_pitcher(
    boxscore: Dict[str, Any],
    team_id: int,
    win_pitcher_id: Optional[int] = None,
    save_pitcher_id: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    """
    win_pitcher_id / save_pitcher_id come from the schedule endpoint's
    hydrate=decisions (see mlb_api -- the boxscore payload itself doesn't
    reliably carry the W/L/SV decision, so pass it in if you have it).
    """
    side = find_side(boxscore, team_id)
    if side is None:
        return None

    best = None
    best_score = float("-inf")
    for player in get_players_for_side(boxscore, side):
        pitching = (player.get("stats") or {}).get("pitching") or {}
        ip_str = pitching.get("inningsPitched")
        if not ip_str or ip_str in ("0.0", "0"):
            continue  # didn't pitch

        person_id = (player.get("person") or {}).get("id")
        outs = _innings_pitched_to_outs(ip_str)
        so = pitching.get("strikeOuts", 0)
        er = pitching.get("earnedRuns", 0)
        bb = pitching.get("baseOnBalls", 0)
        hits_allowed = pitching.get("hits", 0)

        decision = ""
        if person_id is not None and person_id == win_pitcher_id:
            decision = "W"
        elif person_id is not None and person_id == save_pitcher_id:
            decision = "SV"
        decision_bonus = 5 if decision else 0

        score = outs * 0.5 + so * 1.5 - er * 2 - bb * 0.5 + decision_bonus
        if score > best_score:
            best_score = score
            best = {
                "name": (player.get("person") or {}).get("fullName", "Unknown"),
                "inningsPitched": ip_str,
                "strikeOuts": so,
                "earnedRuns": er,
                "baseOnBalls": bb,
                "hits": hits_allowed,
                "decision": decision,
            }
    return best
