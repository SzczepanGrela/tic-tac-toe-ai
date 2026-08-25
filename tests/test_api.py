import asyncio

from httpx import ASGITransport, AsyncClient

import web.app as web_app
from web.app import TokenBucket, app, lifespan


def request(method: str, path: str, **kwargs):
    async def perform():
        async with lifespan(app):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                return await client.request(method, path, **kwargs)
    return asyncio.run(perform())


def test_health_and_frontend():
    assert request("GET", "/").status_code == 200
    payload = request("GET", "/api/health").json()
    assert payload["status"] == "ok"
    assert set(payload["agents"].values()) == {"ready"}


def test_favicon_is_served():
    response = request("GET", "/static/favicon.svg")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/svg+xml")


def test_locales_have_matching_keys_and_reject_unknown_language():
    english = request("GET", "/locales/en.json")
    polish = request("GET", "/locales/pl.json")
    assert english.status_code == polish.status_code == 200
    assert set(english.json()) == set(polish.json())
    assert request("GET", "/locales/de.json").status_code == 404


def test_move_supports_every_agent():
    board = [[1, -1, 0], [0, 1, 0], [0, 0, -1]]
    for algorithm in ("random", "rules", "minimax", "mcts", "q_learning", "dqn", "imitation", "reinforce"):
        response = request("POST", "/api/move", json={"board": board, "algorithm": algorithm, "seed": 7})
        assert response.status_code == 200
        assert response.json()["move"]["player"] == 1


def test_match_series_is_reproducible_and_complete():
    payload = {"x_algorithm": "random", "o_algorithm": "rules", "games": 3, "seed": 123}
    first = request("POST", "/api/matches", json=payload)
    second = request("POST", "/api/matches", json=payload)
    assert first.status_code == 200
    assert first.json() == second.json()
    result = first.json()
    assert len(result["games"]) == 3
    assert sum(result["summary"].values()) == 3
    assert all(5 <= len(game["moves"]) <= 9 for game in result["games"])


def test_match_series_reports_non_draw_scores():
    payload = {"x_algorithm": "random", "o_algorithm": "minimax", "games": 10, "seed": 42}
    response = request("POST", "/api/matches", json=payload)

    assert response.status_code == 200
    assert response.json()["summary"] == {"x_wins": 0, "o_wins": 9, "draws": 1}


def test_match_rejects_invalid_game_count():
    response = request("POST", "/api/matches", json={"x_algorithm": "minimax", "o_algorithm": "rules", "games": 11})
    assert response.status_code == 422


def test_token_bucket_supports_bursts_and_weighted_costs():
    async def exercise():
        limiter = TokenBucket(capacity=3, refill_per_second=1)
        assert await limiter.consume("client", cost=2) is None
        assert await limiter.consume("client") is None
        assert await limiter.consume("client") == 1
    asyncio.run(exercise())


def test_rate_limit_response_includes_retry_after(monkeypatch):
    monkeypatch.setattr(web_app, "match_limiter", TokenBucket(capacity=1, refill_per_second=0.01))
    payload = {"x_algorithm": "random", "o_algorithm": "random", "games": 1, "seed": 7}

    assert request("POST", "/api/matches", json=payload).status_code == 200
    limited = request("POST", "/api/matches", json=payload)

    assert limited.status_code == 429
    assert int(limited.headers["retry-after"]) > 0
