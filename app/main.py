from __future__ import annotations
from io import BytesIO
import os
import asyncio
from typing import Optional, List, Dict, Any
from fastapi import FastAPI, File, Form, HTTPException, Response, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel
from dotenv import load_dotenv
import json

# Load env at startup from absolute path
load_dotenv("/home/xaje/Documents/contentWork/cred.env")

# Import existing modules
from imageAPIscrapers.meme_imgflip import scrape_meme
from imageGeneration.editImage import generate_image
from socialapiscrapers.scrape_reddit import (
    get_oauth_token,
    fetch_subreddit_new,
    compute_engagement,
)
from socialapiscrapers.scrapeTelegramChannel import scrape_channel as tg_scrape_channel
from socialapiscrapers.scrapeInstagramPage import (
    login_with_prompt,
    fetch_medias_since,
    compute_engagement_from_metrics,
)
from footballapiscapers.league import scrape_league
from footballapiscapers.match import scrape_match
from footballapiscapers.player import scrape_player
from imageAPIscrapers.gettyimage import scrape_image

app = FastAPI(title="ContentWork API", version="0.1.0")


@app.get("/health")
def health():
    return {"ok": True}


class RedditRequest(BaseModel):
    subreddit: str
    days: float = 3.0
    top: int = 20
    alpha: float = 1.5
    beta: float = 1.0
    gamma: float = 2.0
    page_size: int = 100
    max_pages: int = 30


@app.post("/reddit")
def reddit_top(req: RedditRequest):
    client_id = os.getenv("REDDIT_CLIENT_ID")
    client_secret = os.getenv("REDDIT_CLIENT_SECRET")
    username = os.getenv("REDDIT_USERNAME")
    password = os.getenv("REDDIT_PASSWORD")
    user_agent = os.getenv("REDDIT_USER_AGENT", "contentwork/0.1 by api")

    missing = [name for name, val in [
        ("REDDIT_CLIENT_ID", client_id),
        ("REDDIT_CLIENT_SECRET", client_secret),
        ("REDDIT_USERNAME", username),
        ("REDDIT_PASSWORD", password),
    ] if not val]
    if missing:
        raise HTTPException(status_code=500, detail=f"Missing env: {', '.join(missing)}")

    token = get_oauth_token(client_id, client_secret, username, password, user_agent)

    import time as _t
    cutoff_ts = int(_t.time() - req.days * 86400)
    posts = fetch_subreddit_new(req.subreddit, token, user_agent, cutoff_ts, page_limit=req.page_size, max_pages=req.max_pages)
    for p in posts:
        p["_engagement_score"] = compute_engagement(p, req.alpha, req.beta, req.gamma)
    posts.sort(key=lambda x: x.get("_engagement_score", 0.0), reverse=True)
    return {"subreddit": req.subreddit, "top": posts[: req.top], "count": len(posts)}


class TelegramRequest(BaseModel):
    channel: str
    days: float = 3.0
    top: int = 20
    out_json: str = ""


@app.post("/telegram")
async def telegram_top(req: TelegramRequest):
    api_id_env = os.getenv("TELEGRAM_API_ID")
    api_hash = os.getenv("TELEGRAM_API_HASH")
    if not api_id_env or not api_hash:
        raise HTTPException(status_code=500, detail="Missing TELEGRAM_API_ID / TELEGRAM_API_HASH in env")
    api_id = int(api_id_env)

    # Run scraper and get posts data
    posts = await tg_scrape_channel(
        api_id=api_id,
        api_hash=api_hash,
        channel=req.channel,
        days=req.days,
        top_n=req.top,
        out_jsonl="",  # do not write jsonl in API mode
        out_json=req.out_json if req.out_json else "",
        page_limit=100,
        alpha=1.5,
        beta=1.0,
        delta=1.0,
        gamma=2.0,
        award_scale=1.0,
        session_path=os.getenv("TELEGRAM_SESSION"),
        string_session=os.getenv("TELEGRAM_STRING_SESSION"),
        do_login_and_print_string=False,
    )
    
    # Return the actual posts data
    return {
        "channel": req.channel, 
        "top": posts[:req.top] if posts else [], 
        "count": len(posts) if posts else 0,
        "saved": bool(req.out_json)
    }


class InstagramRequest(BaseModel):
    target: str
    days: float = 3.0
    top: int = 20
    exclude_videos: bool = False
    max_fetch: int = 400
    alpha: float = 1.5
    beta: float = 1.0
    gamma: float = 0.5
    delta: float = 2.0


@app.post("/instagram")
def instagram_top(req: InstagramRequest):
    # login_with_prompt will prefer INSTAGRAM_SESSIONID or saved settings and should not prompt in API mode
    try:
        cl = login_with_prompt()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Instagram login failed: {e}")

    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=req.days)

    medias = fetch_medias_since(cl, req.target, cutoff, max_fetch=req.max_fetch)

    items: List[Dict[str, Any]] = []
    for m in medias:
        media_pk = getattr(m, "pk", None) or getattr(m, "id", None)
        likes = int(getattr(m, "like_count", 0) or 0)
        comments = int(getattr(m, "comment_count", 0) or 0)
        views = 0
        saves = 0

        if req.exclude_videos:
            try:
                # import here to avoid circular import
                from socialapiscrapers.scrapeInstagramPage import media_is_video
                if media_is_video(m):
                    continue
            except Exception:
                pass

        for attr in ("view_count", "video_view_count", "video_views", "viewCount"):
            val = getattr(m, attr, None)
            if isinstance(val, (int, float)):
                views = int(val)
                break

        if media_pk:
            from socialapiscrapers.scrapeInstagramPage import fetch_insights_safe
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

        score = compute_engagement_from_metrics(likes, comments, views, saves, req.alpha, req.beta, req.gamma, req.delta)
        items.append({
            "pk": media_pk,
            "code": getattr(m, "code", None) or "",
            "likes": likes,
            "comments": comments,
            "views": views,
            "saves": saves,
            "score": float(score),
        })

    items.sort(key=lambda x: x["score"], reverse=True)
    return {"target": req.target, "top": items[: req.top], "count": len(items)}


class FotmobLeagueRequest(BaseModel):
    query: str
    save_json: Optional[str] = None


@app.post("/football/league")
def football_league(req: FotmobLeagueRequest):
    data = scrape_league(league_search_query=req.query, chromedriver_path="/home/xaje/Documents/contentWork/footballapiscapers/chromedriver", save_json_path=req.save_json)
    return data


class FotmobMatchRequest(BaseModel):
    query: str
    save_json: Optional[str] = None


@app.post("/football/match")
def football_match(req: FotmobMatchRequest):
    data = scrape_match(search_query=req.query, chromedriver_path="/home/xaje/Documents/contentWork/footballapiscapers/chromedriver", save_json_path=req.save_json)
    return data


class FotmobPlayerRequest(BaseModel):
    query: str
    save_json: Optional[str] = None


@app.post("/football/player")
def football_player(req: FotmobPlayerRequest):
    data = scrape_player(player_search_query=req.query, chromedriver_path="/home/xaje/Documents/contentWork/footballapiscapers/chromedriver", save_json_path=req.save_json)
    return data


class ImageRequest(BaseModel):
    query: str

@app.post("/images/")
def football_player(req: ImageRequest):
    try:
        result = scrape_image(
            player_search_query=req.query,
            chromedriver_path="/home/xaje/Documents/contentWork/footballapiscapers/chromedriver"
        )
        return JSONResponse(content=json.loads(result))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    

@app.post("/grabMeme/")
async def grab_meme_endpoint(req: ImageRequest):
    try:
        result = scrape_meme(
            memeQuery=req.query,
            chromedriver_path="/home/xaje/Documents/contentWork/footballapiscapers/chromedriver"
        )
        return JSONResponse(content=json.loads(result))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    

class GenerateImageRequest(BaseModel):
    query: str
    promptImageURL: str
    image_url: str

@app.post("/generateImage/")
async def generate_image_endpoint(req: GenerateImageRequest):
    try:
        result = await asyncio.to_thread(generate_image, req.query, req.promptImageURL, req.image_url)

        if result.get("type") == "image":
            buf = BytesIO(result["bytes"])
            buf.seek(0)
            return StreamingResponse(buf, media_type=result.get("mime", "image/png"))

        if result.get("type") == "text":
            return JSONResponse({"query": req.query, "text": result["text"]})

        return JSONResponse({"query": req.query, "result": result})

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))