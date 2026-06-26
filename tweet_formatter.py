"""
tweet_formatter.py
-------------------
Turns structured data (a classified transaction, a box-score top
performer, a weekly stat line) into fan-friendly tweet text. Kept
separate from the data-fetching modules so you can tweak tone/emojis
without touching any API logic.

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


# --------------------------------------------------------------------------
# 1. Roster Transaction Alerts
# --------------------------------------------------------------------------

_CATEGORY_TEMPLATES = {
    tx_mod.CATEGORY_PROMOTED_FROM_CYCLONES: (
        "\U0001F4C8 PROMOTION ALERT \U0001F4C8\n\n"
        "{player} has been called up from the {team_name} to {to_team}! "
        "Congrats on the next step! \U0001F386\n\n{hashtags}"
    ),
    tx_mod.CATEGORY_DEMOTED_FROM_CYCLONES: (
        "\U0001F504 ROSTER MOVE\n\n"
        "{player} has been reassigned from the {team_name} to {to_team}.\n\n{hashtags}"
    ),
    tx_mod.CATEGORY_PROMOTED_TO_CYCLONES: (
        "\U0001F195 NEW CYCLONE \U0001F195\n\n"
        "{player} has been promoted to the {team_name} from {from_team}! "
        "Welcome to Brooklyn! \U0001F309\n\n{hashtags}"
    ),
    tx_mod.CATEGORY_DEMOTED_TO_CYCLONES: (
        "\U0001F4CB ROSTER MOVE\n\n"
        "{player} joins the {team_name} on assignment from {from_team}.\n\n{hashtags}"
    ),
    tx_mod.CATEGORY_PLACED_ON_IL: (
        "\U0001FA79 IL UPDATE\n\n"
        "{player} has been placed on the Injured List. Get well soon! \U0001F4AA\n\n{hashtags}"
    ),
    tx_mod.CATEGORY_ACTIVATED_FROM_IL: (
        "✅ BACK IN ACTION\n\n"
        "{player} has been activated off the Injured List for the {team_name}! "
        "Welcome back! \U0001F3D2\n\n{hashtags}"
    ),
    tx_mod.CATEGORY_SIGNED: (
        "✍️ SIGNED\n\n{player} has signed with the {team_name}! \U0001F389\n\n{hashtags}"
    ),
    tx_mod.CATEGORY_RELEASED: (
        "\U0001F4C4 ROSTER MOVE\n\n"
        "{player} has been released by the {team_name}. Thank you for your time in Brooklyn. \U0001F499\n\n{hashtags}"
    ),
    tx_mod.CATEGORY_OTHER: (
        "\U0001F4CB ROSTER MOVE\n\n{player}: {description}\n\n{hashtags}"
    ),
}


def format_transaction_tweet(classified: Dict[str, Any]) -> str:
    template = _CATEGORY_TEMPLATES.get(classified["category"], _CATEGORY_TEMPLATES[tx_mod.CATEGORY_OTHER])
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
# 2. Daily Post-Game Stat Lines
# --------------------------------------------------------------------------

def format_daily_recap_tweet(
    game_result_line: str,
    top_hitter: Optional[Dict[str, Any]],
    top_pitcher: Optional[Dict[str, Any]],
) -> str:
    lines = [f"\U0001F386 {config.TEAM_NAME.upper()} RECAP \U0001F386", "", game_result_line, ""]

    if top_hitter:
        hit_bits = [f"{top_hitter['hits']}-for-{top_hitter['atBats']}"]
        if top_hitter.get("homeRuns"):
            hit_bits.append(f"{top_hitter['homeRuns']} HR")
        if top_hitter.get("rbi"):
            hit_bits.append(f"{top_hitter['rbi']} RBI")
        if top_hitter.get("doubles"):
            hit_bits.append(f"{top_hitter['doubles']} 2B")
        if top_hitter.get("triples"):
            hit_bits.append(f"{top_hitter['triples']} 3B")
        lines.append(f"\U0001F4A5 {top_hitter['name']}: " + ", ".join(hit_bits))

    if top_pitcher:
        pitch_bits = [f"{top_pitcher['inningsPitched']} IP", f"{top_pitcher['strikeOuts']} K"]
        if top_pitcher.get("earnedRuns") == 0:
            pitch_bits.append("0 ER")
        else:
            pitch_bits.append(f"{top_pitcher['earnedRuns']} ER")
        decision = f" ({top_pitcher['decision']})" if top_pitcher.get("decision") else ""
        lines.append(f"⚾ {top_pitcher['name']}{decision}: " + ", ".join(pitch_bits))

    lines.append("")
    lines.append(config.TEAM_HASHTAGS)
    return _truncate("\n".join(lines))


# --------------------------------------------------------------------------
# 3. Weekly Performance Summaries
# --------------------------------------------------------------------------

def format_weekly_summary_tweet(
    start_date: str,
    end_date: str,
    hot_hitters: List[Dict[str, Any]],
    hot_pitchers: List[Dict[str, Any]],
) -> str:
    lines = [
        f"\U0001F525 WEEKLY HOT STREAKS \U0001F525",
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
# 4. Player Tracker: long-term milestones + periodic progress updates
# --------------------------------------------------------------------------

_PLAYER_MILESTONE_TEMPLATES = {
    pm_mod.MILESTONE_PROMOTED: (
        "\U0001F4C8 LEVEL UP \U0001F4C8\n\n"
        "{player} has been promoted from the {old_team} to the {new_team}! "
        "The development continues. \U0001F31F\n\n{hashtags}"
    ),
    pm_mod.MILESTONE_DEMOTED: (
        "\U0001F504 ROSTER MOVE\n\n"
        "{player} has been reassigned from the {old_team} to the {new_team}.\n\n{hashtags}"
    ),
    pm_mod.MILESTONE_TRADED_ORG: (
        "\U0001F501 TRADED\n\n"
        "{player} is now in the {new_org} organization, assigned to the "
        "{new_team}. We'll keep tracking his progress! \U0001F440\n\n{hashtags}"
    ),
    pm_mod.MILESTONE_LATERAL_MOVE: (
        "\U0001F4CB ROSTER MOVE\n\n{player} has been moved to the {new_team}.\n\n{hashtags}"
    ),
    pm_mod.MILESTONE_LEFT_AFFILIATED_BALL: (
        "\U0001F4C4 STATUS UPDATE\n\n"
        "{player} is no longer with an affiliated MLB/MiLB club. Thanks for "
        "the memories in the system. \U0001F499\n\n{hashtags}"
    ),
    pm_mod.MILESTONE_PLACED_ON_IL: (
        "\U0001FA79 IL UPDATE\n\n"
        "{player} ({new_team_for_il}) has been placed on the Injured List. "
        "Get well soon! \U0001F4AA\n\n{hashtags}"
    ),
    pm_mod.MILESTONE_ACTIVATED_FROM_IL: (
        "✅ BACK IN ACTION\n\n"
        "{player} has been activated off the Injured List for the "
        "{new_team_for_il}!\n\n{hashtags}"
    ),
}


def format_player_milestone_tweet(player_name: str, milestone: Dict[str, Any]) -> str:
    """`milestone` is the dict returned by player_milestones.classify_player_change()."""
    category = milestone.get("category")
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
        "From Brooklyn to The Show — a moment to remember. \U0001F30C\n\n"
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
    lines = [f"\U0001F4CA PLAYER WATCH: {player_name}", f"{team_name} ({level_name})", ""]

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
    lines.append(f"Drafted/developed by the {config.TEAM_NAME} \U0001F309")
    lines.append(config.TEAM_HASHTAGS)
    return _truncate("\n".join(lines))
