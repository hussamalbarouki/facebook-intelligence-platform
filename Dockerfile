# Engine is the unchanged upstream, copied from its published image.
FROM ghcr.io/tamnd/fb:0.3.0 AS upstream
FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 FB_DATA_DIR=/data/guest
RUN apt-get update && apt-get install -y --no-install-recommends chromium ca-certificates fonts-liberation tini && rm -rf /var/lib/apt/lists/*
WORKDIR /srv
COPY --from=upstream /usr/bin/fb /usr/local/bin/fb
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY README.md ./README.md
COPY THIRD_PARTY_LICENSES ./THIRD_PARTY_LICENSES
RUN mkdir -p /data/guest /data/accounts /data/stores && useradd -m -u 10001 app && chown -R app:app /data /srv
USER app
EXPOSE 8000
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--proxy-headers", "--forwarded-allow-ips", "*"]