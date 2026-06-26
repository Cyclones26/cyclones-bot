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

import transactions as tx_mod
import tweet_formatter
import boxscore

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
print(tweet_formatter.format_transaction_tweet(classified))
print()

il_tx = {
    "id": 1002,
    "person": {"fullName": "Sebastian Walcott"},
    "typeDesc": "Status Change",
    "description": "Sebastian Walcott placed on the 7-day injured list retroactive to June 20, 2026.",
}
classified = tx_mod.classify_transaction(il_tx, TEAM_ID, LADDER)
assert classified["category"] == tx_mod.CATEGORY_PLACED_ON_IL, classified
print(tweet_formatter.format_transaction_tweet(classified))
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

print("=== Box score parsing ===")

FAKE_BOXSCORE = {
    "teams": {
        "away": {"team": {"id": 999, "name": "Hudson Valley Renegades"}, "players": {}},
        "home": {
            "team": {"id": 509, "name": "Brooklyn Cyclones"},
            "players": {
                "ID1": {
                    "person": {"id": 1, "fullName": "Jett Williams"},
                    "position": {"abbreviation": "SS"},
                    "stats": {
                        "batting": {
                            "atBats": 4, "hits": 3, "doubles": 1, "triples": 0,
                            "homeRuns": 1, "rbi": 4, "runs": 2, "stolenBases": 1,
                        }
                    },
                },
                "ID2": {
                    "person": {"id": 2, "fullName": "Backup Guy"},
                    "position": {"abbreviation": "2B"},
                    "stats": {"batting": {"atBats": 3, "hits": 1, "rbi": 0, "runs": 0}},
                },
                "ID3": {
                    "person": {"id": 3, "fullName": "Ace Pitcher"},
                    "position": {"abbreviation": "P"},
                    "stats": {
                        "pitching": {
                            "inningsPitched": "6.0", "strikeOuts": 9,
                            "earnedRuns": 1, "baseOnBalls": 2, "hits": 4,
                        }
                    },
                },
                "ID4": {
                    "person": {"id": 4, "fullName": "Mop Up Guy"},
                    "position": {"abbreviation": "P"},
                    "stats": {
                        "pitching": {
                            "inningsPitched": "1.0", "strikeOuts": 0,
                            "earnedRuns": 2, "baseOnBalls": 1, "hits": 2,
                        }
                    },
                },
            },
        },
    }
}

hitter = boxscore.top_hitter(FAKE_BOXSCORE, TEAM_ID)
pitcher = boxscore.top_pitcher(FAKE_BOXSCORE, TEAM_ID, win_pitcher_id=3)

assert hitter["name"] == "Jett Williams", hitter
assert pitcher["name"] == "Ace Pitcher", pitcher
assert pitcher["decision"] == "W", pitcher

recap_tweet = tweet_formatter.format_daily_recap_tweet(
    "FINAL (W): Hudson Valley Renegades 3 @ Brooklyn Cyclones 8", hitter, pitcher
)
print(recap_tweet)
print()
assert len(recap_tweet) <= 280

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

print("\nALL OFFLINE CHECKS PASSED")
