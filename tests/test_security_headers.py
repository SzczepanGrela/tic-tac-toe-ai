import asyncio
import json

import httpx

import web.app as web_app
from web.app import app, lifespan
from web.security_headers import SECURITY_HEADERS


def assert_security_headers(response):
    for name, value in SECURITY_HEADERS.items():
        assert response.headers.get_list(name) == [value]


def test_security_headers_cover_assets_api_errors_and_streams(monkeypatch):
    monkeypatch.setenv("JEV_ENABLED", "false")
    monkeypatch.setattr(web_app, "match_limiter", web_app.TokenBucket(1, 0.001))

    async def exercise():
        async with lifespan(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test",
            ) as client:
                for path, status in (
                    ("/", 200), ("/static/app.js", 200), ("/static/styles.css", 200),
                    ("/static/AtkinsonHyperlegible-Regular.woff2", 200),
                    ("/locales/pl.json", 200), ("/api/health", 200),
                    ("/openapi.json", 200), ("/missing", 404),
                    ("/docs", 404), ("/redoc", 404),
                ):
                    response = await client.get(path)
                    assert response.status_code == status
                    assert_security_headers(response)

                invalid = await client.post("/api/matches/stream", json={})
                assert invalid.status_code == 422
                assert_security_headers(invalid)

                payload = {"x_algorithm": "random", "o_algorithm": "random", "games": 1, "seed": 42}
                stream = await client.post("/api/matches/stream", json=payload)
                assert stream.status_code == 200
                assert_security_headers(stream)
                assert stream.headers["content-type"].startswith("application/x-ndjson")
                assert json.loads(stream.text.splitlines()[-1]) == {"type": "complete"}

                limited = await client.post("/api/matches/stream", json=payload)
                assert limited.status_code == 429
                assert int(limited.headers["retry-after"]) > 0
                assert_security_headers(limited)

    asyncio.run(exercise())


def test_unhandled_errors_keep_headers_without_disclosing_exception(monkeypatch):
    def broken_health(self):
        raise RuntimeError("private diagnostic must not reach the browser")

    monkeypatch.setattr(web_app.AgentRegistry, "health", broken_health)

    async def exercise():
        async with lifespan(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
                base_url="http://test",
            ) as client:
                response = await client.get("/api/health")
                assert response.status_code == 500
                assert response.text == "Internal Server Error"
                assert_security_headers(response)

    asyncio.run(exercise())
