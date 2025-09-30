#!/usr/bin/env python3
"""
telegram_engagement_telethon_persistent.py

Same as your original script but with persistent sessions:
 - Use TELEGRAM_STRING_SESSION env var to reuse a saved StringSession
 - Or use TELEGRAM_SESSION (or --session) to point to a .session file path
 - Use --login to perform an interactive login once and print a StringSession
   that you can store as TELEGRAM_STRING_SESSION for passwordless runs.

Usage:
  # create a string session (run once)
  python telegram_engagement_telethon_persistent.py --login

  # normal scrape (won't prompt if TELEGRAM_STRING_SESSION or session file exists)
  python telegram_engagement_telethon_persistent.py --channel Sky_sports_football_updates --days 3 --top 20
"""
from __future__ import annotations
import os
import argparse
import math
import asyncio
import json
import telethon
from datetime import datetime, timedelta, timezone
from telethon import TelegramClient
from telethon import errors as tele_errors
from telethon.errors import rpcerrorlist
from telethon.sessions import StringSession

# ---------- Helpers (kept from your original) ----------

def safe_text_from_msg(msg) -> str:
    for attr in ("message", "raw_text", "text"):
        val = getattr(msg, attr, None)
        if isinstance(val, str) and val:
            return val
    return ""


def extract_reactions(msg):
    if not hasattr(msg, "reactions") or not msg.reactions:
        return 0, []

    results = getattr(msg.reactions, "results", None)
    if results is None:
        results = msg.reactions if isinstance(msg.reactions, (list, tuple)) else []

    breakdown = []
    total = 0
    for r in results:
        cnt = None
        emoji = None
        chosen_order = None

        if hasattr(r, "count"):
            cnt = getattr(r, "count")
        elif isinstance(r, dict) and "count" in r:
            cnt = r["count"]

        if hasattr(r, "reaction"):
            react = getattr(r, "reaction")
            emoji = getattr(react, "emoticon", None) or str(react)
        elif isinstance(r, dict) and "reaction" in r:
            react = r["reaction"]
            emoji = react.get("emoticon") if isinstance(react, dict) else str(react)

        if hasattr(r, "chosen_order"):
            chosen_order = getattr(r, "chosen_order")
        elif isinstance(r, dict) and "chosen_order" in r:
            chosen_order = r["chosen_order"]

        try:
            cnt = int(cnt) if cnt is not None else 0
        except Exception:
            cnt = 0

        emoji = emoji or "unknown"
        breakdown.append({"emoji": emoji, "count": cnt, "chosen_order": chosen_order})
        total += cnt

    return total, breakdown


def extract_replies_count(msg) -> int:
    if not hasattr(msg, "replies") or msg.replies is None:
        return 0
    r = msg.replies
    if isinstance(r, int):
        return r
    for attr in ("replies", "replies_count", "replies_num"):
        val = getattr(r, attr, None)
        if isinstance(val, int):
            return val
    try:
        if isinstance(r, dict):
            for key in ("replies", "replies_count"):
                if key in r and isinstance(r[key], int):
                    return r[key]
    except Exception:
        pass
    return 0


def pretty_time(dt_or_iso):
    if dt_or_iso is None:
        return "N/A"
    if isinstance(dt_or_iso, str):
        try:
            dt = datetime.fromisoformat(dt_or_iso)
        except Exception:
            return dt_or_iso
    elif isinstance(dt_or_iso, datetime):
        dt = dt_or_iso
    else:
        return str(dt_or_iso)

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def compute_engagement_telegram(metrics: dict, alpha: float, beta: float, delta: float, gamma: float, award_scale: float = 1.0) -> float:
    v = max(0, int(metrics.get("views") or 0))
    f = max(0, int(metrics.get("forwards") or 0))
    r = max(0, int(metrics.get("replies") or 0))
    rx = max(0, int(metrics.get("reactions_total") or 0))

    part_v = alpha * math.log10(1 + v) if v >= 0 else 0.0
    part_f = beta * math.log10(1 + f)
    part_r = delta * math.log10(1 + r)
    part_rx = gamma * math.log10(1 + rx * award_scale)

    return part_v + part_f + part_r + part_rx


# ---------- Session / login helpers ----------

async def get_client(api_id: int, api_hash: str, session_path: str | None, string_session: str | None, interactive_phone: bool = True):
    """
    Returns a connected & authorized TelegramClient.
    Prefer StringSession if provided, otherwise a file session at session_path.
    If not authorized, will perform interactive sign-in (phone -> code -> optional password).
    """
    if string_session:
        client = TelegramClient(StringSession(string_session), api_id, api_hash)
    else:
        # default session file path if not provided
        if not session_path:
            session_path = os.path.expanduser("~/.telegram_scraper_session")
        client = TelegramClient(session_path, api_id, api_hash)

    await client.connect()

    if await client.is_user_authorized():
        return client

    # not authorized -> interactive sign in
    phone = os.getenv("TELEGRAM_PHONE")
    if not phone and interactive_phone:
        phone = input("Enter your phone number (international format, e.g. +123456789): ").strip()

    if not phone:
        await client.disconnect()
        raise RuntimeError("Phone number required to sign in (set TELEGRAM_PHONE env var or run interactively).")

    try:
        await client.send_code_request(phone)
        code = input("Enter the code you received: ").strip()
        try:
            await client.sign_in(phone=phone, code=code)
        except tele_errors.SessionPasswordNeededError:
            # Two-step enabled
            pw = input("Two-step verification enabled. Enter your password: ").strip()
            await client.sign_in(password=pw)

    except Exception as e:
        await client.disconnect()
        raise

    # now authorized
    return client


# ---------- Main async routine (adapted) ----------

async def scrape_channel(
    api_id: int,
    api_hash: str,
    channel: str,
    days: float,
    top_n: int,
    out_jsonl: str,
    out_json: str,
    page_limit: int,
    alpha: float,
    beta: float,
    delta: float,
    gamma: float,
    award_scale: float,
    session_path: str | None,
    string_session: str | None,
    do_login_and_print_string: bool,
):
    # If user asked only to create a login string session, do that and exit.
      # If user asked only to create a login string session, do that and exit.
    if do_login_and_print_string:
        # Use an in-memory StringSession so we can export the session string
        from telethon.sessions import StringSession
        client = TelegramClient(StringSession(), api_id, api_hash)
        await client.connect()

        # If not authorized, do interactive sign-in
        if not await client.is_user_authorized():
            phone = os.getenv("TELEGRAM_PHONE")
            if not phone:
                phone = input("Enter your phone number (international format, e.g. +123456789): ").strip()
            if not phone:
                await client.disconnect()
                raise RuntimeError("Phone number required to sign in (set TELEGRAM_PHONE env var or run interactively).")

            await client.send_code_request(phone)
            code = input("Enter the code you received: ").strip()
            try:
                await client.sign_in(phone=phone, code=code)
            except telethon.errors.SessionPasswordNeededError:
                pw = input("Two-step verification enabled. Enter your password: ").strip()
                await client.sign_in(password=pw)

        try:
            # Export a string session that you can store in TELEGRAM_STRING_SESSION
            ss = client.session.save()
            if not ss:
                print("Warning: could not produce a StringSession value (session.save() returned None).")
            print("\n== StringSession value (save this as TELEGRAM_STRING_SESSION) ==\n")
            print(ss)
            if ss:
                print("\nYou can export it in bash like:")
                print('  export TELEGRAM_STRING_SESSION="{}"'.format(ss))
            else:
                print("\nNo StringSession produced. If you have an existing .session file, delete or move it and retry --login.")
        finally:
            await client.disconnect()
        return

    # Normal scraping flow
    client = await get_client(api_id, api_hash, session_path, string_session, interactive_phone=False)
    # client is connected & authorized
    since = datetime.now(timezone.utc) - timedelta(days=days)

    posts = []
    count = 0
    try:
        async for msg in client.iter_messages(channel, limit=None):
            if not getattr(msg, "date", None):
                continue

            # get msg date as timezone-aware UTC
            try:
                msg_date_utc = msg.date.astimezone(timezone.utc) if getattr(msg, "date", None) else None
            except Exception:
                # fallback: treat as naive UTC
                msg_date_utc = msg.date.replace(tzinfo=timezone.utc) if getattr(msg, "date", None) else None

            if msg_date_utc is None:
                continue
            if msg_date_utc < since:
                break

            count += 1
            text = safe_text_from_msg(msg)
            views = getattr(msg, "views", None)
            forwards = getattr(msg, "forwards", None)
            replies = extract_replies_count(msg)
            reactions_total, reactions_breakdown = extract_reactions(msg)

            entry = {
                "id": getattr(msg, "id", None),
                "date": msg_date_utc.isoformat() if msg_date_utc else None,
                "text": text,
                "views": views,
                "forwards": forwards,
                "replies": replies,
                "reactions_total": reactions_total,
                "reactions_breakdown": reactions_breakdown,
            }

            posts.append(entry)

            if out_jsonl:
                with open(out_jsonl, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(entry, ensure_ascii=False) + "\n")

    except rpcerrorlist.BotMethodInvalidError as e:
        print("\nERROR: Bot API is restricted for this operation.")
        print("You are likely authenticated as a bot (bot token). Bots cannot fetch channel history.")
        print("Use a user session with API_ID and API_HASH (create at https://my.telegram.org/apps) and login once.")
        print("\nDetailed error from Telethon:", e)
        await client.disconnect()
        return
    except KeyboardInterrupt:
        print("\nInterrupted by user. Stopping.")
    except Exception as e:
        print("Unexpected error while iterating messages:", repr(e))
        await client.disconnect()
        return

    # compute engagement and sort
    for p in posts:
        p["_engagement_score"] = compute_engagement_telegram(
            p, alpha=alpha, beta=beta, delta=delta, gamma=gamma, award_scale=award_scale
        )

    posts.sort(key=lambda x: x["_engagement_score"], reverse=True)

    top_n = min(top_n, len(posts))
    now_ts = datetime.now(timezone.utc)
    print(f"Scraped {count} messages in the last {days} days (since {since.isoformat()} UTC). Showing top {top_n} by EngagementScore:\n")
    header = f"{'rank':>4}  {'views':>8}  {'forwards':>8}  {'replies':>7}  {'reactions':>9}  {'engagement':>11}  {'date (UTC)':>19}  {'id':>6}  text"
    print(header)
    print("-" * len(header))
    for i in range(top_n):
        p = posts[i]
        rank = i + 1
        views = p.get("views") or 0
        forwards = p.get("forwards") or 0
        replies = p.get("replies") or 0
        reactions = p.get("reactions_total") or 0
        eng = p["_engagement_score"]
        created = pretty_time(p.get("date"))
        pid = str(p.get("id") or "")
        text_line = (p.get("text") or "").replace("\n", " ")[:120]
        print(f"{rank:4d}  {views:8d}  {forwards:8d}  {replies:7d}  {reactions:9d}  {eng:11.4f}  {created:19s}  {pid:6s}  {text_line}")

    if out_json:
        agg = {
            "meta": {
                "channel": channel,
                "days": days,
                "scraped_at": now_ts.isoformat(),
                "alpha": alpha,
                "beta": beta,
                "delta": delta,
                "gamma": gamma,
            },
            "posts": posts,
        }
        with open(out_json, "w", encoding="utf-8") as fh:
            json.dump(agg, fh, ensure_ascii=False, indent=2)
        print(f"\nSaved aggregated JSON to {out_json}")

    await client.disconnect()


# ---------- CLI ----------

def main():
    parser = argparse.ArgumentParser(description="Scrape Telegram channel messages and rank by engagement.")
    parser.add_argument("--channel", "-c", required=True, help="Channel username or invite link (e.g. Sky_sports_football_updates or https://t.me/xxx)")
    parser.add_argument("--days", "-d", type=float, default=3.0, help="Number of days in the past to include (default: 3)")
    parser.add_argument("--top", "-n", type=int, default=20, help="How many top messages to print (default: 20)")
    parser.add_argument("--out-jsonl", type=str, default="telegram_with_reactions.jsonl", help="Append messages to this JSONL file.")
    parser.add_argument("--out-json", type=str, default="", help="If set, save aggregated posts+scores to this JSON file.")
    parser.add_argument("--api-id", type=int, default=None, help="Telegram API ID (or use TELEGRAM_API_ID env var)")
    parser.add_argument("--api-hash", type=str, default=None, help="Telegram API HASH (or use TELEGRAM_API_HASH env var)")
    parser.add_argument("--page-limit", type=int, default=100, help="(unused) placeholder for parity with reddit script")
    parser.add_argument("--alpha", type=float, default=1.5, help="Weight for views (default: 1.5)")
    parser.add_argument("--beta", type=float, default=1.0, help="Weight for forwards (default: 1.0)")
    parser.add_argument("--delta", type=float, default=1.0, help="Weight for replies (default: 1.0)")
    parser.add_argument("--gamma", type=float, default=2.0, help="Weight for reactions (default: 2.0)")
    parser.add_argument("--award-scale", type=float, default=1.0, help="Scale factor for reactions (default: 1.0)")
    parser.add_argument("--session", type=str, default=None, help="Path to session file (or set TELEGRAM_SESSION env var)")
    parser.add_argument("--login", action="store_true", help="Perform interactive login and print a StringSession (run once and save TELEGRAM_STRING_SESSION).")
    args = parser.parse_args()

    api_id = args.api_id or (os.getenv("TELEGRAM_API_ID") and int(os.getenv("TELEGRAM_API_ID"))) or None
    api_hash = args.api_hash or os.getenv("TELEGRAM_API_HASH") or None

    if not api_id or not api_hash:
        print("ERROR: TELEGRAM API credentials missing. Set --api-id and --api-hash or TELEGRAM_API_ID and TELEGRAM_API_HASH env vars.")
        return

    # priority: TELEGRAM_STRING_SESSION env var -> pass to client as StringSession
    string_session = os.getenv("TELEGRAM_STRING_SESSION") or None
    session_path = args.session or os.getenv("TELEGRAM_SESSION") or None

    asyncio.run(
        scrape_channel(
            api_id=api_id,
            api_hash=api_hash,
            channel=args.channel,
            days=args.days,
            top_n=args.top,
            out_jsonl=args.out_jsonl,
            out_json=args.out_json,
            page_limit=args.page_limit,
            alpha=args.alpha,
            beta=args.beta,
            delta=args.delta,
            gamma=args.gamma,
            award_scale=args.award_scale,
            session_path=session_path,
            string_session=string_session,
            do_login_and_print_string=args.login,
        )
    )


if __name__ == "__main__":
    main()
