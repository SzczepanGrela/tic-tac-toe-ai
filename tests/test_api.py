import asyncio

from httpx import ASGITransport, AsyncClient

from web.app import RateLimiter, app, lifespan


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


def test_match_rejects_invalid_game_count():
    response = request("POST", "/api/matches", json={"x_algorithm": "minimax", "o_algorithm": "rules", "games": 11})
    assert response.status_code == 422


def test_rate_limiter_blocks_excess_requests():
    async def exercise():
        limiter = RateLimiter(2)
        assert await limiter.allow("client")
        assert await limiter.allow("client")
        assert not await limiter.allow("client")
    asyncio.run(exercise())
