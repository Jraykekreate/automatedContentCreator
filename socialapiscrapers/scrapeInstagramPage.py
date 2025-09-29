#!/usr/bin/env python3
"""
scrapeInstagram.py

Robust Instagram post ranking script using instagrapi.

Features:
 - login with INSTAGRAM_SESSIONID or INSTAGRAM_USERNAME + INSTAGRAM_PASSWORD
 - SMS challenge prompt (manual entry)
 - saves/loads settings.json to reuse session and avoid challenges
 - uses private/mobile API (user_medias_paginated_v1) with robust pagination
 - computes EngagementScore from likes/comments/views/saves (log formula)
 - prints top-N posts and optionally saves JSON

Usage:
    export INSTAGRAM_USERNAME="you"
    export INSTAGRAM_PASSWORD="pass"
    python scrapeInstagram.py --target skysportsfootball --days 3 --top 20

Preferred: export INSTAGRAM_SESSIONID from your browser to skip login entirely:
    export INSTAGRAM_SESSIONID="paste_sessionid_here"
"""
from __future__ import annotations
import os
import sys
import time
import math
import json
import argparse
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Optional, Tuple

try:
    from instagrapi import Client
    from instagrapi.mixins.challenge import ChallengeChoice
except Exception as e:
    print("ERROR: instagrapi not installed. Run: pip install instagrapi", file=sys.stderr)
    raise

SETTINGS_FILE = "settings.json"

# -------------------------
# Settings/session helpers
# -------------------------
def dump_settings_safe(cl: Client, path: str = SETTINGS_FILE) -> None:
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cl.get_settings(), f, ensure_ascii=False, indent=2)
        print(f"[+] Saved session/settings -> {path}")
    except Exception as e:
        print("[!] Warning: failed to save settings:", e)

def load_settings_safe(cl: Client, path: str = SETTINGS_FILE) -> bool:
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                cl.set_settings(json.load(f))
            print(f"[+] Loaded settings from {path}")
            return True
    except Exception as e:
        print("[!] Warning: failed to load settings:", e)
    return False

# -------------------------
# SMS challenge handler
# -------------------------
def sms_challenge_handler(username: str, choice: ChallengeChoice):
    """
    Called by instagrapi when Instagram requires a verification code.
    For SMS/email challenges this will prompt you to paste the 6-digit code from your phone or email.
    Return the code string (or False to abort).
    """
    print(f"[challenge] Instagram requires a verification code for {username}. Method: {choice}")
    try:
        code = input("Enter the 6-digit SMS / email code you received: ").strip()
        if not code:
            print("[!] No code entered — aborting challenge.")
            return False
        return code
    except Exception as e:
        print("[!] Error reading code from input:", e)
        return False
    
def media_is_video(m: Any) -> bool:
    """
    Robust check whether a media object is a video (or a carousel containing only videos).
    Tries a number of attributes that instagrapi media objects may expose.
    """
    # direct flags / attributes commonly present
    try:
        if getattr(m, "is_video", False):
            return True
        if getattr(m, "media_type", None) == 2:  # common numeric enum: 2 == video
            return True
        if getattr(m, "video_url", None):
            return True
        mtname = getattr(m, "media_type_name", None) or ""
        if isinstance(mtname, str) and "video" in mtname.lower():
            return True
    except Exception:
        pass

    # handle carousel / album: if album has only video items -> treat as video
    children = getattr(m, "carousel_media", None) or getattr(m, "resources", None) or []
    if children:
        has_image = False
        has_video = False
        for c in children:
            try:
                if getattr(c, "is_video", False) or getattr(c, "media_type", None) == 2 or getattr(c, "video_url", None):
                    has_video = True
                else:
                    has_image = True
            except Exception:
                # conservative fallback: consider child non-video if we can't tell
                has_image = True
        if has_video and not has_image:
            return True
        # if mixed, prefer including (treat as not video)
        return False

    return False


# -------------------------
# Login flow (reuse settings/sessionid + SMS prompt)
# -------------------------
def login_with_prompt(settings_file: str = SETTINGS_FILE) -> Client:
    cl = Client()
    # gentle defaults
    try:
        cl.delay_range = [1, 2]
    except Exception:
        pass

    # attach sms challenge handler
    cl.challenge_code_handler = sms_challenge_handler

    # 1) try to load saved settings.json
    loaded = load_settings_safe(cl, settings_file)
    if loaded:
        try:
            # cheaply validate session by requesting self info (if username is stored)
            if cl.username:
                _ = cl.user_info_by_username_v1(cl.username)
                print("[+] Reused loaded session successfully.")
                return cl
        except Exception:
            print("[*] Saved session invalid/expired; will attempt fresh login.")

    # 2) try sessionid env var
    sessionid = os.getenv("INSTAGRAM_SESSIONID")
    if sessionid:
        try:
            cl.set_settings({"sessionid": sessionid})
            cl.login_by_sessionid(sessionid)
            print("[+] Logged in via INSTAGRAM_SESSIONID")
            dump_settings_safe(cl, settings_file)
            return cl
        except Exception as e:
            print("[!] sessionid login failed — falling back to username/password:", e)

    # 3) full login with username/password (may trigger challenge)
    username = os.getenv("INSTAGRAM_USERNAME")
    password = os.getenv("INSTAGRAM_PASSWORD")
    if not (username and password):
        raise RuntimeError("Provide INSTAGRAM_SESSIONID or INSTAGRAM_USERNAME + INSTAGRAM_PASSWORD in env")

    print("[*] Logging in using username/password — be ready to enter SMS/code if prompted.")
    try:
        cl.login(username, password)
        print("[+] Login successful (username/password).")
        dump_settings_safe(cl, settings_file)
        return cl
    except Exception as exc:
        # show helpful error and re-raise
        print("[!] Login error:", repr(exc))
        raise

# -------------------------
# Fetching medias: robust paginated fetch
# -------------------------
def fetch_medias_since(cl: Client, target_username: str, cutoff_dt: datetime,
                       max_fetch: int = 1000, page_sleep: float = 0.6) -> List[Any]:
    """
    Robust media fetcher using the private/mobile API with:
      - page-sized requests (amount=50)
      - stops when an item older than cutoff_dt is encountered (Instagram returns newest-to-oldest)
      - retries with exponential backoff on transient errors
      - max page limit and detection of stuck end_cursor
    """
    user_info = cl.user_info_by_username_v1(target_username)
    user_id = user_info.pk

    fetched: List[Any] = []
    end_cursor: str = ""
    prev_end_cursor: Optional[str] = None
    max_pages = 200  # safety cap to avoid infinite loops
    page_count = 0

    def _api_page_fetch(uid: int, cursor: str) -> Tuple[List[Any], str]:
        # wrapper to call user_medias_paginated_v1 with retries
        retries = 4
        base_backoff = 0.6
        for attempt in range(1, retries + 1):
            try:
                page, new_cursor = cl.user_medias_paginated_v1(uid, amount=50, end_cursor=cursor)
                return page or [], new_cursor or ""
            except Exception as e:
                print(f"[!] Warning: page fetch error (attempt {attempt}/{retries}): {e}")
                if attempt == retries:
                    raise
                time.sleep(base_backoff * attempt)
        return [], ""

    stop_all = False
    while not stop_all and page_count < max_pages and len(fetched) < max_fetch:
        page_count += 1
        try:
            page, new_end_cursor = _api_page_fetch(user_id, end_cursor)
        except Exception as e:
            print("[!] Fatal: failed to fetch page after retries:", e)
            break

        # progress print so you can see where it might hang
        short_cursor = (new_end_cursor[:12] + "...") if new_end_cursor else "<empty>"
        print(f"[*] Fetched page {page_count}: {len(page)} items (cursor={short_cursor})")

        if not page:
            # no more content
            break

        for m in page:
            taken_at = getattr(m, "taken_at", None)
            if taken_at and taken_at.tzinfo is None:
                taken_at = taken_at.replace(tzinfo=timezone.utc)
            # if we have a timestamp and it's older than cutoff, we can stop fetching further pages
            if taken_at and taken_at < cutoff_dt:
                stop_all = True
                break
            fetched.append(m)
            if len(fetched) >= max_fetch:
                stop_all = True
                break

        # safety: if the cursor didn't change, break to avoid infinite loop
        if not new_end_cursor or new_end_cursor == prev_end_cursor:
            print("[*] Cursor unchanged or empty — stopping pagination.")
            break
        prev_end_cursor = end_cursor
        end_cursor = new_end_cursor

        # polite pause between pages
        time.sleep(page_sleep)

    print(f"[*] Completed fetch: pages={page_count}, items_fetched={len(fetched)}")
    return fetched

# -------------------------
# Insights helper & scoring
# -------------------------
def fetch_insights_safe(cl: Client, media_pk: int) -> Dict[str, Any]:
    try:
        ins = cl.insights_media(media_pk)
        if isinstance(ins, dict):
            return ins
        return {}
    except Exception:
        return {}

def compute_engagement_from_metrics(likes: int, comments: int, views: int, saves: int,
                                    alpha: float, beta: float, gamma: float, delta: float) -> float:
    def L(x: int) -> float:
        x = max(0, int(x or 0))
        # digit-by-digit: safe small-number log
        return math.log10(1 + x)
    return alpha * L(likes) + beta * L(comments) + gamma * L(views) + delta * L(saves)

def pretty_dt(dt: Optional[datetime]) -> str:
    if not dt:
        return "unknown"
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

# -------------------------
# CLI + main flow
# -------------------------
def main():
    p = argparse.ArgumentParser(description="Rank Instagram posts for a user (last N days) by EngagementScore.")
    p.add_argument("--target", "-t", required=True, help="Target Instagram username (e.g. skysportsfootball)")
    p.add_argument("--days", "-d", type=float, default=3.0, help="Days in the past to include (default 3)")
    p.add_argument("--top", "-n", type=int, default=20, help="Number of top posts to display (default 20)")
    p.add_argument("--alpha", type=float, default=1.5, help="weight for likes")
    p.add_argument("--beta", type=float, default=1.0, help="weight for comments")
    p.add_argument("--gamma", type=float, default=0.5, help="weight for views")
    p.add_argument("--delta", type=float, default=2.0, help="weight for saves/bookmarks")
    p.add_argument("--max-fetch", type=int, default=800, help="Max number of recent posts to fetch (default 800)")
    p.add_argument("--save-json", type=str, default="", help="If set, save results to this JSON file")
    p.add_argument("--exclude-videos", action="store_true", help="Exclude video posts (and carousels that are only video) from results")
    args = p.parse_args()

    # login
    try:
        cl = login_with_prompt()
    except Exception as e:
        print("[!] Could not login:", e)
        sys.exit(1)

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=args.days)
    print(f"[*] Fetching up to {args.max_fetch} medias for @{args.target} since {cutoff.isoformat()} ...")

    try:
        medias = fetch_medias_since(cl, args.target, cutoff, max_fetch=args.max_fetch)
    except Exception as e:
        print("[!] Error fetching medias:", e)
        sys.exit(1)

    if not medias:
        print("[*] No medias found in that window.")
        return

    scored: List[Dict[str, Any]] = []
    for m in medias:
        media_pk = getattr(m, "pk", None) or getattr(m, "id", None)
        likes = int(getattr(m, "like_count", 0) or 0)
        comments = int(getattr(m, "comment_count", 0) or 0)
        views = 0
        saves = 0
                # Optional: skip videos when requested
        if getattr(args, "exclude_videos", False):
            try:
                if media_is_video(m):
                    # uncomment the next line to see which posts were skipped
                    # print(f"[-] Skipping video post {getattr(m,'pk',getattr(m,'id',None))}")
                    continue
            except Exception:
                # if detection fails for some media, play safe and skip only if sure it's a video
                pass


        # try fields on media object first
        for attr in ("view_count", "video_view_count", "video_views", "viewCount"):
            val = getattr(m, attr, None)
            if isinstance(val, (int, float)):
                views = int(val)
                break

        # then try insights fallback (may return view/save counts)
        if media_pk:
            ins = fetch_insights_safe(cl, int(media_pk))
            if isinstance(ins, dict):
                for k in ("video_view_count", "video_views", "view_count", "views"):
                    if k in ins and isinstance(ins[k], (int, float)):
                        views = int(ins[k])
                        break
                for k in ("save_count", "saves", "save"):
                    if k in ins and isinstance(ins[k], (int, float)):
                        saves = int(ins[k])
                        break

        score = compute_engagement_from_metrics(
            likes=likes, comments=comments, views=views, saves=saves,
            alpha=args.alpha, beta=args.beta, gamma=args.gamma, delta=args.delta
        )
        url = getattr(m, "thumbnail_url", None) or getattr(m, "url", None) or f"https://www.instagram.com/p/{getattr(m,'code','')}/"
        scored.append({
            "pk": media_pk,
            "code": getattr(m, "code", None) or "",
            "url": url,
            "taken_at": getattr(m, "taken_at", None),
            "likes": likes,
            "comments": comments,
            "views": views,
            "saves": saves,
            "_score": float(score),
        })
        time.sleep(0.03)  # tiny polite pause

    scored.sort(key=lambda x: x["_score"], reverse=True)
    top_n = min(len(scored), args.top)
    print(f"Found {len(scored)} posts; showing top {top_n} by EngagementScore:\n")
    header = f"{'rank':>4}  {'likes':>6}  {'comments':>8}  {'views':>7}  {'saves':>6}  {'engagement':>12}  {'taken_at':>20}  {'code':>12}  url"
    print(header)
    print("-" * len(header))
    for i in range(top_n):
        it = scored[i]
        print(f"{i+1:4d}  {it['likes']:6d}  {it['comments']:8d}  {it['views']:7d}  {it['saves']:6d}  {it['_score']:12.4f}  {pretty_dt(it['taken_at']):20s}  {str(it['code']):12s}  {it['url']}")

    if args.save_json:
        out = {
            "meta": {
                "target": args.target,
                "days": args.days,
                "fetched_at": now.isoformat(),
                "alpha": args.alpha, "beta": args.beta, "gamma": args.gamma, "delta": args.delta
            },
            "posts": scored
        }
        with open(args.save_json, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
        print(f"[+] Saved results to {args.save_json}")

if __name__ == "__main__":
    main()
