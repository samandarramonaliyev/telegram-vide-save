#!/bin/sh
# Upgrade yt-dlp in the local venv (or in the running container with --docker) and restart.
#   ./scripts/update_ytdlp.sh            # local venv
#   ./scripts/update_ytdlp.sh --docker   # docker compose service "bot"
set -e

if [ "$1" = "--docker" ]; then
  docker compose exec bot pip install --user --upgrade yt-dlp
  docker compose restart bot
  docker compose exec bot python -c 'import yt_dlp; print("yt-dlp", yt_dlp.version.__version__)'
else
  PY=".venv/bin/python"
  [ -x "$PY" ] || PY=".venv/Scripts/python.exe"
  [ -x "$PY" ] || PY="python"
  "$PY" -m pip install --upgrade yt-dlp
  "$PY" -c 'import yt_dlp; print("yt-dlp", yt_dlp.version.__version__)'
  echo "Restart the bot to apply the new version."
fi
