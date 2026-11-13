FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# ffmpeg for merging / MP3 / compression; ca-certificates for HTTPS
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt \
    && pip install --upgrade yt-dlp

COPY bot ./bot
COPY scripts ./scripts
RUN chmod +x scripts/*.sh && mkdir -p /app/data /app/downloads

# Run as non-root. Volumes mounted at /app/data and /app/downloads must be writable by uid 1000.
RUN useradd -m -u 1000 botuser && chown -R botuser:botuser /app
USER botuser

ENTRYPOINT ["./scripts/entrypoint.sh"]
CMD ["python", "-m", "bot.main"]
