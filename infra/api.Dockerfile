FROM node:22-bookworm-slim AS node-runtime

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN groupadd --system --gid 10001 studio \
    && useradd --system --uid 10001 --gid 10001 --home-dir /home/studio --create-home --shell /usr/sbin/nologin studio

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ffmpeg \
        ca-certificates \
        libasound2 \
        libatk-bridge2.0-0 \
        libatk1.0-0 \
        libcairo2 \
        libdbus-1-3 \
        libdrm2 \
        libgbm1 \
        libglib2.0-0 \
        libnss3 \
        libpango-1.0-0 \
        libxcomposite1 \
        libxdamage1 \
        libxfixes3 \
        libxkbcommon0 \
        libxrandr2 \
        fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

COPY --from=node-runtime /usr/local/bin/node /usr/local/bin/node
COPY --from=node-runtime /usr/local/bin/npm /usr/local/bin/npm
COPY --from=node-runtime /usr/local/bin/npx /usr/local/bin/npx
COPY --from=node-runtime /usr/local/lib/node_modules/npm /usr/local/lib/node_modules/npm

COPY requirements.txt requirements-postgres.txt requirements-storage.txt requirements-observability.txt ./
RUN pip install --no-cache-dir -r requirements.txt -r requirements-postgres.txt -r requirements-storage.txt -r requirements-observability.txt

COPY studio_core ./studio_core
COPY studio_api ./studio_api
COPY comic_engine ./comic_engine
COPY scripts ./scripts
COPY run_api.py ./run_api.py
COPY migrations ./migrations
COPY rendering ./rendering

RUN mkdir -p /app/runtime/assets \
    && chown -R studio:studio /app/rendering /app/runtime /home/studio

ENV HOME=/home/studio

USER studio:studio

RUN cd /app/rendering \
    && npm ci --omit=dev --no-audit --no-fund \
    && node --input-type=module -e "import { ensureBrowser } from '@remotion/renderer'; await ensureBrowser({ logLevel: 'error' });"

EXPOSE 8787

HEALTHCHECK --interval=10s --timeout=5s --start-period=20s --retries=6 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8787/api/ready', timeout=3)"

CMD ["uvicorn", "studio_api.main:app", "--host", "0.0.0.0", "--port", "8787"]
