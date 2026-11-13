# Media Downloader Telegram Bot

Telegram bot that downloads **video** and **MP3** from **Instagram** (posts / Reels),
**YouTube** (videos / Shorts, 360p · 720p · 1080p) and **TikTok** (no watermark when
the platform offers a clean stream), and finds music by name
(`Shahzoda - Yetmadi` → top 5 results → MP3 with title / performer / cover).

Interface language: Uzbek (Latin, default), Russian, English.

Stack: Python 3.12 · aiogram 3 · yt-dlp · ffmpeg · SQLAlchemy 2 (async, SQLite) ·
pydantic-settings · Docker.

---

## Architecture

```
Telegram ──► aiogram Dispatcher
              │  outer middlewares (per message / callback):
              │    UserMiddleware     – upsert user, inject lang
              │    ForceSubMiddleware – optional channel gate
              ▼
   handlers/start     /start /help /lang, language buttons, "check subscription"
   handlers/admin     /stats /broadcast /update_ytdlp   (ADMIN_IDS only)
   handlers/download  link detection → [🎬 Video][🎵 MP3] → process_download()
   handlers/music     plain text → ytsearch5 → 5 inline buttons → MP3
              │
              ▼  process_download():
   RateLimiter ─► FileIdCache (SQLite) ─hit─► send by file_id (instant)
                        │ miss
                        ▼
   Downloader (yt-dlp in a thread, global Semaphore)
        │  video: format selector per platform + size filter
        │  audio: bitrate chosen so the MP3 fits the upload cap
        ▼
   services/ffmpeg: probe · compress (if > limit) · 320px thumbnail
        ▼
   send_video / send_audio ─► store file_id in cache ─► delete temp dir
```

* **Nothing blocks the event loop**: yt-dlp runs via `asyncio.to_thread`, ffmpeg via
  `asyncio.create_subprocess_exec`, SQLite via `aiosqlite`.
* **Upload cap**: 50 MB on the cloud Bot API. Oversized videos are re-encoded with
  ffmpeg to a bitrate that fits (lowering resolution if needed). If even that is
  impossible the user is offered MP3 / lower quality. With `USE_LOCAL_BOT_API=true`
  the cap becomes 2 GB.
* **Cache**: `(normalized_url, format)` → Telegram `file_id`. Share links with
  tracking params (`?si=`, `?igsh=`, `vm.tiktok.com/...`) normalize to one key.
* **Callback data** is limited to 64 bytes, so URLs / search results are kept in a
  short-lived in-memory token store (1 h TTL). After a restart old buttons answer
  "link expired, resend".
* **Errors**: yt-dlp messages are classified (`private`, `removed`, `geo_blocked`,
  `age_restricted`, `login_required`, `live`, `too_large`, `too_long`,
  `unsupported`, `timeout`) → short friendly message to the user, full detail in logs.

### Project layout

```
bot/
  main.py            entry point, wiring, polling
  config.py          pydantic-settings (reads .env)
  context.py         AppContext passed to handlers as `ctx`
  keyboards.py       inline keyboards
  handlers/          start.py download.py music.py admin.py
  middlewares/       user.py forcesub.py
  services/          downloader.py ffmpeg.py cache.py cleanup.py
  db/                models.py session.py repo.py
  locales/           uz.json ru.json en.json
  utils/             regex.py ratelimit.py linkstore.py i18n.py text.py
tests/               test_regex.py test_downloader.py (yt-dlp mocked) test_utils.py
scripts/             entrypoint.sh update_ytdlp.sh update_ytdlp.ps1
Dockerfile · docker-compose.yml · requirements.txt · .env.example
```

---

## 1. Get a bot token

1. Open [@BotFather](https://t.me/BotFather) in Telegram → `/newbot`.
2. Choose a display name and a username ending in `bot`.
3. Copy the token (`123456789:AA...`) into `.env` as `BOT_TOKEN`.
4. Find your own numeric ID with [@userinfobot](https://t.me/userinfobot) and put it
   in `ADMIN_IDS`.

Never commit `.env`. The token is read only from the environment.

## 2. Run locally

Requirements: Python 3.12, **ffmpeg + ffprobe in PATH**
(Windows: `winget install Gyan.FFmpeg`, macOS: `brew install ffmpeg`,
Debian/Ubuntu: `sudo apt install ffmpeg`).

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate      Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env         # then edit BOT_TOKEN and ADMIN_IDS
python -m bot.main
```

Run tests (no network, no ffmpeg needed):

```bash
pytest
```

## 3. Run with Docker

```bash
cp .env.example .env         # edit BOT_TOKEN, ADMIN_IDS
docker compose up -d --build
docker compose logs -f bot
```

`data/` (SQLite) and `downloads/` (temp files) are bind-mounted so they survive
rebuilds. `AUTO_UPDATE_YTDLP=true` (default in `.env.example`) upgrades yt-dlp on
every container start.

### Optional: local Bot API server (files up to 2 GB)

1. Get `api_id` / `api_hash` at <https://my.telegram.org> → *API development tools*.
2. In `.env`:
   ```
   USE_LOCAL_BOT_API=true
   TELEGRAM_API_ID=12345
   TELEGRAM_API_HASH=abcdef...
   ```
3. If the bot ever ran against the cloud API, log it out once so the local server
   can take over (replace the token):
   ```bash
   curl https://api.telegram.org/bot<BOT_TOKEN>/logOut
   ```
4. Start both services:
   ```bash
   docker compose --profile local-api up -d --build
   ```

## 4. Deploy

### VPS (Ubuntu / Debian)

```bash
sudo apt update && sudo apt install -y docker.io docker-compose-v2 git
git clone <your-repo> media-bot && cd media-bot
cp .env.example .env && nano .env
docker compose up -d --build
```

Updates: `git pull && docker compose up -d --build`.
Logs: `docker compose logs -f --tail 200 bot`.

### Render

The bot uses long polling, so deploy it as a **Background Worker**, not a Web Service.

1. Push the repo to GitHub.
2. Render → *New* → *Background Worker* → connect the repo → Runtime: **Docker**.
3. Add environment variables (`BOT_TOKEN`, `ADMIN_IDS`, …) in the Render dashboard.
   Set `DATABASE_URL=sqlite+aiosqlite:////var/data/bot.db` and attach a
   **persistent disk** mounted at `/var/data`, otherwise the SQLite database and
   file_id cache are lost on every deploy.
4. Deploy. Render's free tier has no persistent disk and may spin down workers;
   use a paid instance for a production bot.

## 5. Cookies for Instagram / YouTube

Instagram and YouTube increasingly refuse anonymous requests
("login required", "Sign in to confirm you're not a bot", age-restricted videos).
yt-dlp can use your browser's cookies:

1. Install the browser extension **"Get cookies.txt LOCALLY"** (Chrome/Edge) or
   **"cookies.txt"** (Firefox).
2. Log in to instagram.com / youtube.com in that browser, ideally with a
   **throw-away account**, open the site, and export cookies in **Netscape format**.
3. Save the file as `cookies/cookies.txt` in the project.
4. In `.env`:
   ```
   COOKIES_FILE=./cookies/cookies.txt          # local run
   COOKIES_FILE=/app/cookies/cookies.txt       # docker (the folder is mounted read-only)
   ```
5. Restart the bot. Cookies expire; re-export when "login required" errors return.

Tips: don't use the same browser session for browsing afterwards (YouTube rotates
cookies), and consider `PROXY=` if your server IP gets rate-limited.
`YT_PLAYER_CLIENTS=android,web` can help when YouTube breaks a particular client.

## 6. Keeping yt-dlp fresh

Platforms change often; most "unknown error" reports are fixed by upgrading yt-dlp.

| Where | How |
|-------|-----|
| Admin in Telegram | `/update_ytdlp` (then restart the bot / container) |
| Local venv | `./scripts/update_ytdlp.sh` or `.\scripts\update_ytdlp.ps1` |
| Docker | `./scripts/update_ytdlp.sh --docker` (upgrades + restarts) |
| Automatic | `AUTO_UPDATE_YTDLP=true` in `.env` → upgrades at every container start |

## 7. Configuration reference

All variables live in `.env` (see `.env.example`).

| Variable | Default | Meaning |
|----------|---------|---------|
| `BOT_TOKEN` | — | required |
| `ADMIN_IDS` | empty | comma-separated Telegram user IDs |
| `FORCE_SUB_CHANNEL` | empty | `@channel` or `-100…` id; bot must be channel admin |
| `FORCE_SUB_CHANNEL_URL` | empty | invite link for private channels |
| `USE_LOCAL_BOT_API` | `false` | use self-hosted Bot API (2 GB cap) |
| `LOCAL_BOT_API_URL` | `http://telegram-bot-api:8081` | |
| `DATABASE_URL` | `sqlite+aiosqlite:///./data/bot.db` | |
| `DOWNLOAD_DIR` | `./downloads` | temp files, auto-deleted |
| `TEMP_FILE_TTL_MINUTES` / `CLEANUP_INTERVAL_MINUTES` | 60 / 30 | periodic cleanup |
| `COOKIES_FILE` | empty | Netscape cookies.txt |
| `PROXY` | empty | yt-dlp proxy |
| `YT_PLAYER_CLIENTS` | empty | YouTube extractor arg |
| `DOWNLOAD_TIMEOUT_SECONDS` | 900 | |
| `MAX_DURATION_MINUTES` | 180 | reject longer media |
| `AUDIO_BITRATE_KBPS` | 192 | MP3 bitrate (auto-lowered to fit size cap) |
| `RATE_LIMIT_PER_MINUTE` | 5 | downloads per user per minute |
| `MAX_CONCURRENT_DOWNLOADS` | 3 | global semaphore |
| `LOG_LEVEL` | `INFO` | |
| `DEFAULT_LANGUAGE` | `uz` | |

## 8. Admin commands

* `/stats` — users (total / today), downloads (today / total), errors today, cache stats, yt-dlp version
* `/broadcast <text>` — HTML message to all users (≈20 msg/s; users who blocked the bot are flagged)
* `/update_ytdlp` — pip-upgrade yt-dlp in place

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| "ffmpeg/ffprobe not found" in logs | install ffmpeg (Docker image already has it) |
| Instagram: "login required" | add cookies (section 5), or wait — IG rate-limits IPs |
| YouTube: "Sign in to confirm you're not a bot" | cookies, `PROXY`, or `YT_PLAYER_CLIENTS=android,web`; upgrade yt-dlp |
| Many "unknown" errors | `/update_ytdlp` |
| Buttons say "link expired" | bot restarted; the user just resends the link |
| Force-sub lets everyone through | bot is not admin in the channel (logged as error) |
