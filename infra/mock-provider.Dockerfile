FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --system --uid 10001 --create-home --shell /usr/sbin/nologin mockprovider

COPY scripts/mock_provider_server.py /app/mock_provider_server.py
RUN chown -R mockprovider:mockprovider /app

USER mockprovider:mockprovider

EXPOSE 8080 8090 8188

ENTRYPOINT ["python", "/app/mock_provider_server.py"]
