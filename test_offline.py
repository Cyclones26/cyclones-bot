"""
test_offline.py
-----------------
Quick offline sanity check using hand-built fixtures that mirror the real
MLB Stats API response shapes (the sandbox this was built in can't reach
statsapi.mlb.com directly, so this substitutes for a live smoke test --
re-run something like this against real data before your first live post).

Run: DRY_RUN=true python test_offline.py
"""

import os

os.environ.setdefault("DRY_RUN", "true")

import config
import transactions as tx_mod
import tweet_formatter
import player_milestones as pm_mod

TEAM_ID = 509

# --- Fixture: affiliate ladder (shape matches mlb_api.get_affiliate_ladder) ---
LADDER = {
    1: {"teamId": 121, "name": "New York Mets", "rank": 5},
    11: {"teamId": 1325, "name": "Syracuse Mets", "rank": 4},
    12: {"teamId": 1324, "name": "Binghamton Rumble Ponies", "rank": 3},
    13: {"teamId": 509, "name": "Brooklyn Cyclones", "rank": 2},
    14: {"teamId": 2786, "name": "St. Lucie Mets", "rank": 1},
}

print("=== Transaction classification + tweet formatting ===")

promotion_tx = {
    "id": 1001,
    "person": {"fullName": "Jett Williams"},
    "fromTeam": {"id": 509, "name": "Brooklyn Cyclones"},
    "toTeam": {"id": 1324, "name": "Binghamton Rumble Ponies"},
    "typeDesc": "Optioned",
    "description": "Jett Williams optioned to Binghamton Rumble Ponies from Brooklyn Cyclones.",
    "date": "2026-06-24",
}
classified = tx_mod.classify_transaction(promotion_tx, TEAM_ID, LADDER)
assert classified["category"] == tx_mod.CATEGORY_PROMOTED_FROM_CYCLONES, classified
tweet = tweet_formatter.format_transaction_tweet(classified)
assert "MOVING UP" in tweet and "Binghamton" in tweet, tweet
print(tweet)
print()

call_up_tx = {
    "id": 1003,
    "person": {"fullName": "Carson Benge"},
    "fromTeam": {"id": 2786, "name": "St. Lucie Mets"},
    "toTeam": {"id": 509, "name": "Brooklyn Cyclones"},
    "typeDesc": "Assigned",
    "description": "Carson Benge assigned to Brooklyn Cyclones from St. Lucie Mets.",
}
classified = tx_mod.classify_transaction(call_up_tx, TEAM_ID, LADDER)
assert classified["category"] == tx_mod.CATEGORY_PROMOTED_TO_CYCLONES, classified
print(tweet_formatter.format_transaction_tweet(classified))
print()

print("=== IL detail extraction (injury type / retro date, best-effort) ===")

# Style 1: injury as a trailing sentence.
il_tx = {
    "id": 1002,
    "person": {"fullName": "Sebastian Walcott"},
    "typeDesc": "Status Change",
    "description": (
        "Brooklyn Cyclones placed SS Sebastian Walcott on the 7-day injured "
        "list retroactive to June 20, 2026. Right hamstring strain."
    ),
}
classified = tx_mod.classify_transaction(il_tx, TEAM_ID, LADDER)
assert classified["category"] == tx_mod.CATEGORY_PLACED_ON_IL, classified
details = classified.get("il_details") or {}
assert details.get("il_days") == "7", details
assert details.get("retro_date") == "June 20, 2026", details
assert details.get("injury") == "Right hamstring strain", details
il_tweet = tweet_formatter.format_transaction_tweet(classified)
assert "7-day IL" in il_tweet and "hamstring strain" in il_tweet.lower(), il_tweet
assert len(il_tweet) <= 280
print(il_tweet)
print()

# Style 2: injury inline with "with a ...".
il_tx2 = {
    "id": 1004,
    "person": {"fullName": "Tanner Witt"},
    "typeDesc": "Status Change",
    "description": (
        "Brooklyn Cyclones placed RHP Tanner Witt on the 7-day injured list "
        "with a right elbow sprain."
    ),
}
classified = tx_mod.classify_transaction(il_tx2, TEAM_ID, LADDER)
assert classified["category"] == tx_mod.CATEGORY_PLACED_ON_IL, classified
assert (classified.get("il_details") or {}).get("injury") == "right elbow sprain", classified
print(tweet_formatter.format_transaction_tweet(classified))
print()

# Style 3: no detail published at all -- tweet must degrade gracefully.
il_tx3 = {
    "id": 1005,
    "person": {"fullName": "Felix Cepeda"},
    "typeDesc": "Status Change",
    "description": "Brooklyn Cyclones placed RHP Felix Cepeda on the injured list.",
}
classified = tx_mod.classify_transaction(il_tx3, TEAM_ID, LADDER)
assert classified["category"] == tx_mod.CATEGORY_PLACED_ON_IL, classified
bare_tweet = tweet_formatter.format_transaction_tweet(classified)
assert "Felix Cepeda" in bare_tweet and len(bare_tweet) <= 280, bare_tweet
print(bare_tweet)
print()

print("=== Do-not-track list ===")
assert 621345 in config.DO_NOT_TRACK_IDS   # A.J. Minter
assert 476594 in config.DO_NOT_TRACK_IDS   # Robert Stock
assert 640470 in config.DO_NOT_TRACK_IDS   # Adbert Alzolay
assert 643361 in config.DO_NOT_TRACK_IDS   # Kevin Herget
assert 666197 in config.DO_NOT_TRACK_IDS   # Grae Kessinger
assert 682175 in config.DO_NOT_TRACK_IDS   # Joe Jacques
print(f"{len(config.DO_NOT_TRACK_IDS)} player(s) on the do-not-track list.")
print()

print("=== Weekly summary formatting ===")
hot_hitters = [
    {"name": "Jett Williams", "avg": 0.412, "hits": 14, "atBats": 34, "homeRuns": 3, "rbi": 9, "ops": 1.150},
]
hot_pitchers = [
    {"name": "Ace Pitcher", "era": 0.96, "strikeOuts": 16, "inningsPitched": "12.2"},
]
weekly_tweet = tweet_formatter.format_weekly_summary_tweet("2026-06-18", "2026-06-24", hot_hitters, hot_pitchers)
print(weekly_tweet)
assert len(weekly_tweet) <= 280

print("=== Player Tracker: milestone classification + tweet formatting ===")

# First-ever check on a player -- nothing to diff against yet, must be silent.
brand_new = {"name": "Mitch Voit", "_initialized": False, "addedDate": "2026-06-26"}
assert pm_mod.classify_player_change(brand_new, brand_new) is None

base_snapshot = {
    "name": "Mitch Voit",
    "currentTeamId": 509,
    "currentTeamName": "Brooklyn Cyclones",
    "sportId": 13,
    "levelRank": 2,
    "parentOrgName": "New York Mets",
    "primaryPosition": "1B",
    "mlbDebutSeen": False,
    "lastStatus": "ACTIVE",
    "_initialized": True,
}

# No change at all -> no milestone.
assert pm_mod.classify_player_change(base_snapshot, dict(base_snapshot)) is None

# Promotion within the same org (Brooklyn -> Binghamton).
promoted = dict(base_snapshot, currentTeamId=1324, currentTeamName="Binghamton Rumble Ponies",
                 sportId=12, levelRank=3)
milestone = pm_mod.classify_player_change(base_snapshot, promoted)
assert milestone["category"] == pm_mod.MILESTONE_PROMOTED, milestone
print(tweet_formatter.format_player_milestone_tweet("Mitch Voit", milestone))
print()

# Traded to a different organization at the same level.
traded = dict(base_snapshot, currentTeamId=9999, currentTeamName="Some Other Team",
              parentOrgName="Some Other Org")
milestone = pm_mod.classify_player_change(base_snapshot, traded)
assert milestone["category"] == pm_mod.MILESTONE_TRADED_ORG, milestone
print(tweet_formatter.format_player_milestone_tweet("Mitch Voit", milestone))
print()

# MLB debut takes priority even if the team also changed.
debuted = dict(base_snapshot, currentTeamId=121, currentTeamName="New York Mets",
               sportId=1, levelRank=5, mlbDebutSeen=True)
milestone = pm_mod.classify_player_change(base_snapshot, debuted)
assert milestone["category"] == pm_mod.MILESTONE_MLB_DEBUT, milestone
debut_tweet = tweet_formatter.format_player_debut_tweet("Mitch Voit", milestone.get("team_name"))
print(debut_tweet)
print()
assert len(debut_tweet) <= 280

# Released / left affiliated ball entirely.
left_ball = dict(base_snapshot, currentTeamId=None, currentTeamName=None)
milestone = pm_mod.classify_player_change(base_snapshot, left_ball)
assert milestone["category"] == pm_mod.MILESTONE_LEFT_AFFILIATED_BALL, milestone
print(tweet_formatter.format_player_milestone_tweet("Mitch Voit", milestone))
print()

# Best-effort IL placement WITH an injury note from the transaction feed.
on_il = dict(base_snapshot, lastStatus="IL")
milestone = pm_mod.classify_player_change(base_snapshot, on_il)
assert milestone["category"] == pm_mod.MILESTONE_PLACED_ON_IL, milestone
il_note = (
    "Brooklyn Cyclones placed 1B Mitch Voit on the 7-day injured list "
    "retroactive to July 15, 2026. Left wrist soreness."
)
il_milestone_tweet = tweet_formatter.format_player_milestone_tweet(
    "Mitch Voit", milestone, injury_note=il_note
)
assert "7-day IL" in il_milestone_tweet and "wrist soreness" in il_milestone_tweet.lower(), il_milestone_tweet
assert len(il_milestone_tweet) <= 280
print(il_milestone_tweet)
print()

# ...and WITHOUT any note (MLB published nothing) -- must degrade gracefully.
print(tweet_formatter.format_player_milestone_tweet("Mitch Voit", milestone))
print()

# Activation off the IL.
activated = dict(on_il, lastStatus="ACTIVE")
milestone = pm_mod.classify_player_change(on_il, activated)
assert milestone["category"] == pm_mod.MILESTONE_ACTIVATED_FROM_IL, milestone
print(tweet_formatter.format_player_milestone_tweet("Mitch Voit", milestone))
print()

# Periodic "graduate" progress tweet, for both a hitter and a pitcher.
hitter_stat = {"avg": 0.305, "hits": 9, "atBats": 28, "homeRuns": 2, "rbi": 7}
progress_tweet = tweet_formatter.format_player_progress_tweet(
    "Mitch Voit", "Binghamton Rumble Ponies", "AA", False, hitter_stat
)
print(progress_tweet)
assert len(progress_tweet) <= 280

pitcher_stat = {"inningsPitched": "6.2", "strikeOuts": 8, "era": 2.10}
progress_tweet = tweet_formatter.format_player_progress_tweet(
    "Ace Pitcher", "Syracuse Mets", "AAA", True, pitcher_stat
)
print(progress_tweet)
assert len(progress_tweet) <= 280

print("=== Rehab assignment / return classification ===")

il_snapshot = dict(base_snapshot, lastStatus="IL")
rehab = dict(base_snapshot, currentTeamId=468, currentTeamName="FCL Mets",
             sportId=16, levelRank=0, lastStatus="ACTIVE")
milestone = pm_mod.classify_player_change(il_snapshot, rehab)
assert milestone["category"] == pm_mod.MILESTONE_REHAB_ASSIGNMENT, milestone
print(tweet_formatter.format_player_milestone_tweet("Mitch Voit", milestone))
print()

# Return from rehab must NOT read as a promotion.
on_rehab = dict(rehab, onRehab=True)
returned = dict(base_snapshot, lastStatus="ACTIVE")
milestone = pm_mod.classify_player_change(on_rehab, returned)
assert milestone["category"] == pm_mod.MILESTONE_REHAB_RETURN, milestone
print(tweet_formatter.format_player_milestone_tweet("Mitch Voit", milestone))
print()

# A normal demotion (not from IL) must still classify as a demotion.
to_complex = dict(base_snapshot, currentTeamId=468, currentTeamName="FCL Mets",
                  sportId=16, levelRank=0)
milestone = pm_mod.classify_player_change(base_snapshot, to_complex)
assert milestone["category"] == pm_mod.MILESTONE_DEMOTED, milestone

print("=== Big-game feat detection ===")

GAMELOG = [
    {"date": "2026-07-10", "stat": {"homeRuns": 0, "hits": 1, "rbi": 0}},
    {"date": "2026-07-14", "stat": {"homeRuns": 2, "hits": 3, "rbi": 5}},
    {"date": "2026-07-16", "stat": {"homeRuns": 0, "hits": 4, "rbi": 1}},
]
feats = pm_mod.detect_game_feats(GAMELOG, is_pitcher=False, since_date="2026-07-12")
assert len(feats) == 2, feats
assert "2 home runs" in feats[0]["desc"] and "5 RBI" in feats[0]["desc"], feats
assert "4-hit game" in feats[1]["desc"], feats
# The 07-10 game is before since_date and must be excluded.
assert all(f["date"] > "2026-07-12" for f in feats)
print(tweet_formatter.format_player_feat_tweet(
    "Mitch Voit", "Brooklyn Cyclones", "High-A", feats[0]["date"], feats[0]["desc"]))
print()

PITCHER_LOG = [
    {"date": "2026-07-15", "stat": {"strikeOuts": 11, "inningsPitched": "6.0"}},
    {"date": "2026-07-09", "stat": {"strikeOuts": 12, "inningsPitched": "7.0"}},
]
pfeats = pm_mod.detect_game_feats(PITCHER_LOG, is_pitcher=True, since_date="2026-07-12")
assert len(pfeats) == 1 and "11 strikeouts" in pfeats[0]["desc"], pfeats

assert pm_mod.season_hr_total(GAMELOG) == 2
assert pm_mod.first_hr_date(GAMELOG) == "2026-07-14"

print("=== Progress tweet with season-to-date line ===")
season_stat = {"avg": 0.291, "homeRuns": 11, "rbi": 44}
progress_tweet = tweet_formatter.format_player_progress_tweet(
    "Mitch Voit", "Binghamton Rumble Ponies", "AA", False, hitter_stat, season_stat=season_stat
)
assert "season: .291, 11 HR, 44 RBI" in progress_tweet, progress_tweet
assert len(progress_tweet) <= 280
print(progress_tweet)
print()

print("=== Season recap tweets ===")
recap_players = {
    "1": {"name": "Mitch Voit", "initialTeamName": "Brooklyn Cyclones", "initialLevelRank": 2,
          "currentTeamName": "Binghamton Rumble Ponies", "levelRank": 3, "currentTeamId": 505,
          "parentOrgName": "New York Mets", "mlbDebutSeen": False, "initialMlbDebutSeen": False},
    "2": {"name": "Ace Pitcher", "initialTeamName": "Brooklyn Cyclones", "initialLevelRank": 2,
          "currentTeamName": "New York Mets", "levelRank": 5, "currentTeamId": 121,
          "parentOrgName": "New York Mets", "mlbDebutSeen": True, "initialMlbDebutSeen": False},
    "3": {"name": "Steady Guy", "initialTeamName": "Brooklyn Cyclones", "initialLevelRank": 2,
          "currentTeamName": "Brooklyn Cyclones", "levelRank": 2, "currentTeamId": 453,
          "parentOrgName": "New York Mets", "mlbDebutSeen": False, "initialMlbDebutSeen": False},
    "4": {"name": "Traded Guy", "initialTeamName": "Brooklyn Cyclones", "initialLevelRank": 2,
          "currentTeamName": "Indianapolis Indians", "levelRank": 4, "currentTeamId": 484,
          "parentOrgName": "Pittsburgh Pirates", "mlbDebutSeen": False, "initialMlbDebutSeen": False},
}
recap_tweets = tweet_formatter.format_season_recap_tweets(recap_players, 2026)
assert len(recap_tweets) == 2, recap_tweets
assert "We followed 4 Cyclones" in recap_tweets[0], recap_tweets[0]
assert "3 climbed at least one level" in recap_tweets[0], recap_tweets[0]
assert "1 made their MLB debut" in recap_tweets[0], recap_tweets[0]
assert "Ace Pitcher" in recap_tweets[1], recap_tweets[1]
for t in recap_tweets:
    assert len(t) <= 280
    print(t)
    print()

print("=== Extra-track IDs config ===")
assert isinstance(config.EXTRA_TRACK_IDS, set)
assert config.EXTRA_TRACK_IDS.isdisjoint(config.DO_NOT_TRACK_IDS)

print("\nALL OFFLINE CHECKS PASSED")
