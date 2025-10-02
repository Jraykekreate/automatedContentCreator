# ContentWork API

Run the API locally:

```bash
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Ensure `cred.env` is present at `/home/xaje/Documents/contentWork/cred.env` with required credentials. The app auto-loads it at startup.

Endpoints:
- `GET /health` — health check
- `POST /reddit` — body: `{ "subreddit": "soccer", "days": 3, "top": 20 }`
- `POST /telegram` — body: `{ "channel": "Sky_sports_football_updates", "days": 3, "top": 20 }`
- `POST /instagram` — body: `{ "target": "skysportsfootball", "days": 3, "top": 20 }`
- `POST /football/league` — body: `{ "query": "premier league", "save_json": null }`
- `POST /football/match` — body: `{ "query": "chelsea vs benfica" }`
- `POST /football/player` — body: `{ "query": "joao pedro" }`

Notes:
- The Telegram endpoint requires `TELEGRAM_API_ID` and `TELEGRAM_API_HASH` (and optionally `TELEGRAM_STRING_SESSION` or a `TELEGRAM_SESSION` file path) in `cred.env`.
-The reddit endpoint requires a `REDDIT_CLIENT_ID`  `REDDIT_CLIENT_SECRET` and `REDDIT_USERNAME` and `REDDIT_PASSWORD` 
-In the workflow I used groq but you can replace it with whatever chatbot api you prefer i also used openrouter as well to help me communicate with Nano banana
- Instagram endpoint prefers `INSTAGRAM_SESSIONID` or saved `socialapiscrapers/settings.json` to avoid interactive prompts.
- Football endpoints use the bundled ChromeDriver at `footballapiscapers/chromedriver` and run Chrome headless.

