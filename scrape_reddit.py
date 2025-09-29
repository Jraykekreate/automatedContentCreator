#!/usr/bin/env python3
"""
reddit_engagement_reddit_api.py

Fetch subreddit posts from the official Reddit API (OAuth) from the last N days,
compute an EngagementScore, and print the top-N posts.

Requirements:
    pip install requests

Before running:
    1. Create a "script" app at https://www.reddit.com/prefs/apps
       - copy client_id and client_secret
    2. Export the following env vars (example for bash):
       export REDDIT_CLIENT_ID="WHZ4OSAZ-XptVB4hNOtm3g"
       export REDDIT_CLIENT_SECRET="0eOGACJSqTXfxvVweafwpZ8NEP04Pw"
       export REDDIT_USERNAME="Xaje_Fafa"
       export REDDIT_PASSWORD="agbakpe03211"
       export REDDIT_USER_AGENT="myscript/0.1 by Xaje_Fafa"

Usage:
    python reddit_engagement_reddit_api.py --subreddit soccer --days 3 --top 20
"""
from __future__ import annotations
import os
import time
import math
import argparse
import requests
from datetime import datetime, timezone
import sys
import json

TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
OAUTH_API_BASE = "https://oauth.reddit.com"

def get_oauth_token(client_id: str, client_secret: str, username: str, password: str, user_agent: str, timeout: int = 20) -> str:
    """
    Get OAuth2 access token using "password" grant for script apps.
    """
    auth = requests.auth.HTTPBasicAuth(client_id, client_secret)
    data = {"grant_type": "password", "username": username, "password": password}
    headers = {"User-Agent": user_agent}
    resp = requests.post(TOKEN_URL, auth=auth, data=data, headers=headers, timeout=timeout)
    if resp.status_code != 200:
        raise RuntimeError(f"Failed to obtain token: {resp.status_code} {resp.text}")
    j = resp.json()
    return j["access_token"]

def fetch_subreddit_new(subreddit: str, access_token: str, user_agent: str, cutoff_ts: int, page_limit: int = 100, max_pages: int = 50):
    """
    Paginate /r/{subreddit}/new and collect posts with created_utc >= cutoff_ts.
    Stops once it encounters posts older than cutoff (since 'new' is newest-first).
    Returns list of post data dicts.
    """
    headers = {"Authorization": f"bearer {access_token}", "User-Agent": user_agent}
    url = f"{OAUTH_API_BASE}/r/{subreddit}/new"
    params = {"limit": page_limit}
    collected = []
    pages = 0
    after = None

    while pages < max_pages:
        if after:
            params["after"] = after
        resp = requests.get(url, headers=headers, params=params, timeout=20)
        if resp.status_code == 401:
            raise RuntimeError("Unauthorized — token probably expired or wrong credentials.")
        if resp.status_code != 200:
            raise RuntimeError(f"Reddit API returned {resp.status_code}: {resp.text}")
        data = resp.json().get("data", {})
        children = data.get("children", [])
        if not children:
            break

        pages += 1
        stop_early = False
        for ch in children:
            post = ch.get("data", {})
            created = int(post.get("created_utc", 0))
            if created >= cutoff_ts:
                # keep only useful fields to reduce memory
                mini = {
                    "id": post.get("id"),
                    "name": post.get("name"),  # fullname like t3_xxx
                    "created_utc": created,
                    "title": post.get("title"),
                    "score": post.get("score", 0),
                    "num_comments": post.get("num_comments", 0),
                    "total_awards_received": post.get("total_awards_received", 0),
                    "permalink": post.get("permalink"),
                    "author": post.get("author"),
                }
                collected.append(mini)
            else:
                # Once we find a post older than cutoff in 'new' listing, we can stop paging.
                stop_early = True
                break

        if stop_early:
            break

        after = data.get("after")
        if not after:
            break

        # be polite / account for rate limits
        time.sleep(1.0)

    return collected

def compute_engagement(post: dict, alpha: float, beta: float, gamma: float, award_scale: float = 10.0) -> float:
    """
    EngagementScore = α * log10(1 + score)
                    + β * log10(1 + num_comments)
                    + γ * log10(1 + total_awards_received * award_scale)
    """
    score = max(0, int(post.get("score") or 0))
    num_comments = max(0, int(post.get("num_comments") or 0))
    awards = max(0, int(post.get("total_awards_received") or 0))

    part_votes = alpha * math.log10(1 + score) if score >= 0 else 0.0
    part_comments = beta * math.log10(1 + num_comments)
    part_awards = gamma * math.log10(1 + awards * award_scale)

    return part_votes + part_comments + part_awards

def pretty_time(utc_ts: int) -> str:
    return datetime.fromtimestamp(utc_ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

def main():
    parser = argparse.ArgumentParser(description="Fetch subreddit posts (last N days) via Reddit API and rank by engagement score.")
    parser.add_argument("--subreddit", "-r", default="soccer", help="Subreddit to query (default: soccer)")
    parser.add_argument("--days", "-d", type=float, default=3.0, help="Number of days in the past to include (default: 3)")
    parser.add_argument("--top", "-n", type=int, default=20, help="How many top posts to print (default: 20)")
    parser.add_argument("--alpha", type=float, default=1.5, help="Weight for score/upvotes (default: 1.5)")
    parser.add_argument("--beta", type=float, default=1.0, help="Weight for comments (default: 1.0)")
    parser.add_argument("--gamma", type=float, default=2.0, help="Weight for awards (default: 2.0)")
    parser.add_argument("--page-size", type=int, default=100, help="Reddit listing page size (max 100).")
    parser.add_argument("--max-pages", type=int, default=50, help="Maximum pages to fetch (safety limit).")
    parser.add_argument("--save-json", type=str, default="", help="If set, save all fetched posts + scores to this JSON file.")
    args = parser.parse_args()

    # env vars
    client_id = os.getenv("REDDIT_CLIENT_ID")
    client_secret = os.getenv("REDDIT_CLIENT_SECRET")
    username = os.getenv("REDDIT_USERNAME")
    password = os.getenv("REDDIT_PASSWORD")
    user_agent = os.getenv("REDDIT_USER_AGENT", "reddit_engagement_script/0.1 by yourusername")

    missing = [name for name, val in [
        ("REDDIT_CLIENT_ID", client_id),
        ("REDDIT_CLIENT_SECRET", client_secret),
        ("REDDIT_USERNAME", username),
        ("REDDIT_PASSWORD", password)
    ] if not val]
    if missing:
        print("ERROR: missing environment variables:", ", ".join(missing), file=sys.stderr)
        print("Please set REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET, REDDIT_USERNAME, REDDIT_PASSWORD", file=sys.stderr)
        return

    now_ts = int(time.time())
    cutoff_ts = now_ts - int(args.days * 86400)

    print(f"Obtaining OAuth token...")
    try:
        token = get_oauth_token(client_id, client_secret, username, password, user_agent)
    except Exception as e:
        print("Failed to get token:", e, file=sys.stderr)
        return

    print(f"Fetching posts from r/{args.subreddit} after {pretty_time(cutoff_ts)} (last {args.days} days)...")
    try:
        posts = fetch_subreddit_new(args.subreddit, token, user_agent, cutoff_ts, page_limit=args.page_size, max_pages=args.max_pages)
    except Exception as e:
        print("Error fetching subreddit posts:", e, file=sys.stderr)
        return

    if not posts:
        print("No posts found in that window (or subreddit has no new posts).")
        return

    # compute engagement score per post
    for p in posts:
        p["_engagement_score"] = compute_engagement(p, args.alpha, args.beta, args.gamma)

    posts.sort(key=lambda x: x["_engagement_score"], reverse=True)

    top_n = min(args.top, len(posts))
    print(f"Found {len(posts)} posts; showing top {top_n} by EngagementScore:\n")
    header = f"{'rank':>4}  {'score':>6}  {'comments':>8}  {'awards':>6}  {'engagement':>11}  {'created (UTC)':>19}  {'id':>8}  title"
    print(header)
    print("-" * len(header))

    for i in range(top_n):
        p = posts[i]
        rank = i + 1
        score = p.get("score", 0)
        comments = p.get("num_comments", 0)
        awards = p.get("total_awards_received", 0)
        eng = p["_engagement_score"]
        created = pretty_time(p.get("created_utc", 0))
        post_id = p.get("id")
        title = (p.get("title") or "").replace("\n", " ")[:120]
        permalink = p.get("permalink") or ""
        print(f"{rank:4d}  {score:6d}  {comments:8d}  {awards:6d}  {eng:11.4f}  {created:19s}  {post_id:8s}  {title}")
        print(f"       https://reddit.com{permalink}")

    if args.save_json:
        out = {
            "meta": {
                "subreddit": args.subreddit,
                "days": args.days,
                "fetched_at": pretty_time(now_ts),
                "alpha": args.alpha,
                "beta": args.beta,
                "gamma": args.gamma
            },
            "posts": posts
        }
        with open(args.save_json, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
        print(f"\nSaved results to {args.save_json}")

if __name__ == "__main__":
    main()
