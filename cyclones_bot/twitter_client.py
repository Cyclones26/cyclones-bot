"""
twitter_client.py
-------------------
Thin wrapper around tweepy's X API v2 client. Posting a tweet
(`create_tweet`) only needs OAuth1 user-context credentials (API key/secret
+ access token/secret) with "Read and Write" app permissions -- you do NOT
need elevated read access for this bot, which matters because reads and
writes are billed/limited very differently (see README "X API reality
check" section).

If config.DRY_RUN is true, post_tweet() just prints the text and returns a
fake response -- use this constantly while developing so you don't spend
real write quota on test runs.
"""

from __future__ import annotations

import logging
from typing import Optional

import config

logger = logging.getLogger("twitter_client")


class TweetPostError(RuntimeError):
    pass


def _get_client():
    import tweepy  # imported lazily so DRY_RUN runs don't require it installed

    missing = [
        name
        for name, val in (
            ("X_API_KEY", config.X_API_KEY),
            ("X_API_SECRET", config.X_API_SECRET),
            ("X_ACCESS_TOKEN", config.X_ACCESS_TOKEN),
            ("X_ACCESS_SECRET", config.X_ACCESS_SECRET),
        )
        if not val or val.startswith("YOUR_")
    ]
    if missing:
        raise TweetPostError(
            f"Missing X API credentials: {', '.join(missing)}. "
            "Set them as environment variables / GitHub Actions secrets, "
            "or set DRY_RUN=true to test without posting."
        )

    return tweepy.Client(
        consumer_key=config.X_API_KEY,
        consumer_secret=config.X_API_SECRET,
        access_token=config.X_ACCESS_TOKEN,
        access_token_secret=config.X_ACCESS_SECRET,
    )


def post_tweet(text: str) -> Optional[str]:
    """
    Posts `text` to X. Returns the new tweet's id (as a string), or None
    in DRY_RUN mode. Raises TweetPostError on failure.
    """
    if config.DRY_RUN:
        print("\n----- DRY RUN: would have posted this tweet -----")
        print(text)
        print(f"----- ({len(text)} chars) -----\n")
        return None

    client = _get_client()
    try:
        response = client.create_tweet(text=text)
    except Exception as exc:  # tweepy raises various subclasses of TweepyException
        raise TweetPostError(f"Failed to post tweet: {exc}") from exc

    tweet_id = str(response.data.get("id")) if getattr(response, "data", None) else None
    logger.info("Posted tweet id=%s", tweet_id)
    return tweet_id
