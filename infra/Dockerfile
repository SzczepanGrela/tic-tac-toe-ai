FROM python:3.12-slim@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea

LABEL org.opencontainers.image.title="Tic-Tac-Toe AI Lab" \
      org.opencontainers.image.source="https://github.com/SzczepanGrela/tic-tac-toe-ai"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements-web.lock ./
RUN python -m pip install --require-hashes -r requirements-web.lock

COPY ai ./ai
COPY game ./game
COPY models ./models
COPY web ./web

ARG RELEASE_REVISION=development
LABEL org.opencontainers.image.revision="${RELEASE_REVISION}"
ENV RELEASE_REVISION="${RELEASE_REVISION}"

RUN useradd --create-home --uid 10001 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8080
STOPSIGNAL SIGTERM
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/api/health', timeout=3)"

CMD ["python", "-m", "uvicorn", "web.app:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1", "--proxy-headers"]
