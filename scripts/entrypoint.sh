#!/bin/sh
# Container entrypoint: optionally refresh yt-dlp, then run the given command.
set -e

if [ "${AUTO_UPDATE_YTDLP:-false}" = "true" ]; then
  echo "[entrypoint] upgrading yt-dlp..."
  pip install --user --quiet --upgrade yt-dlp || echo "[entrypoint] yt-dlp upgrade failed, continuing with installed version"
fi
echo "[entrypoint] yt-dlp $(python -c 'import yt_dlp; print(yt_dlp.version.__version__)')"

exec "$@"
