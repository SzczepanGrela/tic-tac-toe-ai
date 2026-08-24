FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements-core.txt requirements-web.txt ./
RUN python -m pip install --upgrade pip && python -m pip install -r requirements-web.txt

COPY ai ./ai
COPY game ./game
COPY models ./models
COPY web ./web

RUN useradd --create-home --uid 10001 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8084
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8084/api/health', timeout=3)"

CMD ["python", "-m", "uvicorn", "web.app:app", "--host", "0.0.0.0", "--port", "8084", "--workers", "1", "--proxy-headers", "--forwarded-allow-ips", "*"]
