"""
tweet_formatter.py
-------------------
Turns structured data (a classified transaction, a player-tracker
milestone, a weekly stat line) into fan-friendly tweet text. Kept
separate from the data-fetching modules so you can tweak tone/emojis
without touching any API logic.

FORMAT PHILOSOPHY (July 2026 refresh): this bot is about *player
development*, not scoreboard watching. Game recaps are gone entirely.
Every template is written around the player's journey -- where he came
from, where he's headed, how he's progressing -- rather than one game's
result.

All formatters truncate to TWEET_MAX_CHARS so a long player name or
description never gets silently rejected by the X API.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import config
import player_milestones as pm_mod
import transactions as tx_mod

TWEET_MAX_CHARS = 280


def _truncate(text: str, max_chars: int = TWEET_MAX_CHARS) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"  # add an ellipsis


def _il_detail_line(il_details: Optional[Dict[str, str]]) -> str:
    """
    Builds a human-readable injury-detail fragment from
    transactions.extract_il_details() output, e.g.:
      "7-day IL (right shoulder strain), retroactive to July 10"
    Returns "" when there's nothing useful to say -- the tweet then reads
    as a plain IL notice. MLB only sometimes publishes the specific injury
    for minor leaguers, so this is best-effort by design.
    """
    if not il_details:
        return ""
    bits: List[str] = []
    if il_details.get("il_days"):
        bits.append(f"{il_details['il_days']}-day IL")
    if il_details.get("injury"):
        injury = il_details["injury"].strip().rstrip(".")
        if bits:
            bits[-1] += f" ({injury.lower() if injury[:1].isupper() and not injury.isupper() else injury})"
        else:
            bits.append(injury)
    if il_details.get("retro_date"):
        bits.append(f"retroactive to {il_details['retro_date']}")
    return ", ".join(bits)


# --------------------------------------------------------------------------
# 1. Roster Transaction Alerts (development-focused templates)
# --------------------------------------------------------------------------

_CATEGORY_TEMPLATES = {
    tx_mod.CATEGORY_PROMOTED_FROM_CYCLONES: (
        "\U0001F4C8 MOVING UP\n\n"
        "{player} has earned the call from Brooklyn to {to_team} — "
        "the next rung on the development ladder. \U0001F31F\n\n{hashtags}"
    ),
    tx_mod.CATEGORY_DEMOTED_FROM_CYCLONES: (
        "\U0001F504 DEVELOPMENT MOVE\n\n"
        "{player} heads to {to_team} to keep working on his game.\n\n{hashtags}"
    ),
    tx_mod.CATEGORY_PROMOTED_TO_CYCLONES: (
        "\U0001F195 WELCOME TO BROOKLYN\n\n"
        "{player} earns the bump to High-A, joining the {team_name} "
        "from {from_team}. The journey continues. \U0001F309\n\n{hashtags}"
    ),
    tx_mod.CATEGORY_DEMOTED_TO_CYCLONES: (
        "\U0001F4CB DEVELOPMENT MOVE\n\n"
        "{player} joins the {team_name} on assignment from {from_team}.\n\n{hashtags}"
    ),
    tx_mod.CATEGORY_ACTIVATED_FROM_IL: (
        "✅ BACK IN ACTION\n\n"
        "{player} is off the injured list and back on the field for the "
        "{team_name}. \u26BE\n\n{hashtags}"
    ),
    tx_mod.CATEGORY_SIGNED: (
        "✍️ SIGNED\n\n{player} has signed with the {team_name}! "
        "A new chapter starts in Brooklyn. \U0001F389\n\n{hashtags}"
    ),
    tx_mod.CATEGORY_RELEASED: (
        "\U0001F4C4 ROSTER MOVE\n\n"
        "{player} has been released by the {team_name}. Thank you for your "
        "time in Brooklyn. \U0001F499\n\n{hashtags}"
    ),
    tx_mod.CATEGORY_OTHER: (
        "\U0001F4CB ROSTER MOVE\n\n{player}: {description}\n\n{hashtags}"
    ),
}


def format_transaction_tweet(classified: Dict[str, Any]) -> str:
    category = classified["category"]

    # IL placements get their own builder so injury details (when MLB
    # publishes them) can be woven in.
    if category == tx_mod.CATEGORY_PLACED_ON_IL:
        detail = _il_detail_line(classified.get("il_details"))
        detail_line = f"\n{detail.capitalize() if detail and detail[0].islower() else detail}\n" if detail else ""
        text = (
            "\U0001FA79 INJURY UPDATE\n\n"
            f"{classified.get('player', 'A player')} has been placed on the injured list.\n"
            f"{detail_line}"
            "\nGet well soon. \U0001F4AA\n\n"
            f"{config.TEAM_HASHTAGS}"
        )
        return _truncate(text)

    template = _CATEGORY_TEMPLATES.get(category, _CATEGORY_TEMPLATES[tx_mod.CATEGORY_OTHER])
    text = template.format(
        player=classified.get("player", "A player"),
        team_name=config.TEAM_NAME,
        from_team=classified.get("from_team") or "their previous club",
        to_team=classified.get("to_team") or "their new club",
        description=classified.get("description", ""),
        hashtags=config.TEAM_HASHTAGS,
    )
    return _truncate(text)


# --------------------------------------------------------------------------
# 2. Weekly Performance Summaries (current Cyclones)
# --------------------------------------------------------------------------

def format_weekly_summary_tweet(
    start_date: str,
    end_date: str,
    hot_hitters: List[Dict[str, Any]],
    hot_pitchers: List[Dict[str, Any]],
) -> str:
    lines = [
        "\U0001F525 DEVELOPMENT REPORT: WHO'S TRENDING UP \U0001F525",
        f"{start_date} - {end_date}",
        "",
    ]

    for h in hot_hitters[:2]:
        lines.append(
            f"\U0001F4CA {h['name']}: .{int(round(h['avg'] * 1000)):03d} "
            f"({h['hits']}-for-{h['atBats']}), {h['homeRuns']} HR, {h['rbi']} RBI"
        )

    for p in hot_pitchers[:1]:
        lines.append(
            f"\U0001F3AF {p['name']}: {p['era']:.2f} ERA, {p['strikeOuts']} K "
            f"over {p['inningsPitched']} IP"
        )

    lines.append("")
    lines.append(config.TEAM_HASHTAGS)
    return _truncate("\n".join(lines))


# --------------------------------------------------------------------------
# 3. Player Tracker: long-term milestones + periodic progress updates
# --------------------------------------------------------------------------

_PLAYER_MILESTONE_TEMPLATES = {
    pm_mod.MILESTONE_PROMOTED: (
        "\U0001F4C8 LEVEL UP \U0001F4C8\n\n"
        "{player}: {old_team} ➡️ {new_team}\n\n"
        "Another step up the ladder for a Brooklyn alum. \U0001F31F\n\n{hashtags}"
    ),
    pm_mod.MILESTONE_DEMOTED: (
        "\U0001F504 DEVELOPMENT MOVE\n\n"
        "{player} has been reassigned from the {old_team} to the {new_team}. "
        "Development isn't always a straight line.\n\n{hashtags}"
    ),
    pm_mod.MILESTONE_TRADED_ORG: (
        "\U0001F501 TRADED\n\n"
        "{player} is now in the {new_org} organization, assigned to the "
        "{new_team}. Once a Cyclone, always a Cyclone — we'll keep following "
        "his journey. \U0001F440\n\n{hashtags}"
    ),
    pm_mod.MILESTONE_LATERAL_MOVE: (
        "\U0001F4CB ROSTER MOVE\n\n{player} has been moved to the {new_team}.\n\n{hashtags}"
    ),
    pm_mod.MILESTONE_LEFT_AFFILIATED_BALL: (
        "\U0001F4C4 STATUS UPDATE\n\n"
        "{player} is no longer with an affiliated MLB/MiLB club. Wherever the "
        "road leads next — thanks for the memories in the system. \U0001F499\n\n{hashtags}"
    ),
    pm_mod.MILESTONE_ACTIVATED_FROM_IL: (
        "✅ BACK IN ACTION\n\n"
        "{player} is off the injured list and back on the field for the "
        "{new_team_for_il}. The development clock starts again. \u26BE\n\n{hashtags}"
    ),
}


def format_player_milestone_tweet(
    player_name: str,
    milestone: Dict[str, Any],
    injury_note: Optional[str] = None,
) -> str:
    """
    `milestone` is the dict returned by player_milestones.classify_player_change().
    `injury_note` is an optional raw transaction description for IL
    placements (player_tracker.py looks it up); any injury detail in it
    gets woven into the tweet.
    """
    category = milestone.get("category")

    if category == pm_mod.MILESTONE_PLACED_ON_IL:
        detail = _il_detail_line(tx_mod.extract_il_details(injury_note))
        detail_line = f"\n{detail.capitalize() if detail and detail[0].islower() else detail}\n" if detail else ""
        text = (
            "\U0001FA79 INJURY UPDATE\n\n"
            f"{player_name} ({milestone.get('team_name') or 'his club'}) has been "
            "placed on the injured list.\n"
            f"{detail_line}"
            "\nGet well soon. \U0001F4AA\n\n"
            f"{config.TEAM_HASHTAGS}"
        )
        return _truncate(text)

    template = _PLAYER_MILESTONE_TEMPLATES.get(category)
    if template is None:
        text = f"\U0001F4CB PLAYER WATCH\n\n{player_name}: status update.\n\n{config.TEAM_HASHTAGS}"
        return _truncate(text)

    text = template.format(
        player=player_name,
        old_team=milestone.get("old_team_name") or "their previous club",
        new_team=milestone.get("new_team_name") or "their new club",
        new_org=milestone.get("new_org") or "a new organization",
        new_team_for_il=milestone.get("team_name") or "their club",
        hashtags=config.TEAM_HASHTAGS,
    )
    return _truncate(text)


def format_player_debut_tweet(player_name: str, team_name: Optional[str]) -> str:
    """A dedicated, extra-celebratory template for the rarest milestone of all."""
    where = f" with the {team_name}" if team_name else ""
    text = (
        "\U0001F386\U0001F386 MLB DEBUT \U0001F386\U0001F386\n\n"
        f"{player_name} has made his Major League debut{where}! "
        "From Coney Island to The Show — this is what development is all "
        "about. \U0001F30C\n\n"
        f"{config.TEAM_HASHTAGS}"
    )
    return _truncate(text)


def format_player_progress_tweet(
    player_name: str,
    team_name: str,
    level_name: str,
    is_pitcher: bool,
    stat: Dict[str, Any],
) -> str:
    """
    Periodic "how's he doing now" update for a tracked player who has
    moved on from the Cyclones (the weekly_summary.py hot-streaks tweet
    already covers the current roster, so this is specifically for
    graduates -- see player_tracker.py).
    """
    lines = [f"\U0001F4CA DEVELOPMENT WATCH: {player_name}", f"{team_name} ({level_name})", ""]

    if is_pitcher:
        era = stat.get("era", 0) or 0
        lines.append(
            f"Last 7 days: {stat.get('inningsPitched', '0.0')} IP, "
            f"{stat.get('strikeOuts', 0)} K, {float(era):.2f} ERA"
        )
    else:
        avg = stat.get("avg", 0) or 0
        lines.append(
            f"Last 7 days: .{int(round(float(avg) * 1000)):03d} "
            f"({stat.get('hits', 0)}-for-{stat.get('atBats', 0)}), "
            f"{stat.get('homeRuns', 0)} HR, {stat.get('rbi', 0)} RBI"
        )

    lines.append("")
    lines.append("Once a Cyclone, always a Cyclone \U0001F309")
    lines.append(config.TEAM_HASHTAGS)
    return _truncate("\n".join(lines))
