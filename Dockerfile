# Chibi Emoji Telegram bot
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DATA_DIR=/app/data

# ffmpeg: VP9 with alpha for animated emoji, H.264 for chat previews.
# DejaVu: a font with Cyrillic for the labels on the example sheets.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# the system ffmpeg is used, so the ffmpeg bundled with imageio-ffmpeg is not needed
COPY requirements.txt .
RUN grep -v '^imageio-ffmpeg' requirements.txt > /tmp/requirements.txt \
 && pip install -r /tmp/requirements.txt \
 && rm /tmp/requirements.txt

RUN useradd --create-home --uid 1000 bot \
 && mkdir -p /app/data \
 && chown bot:bot /app/data

COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
COPY bot.py ./
COPY chibibot ./chibibot
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

VOLUME ["/app/data"]
ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["python", "bot.py"]
