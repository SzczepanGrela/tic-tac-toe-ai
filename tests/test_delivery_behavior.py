"""Delivery contracts: client identity, shared endpoint budgets and real HTTP streams."""

import asyncio
import json
import logging
import re
import socket
import threading
import time
from types import SimpleNamespace

import httpx
import pytest
import uvicorn
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from ai.execution import check_search
import web.app as web_app
from web.app import TokenBucket, app, lifespan


MATCH = {"x_algorithm": "random", "o_algorithm": "random", "games": 1, "seed": 42}
MOVE = {"algorithm": "random", "board": [[0] * 3 for _ in range(3)], "seed": 42}


def test_weighted_budget_is_shared_by_batch_and_stream_but_isolates_clients(monkeypatch):
    monkeypatch.setattr(web_app, "match_limiter", TokenBucket(30, 0.5))
    monkeypatch.setattr(web_app, "move_limiter", TokenBucket(10, 0.5))

    async def exercise():
        async with lifespan(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app, client=("192.0.2.1", 1234)),
                base_url="http://test",
            ) as first, httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app, client=("192.0.2.2", 1234)),
                base_url="http://test",
            ) as second:
                for path in ("/api/matches", "/api/matches/stream", "/api/matches"):
                    response = await first.post(path, json=MATCH | {"games": 10})
                    assert response.status_code == 200
                for path in ("/api/matches", "/api/matches/stream"):
                    response = await first.post(path, json=MATCH | {"games": 10})
                    assert response.status_code == 429
                    assert response.json()["detail"] == "Match-series rate limit exceeded"
                    assert 1 <= int(response.headers["retry-after"]) <= 20
                    assert (await second.post(path, json=MATCH)).status_code == 200
                assert (await first.post("/api/move", json=MOVE)).status_code == 200
                assert (await first.get("/api/health")).status_code == 200

    asyncio.run(exercise())


@pytest.mark.parametrize("trusted", [False, True])
def test_forwarded_identity_obeys_proxy_trust_and_cannot_reset_budget(monkeypatch, trusted):
    monkeypatch.setattr(web_app, "move_limiter", TokenBucket(2, 0.5))
    wrapped = ProxyHeadersMiddleware(app, trusted_hosts=["127.0.0.1"])
    peer = "127.0.0.1" if trusted else "192.0.2.10"

    async def exercise():
        async with lifespan(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=wrapped, client=(peer, 1234)),
                base_url="http://test",
            ) as client:
                first = {"X-Forwarded-For": "198.51.100.1"}
                for _ in range(2):
                    assert (await client.post("/api/move", json=MOVE, headers=first)).status_code == 200
                blocked = await client.post("/api/move", json=MOVE, headers=first)
                assert blocked.status_code == 429
                assert int(blocked.headers["retry-after"]) >= 1
                changed = await client.post("/api/move", json=MOVE, headers={
                    "X-Forwarded-For": "198.51.100.2",
                    "CF-Connecting-IP": "198.51.100.3",
                    "X-Real-IP": "198.51.100.4",
                })
                assert changed.status_code == (200 if trusted else 429)

    asyncio.run(exercise())


def test_bucket_retry_recovers_without_refreshing_another_clients_budget(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(web_app, "time", SimpleNamespace(monotonic=lambda: now[0]))

    async def exercise():
        bucket = TokenBucket(10, 0.5)
        assert await bucket.consume("first", cost=10) is None
        assert await bucket.consume("first") == 2
        assert await bucket.consume("second", cost=10) is None
        now[0] += 1
        assert await bucket.consume("first") == 1
        now[0] += 1
        assert await bucket.consume("first") is None
        assert await bucket.consume("first") == 2
        assert await bucket.consume("second") is None
        outcomes = await asyncio.gather(*(bucket.consume("third") for _ in range(12)))
        assert outcomes.count(None) == 10
        assert outcomes.count(2) == 2

    asyncio.run(exercise())


@pytest.mark.parametrize("path", ["/api/move", "/api/matches", "/api/matches/stream"])
def test_full_work_queue_preserves_health_then_recovers(monkeypatch, path):
    select = web_app._select_move
    release = threading.Event()
    monkeypatch.setattr(web_app, "move_limiter", TokenBucket(100, 100))
    monkeypatch.setattr(web_app, "match_limiter", TokenBucket(30, 0.5))

    def controlled(*args):
        assert release.wait(3), "test did not release occupied workers"
        return select(*args)

    monkeypatch.setattr(web_app, "_select_move", controlled)

    async def exercise():
        async with lifespan(app):
            gate = app.state.work_gate
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                tasks = [asyncio.create_task(client.post("/api/move", json=MOVE))
                         for _ in range(web_app.AI_ACTIVE_LIMIT + web_app.AI_WAITING_LIMIT)]
                try:
                    async with asyncio.timeout(2):
                        while gate._waiting != web_app.AI_WAITING_LIMIT:
                            await asyncio.sleep(0.001)
                    payload = MOVE if path == "/api/move" else MATCH
                    response = await client.post(path, json=payload)
                    if path.endswith("/stream"):
                        # Headers are already sent. An error frame must end the
                        # stream without a game or a false complete marker.
                        assert response.status_code == 200
                        assert [json.loads(line) for line in response.text.splitlines()] == [
                            {"type": "error", "detail": "AI work queue is full"}]
                    else:
                        assert response.status_code == 429
                        assert response.headers["retry-after"] == "1"
                    assert (await client.get("/api/health")).status_code == 200
                    # Disconnect a queued caller and use its place again.
                    tasks[-1].cancel()
                    await asyncio.gather(tasks[-1], return_exceptions=True)
                    replacement = asyncio.create_task(client.post("/api/move", json=MOVE))
                    tasks[-1] = replacement
                    release.set()
                    responses = await asyncio.wait_for(asyncio.gather(*tasks), timeout=3)
                    assert all(response.status_code == 200 for response in responses)
                    assert (await client.post("/api/move", json=MOVE)).status_code == 200
                finally:
                    release.set()
                    for task in tasks:
                        if not task.done():
                            task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)

    asyncio.run(exercise())


@pytest.fixture
def stream_logs(caplog, monkeypatch):
    logger = web_app.STREAM_LOGGER
    caplog.set_level(logging.INFO, logger=logger.name)
    monkeypatch.setattr(logger, "propagate", False)
    logger.addHandler(caplog.handler)
    try:
        yield lambda: [r.getMessage() for r in caplog.records if r.name == logger.name]
    finally:
        logger.removeHandler(caplog.handler)


def wait_for_log(logs, fragment):
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        if any(fragment in line for line in logs()):
            return
        time.sleep(0.005)
    pytest.fail(f"Missing delivery log: {fragment}")


@pytest.fixture(params=["h11", "httptools"])
def http_server(monkeypatch, request):
    """Use a real socket: an in-process HTTP transport buffers streamed bodies."""
    monkeypatch.setenv("JEV_ENABLED", "false")
    monkeypatch.setattr(web_app, "match_limiter", TokenBucket(30, 0.5))
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(
            app, loop="asyncio", http=request.param, log_level="error", lifespan="on",
            timeout_graceful_shutdown=3,
        ))
        thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 5
            while not server.started and thread.is_alive() and time.monotonic() < deadline:
                time.sleep(0.01)
            assert server.started, "test HTTP server did not start"
            yield f"http://127.0.0.1:{port}"
        finally:
            server.should_exit = True
            thread.join(timeout=5)
            assert not thread.is_alive(), "test HTTP server did not stop"


def test_http_stream_delivers_game_before_next_game_finishes(monkeypatch, http_server, stream_logs):
    simulate = web_app._simulate_match_game
    second_started = threading.Event()
    release_second = threading.Event()

    def controlled(*args):
        if args[-1] == 2:
            second_started.set()
            assert release_second.wait(5), "test did not release the second game"
        return simulate(*args)

    monkeypatch.setattr(web_app, "_simulate_match_game", controlled)
    try:
        with httpx.stream("POST", f"{http_server}/api/matches/stream", json=MATCH | {"games": 2},
                          headers={"X-Stream-ID": "untrusted-client-marker"}, timeout=2) as response:
            assert response.status_code == 200
            stream_id = response.headers["x-stream-id"]
            assert re.fullmatch(r"[0-9a-f]{32}", stream_id)
            assert response.headers["content-type"].startswith("application/x-ndjson")
            assert "no-transform" in response.headers["cache-control"]
            lines = response.iter_lines()
            first = json.loads(next(lines))
            assert first["type"] == "game" and first["game"]["game"] == 1
            assert second_started.wait(2)
            assert not release_second.is_set()
            release_second.set()
            remaining = [json.loads(line) for line in lines]
            assert remaining[0]["game"]["game"] == 2
            assert remaining[1:] == [{"type": "complete"}]
        wait_for_log(stream_logs, f"id={stream_id} event=closed outcome=complete completed_games=2")
        for game in (1, 2):
            assert any(f"id={stream_id} game={game} event=finished outcome=completed" in line
                       for line in stream_logs())
        assert all("untrusted-client-marker" not in line for line in stream_logs())
        assert all("seed=" not in line and "board=" not in line for line in stream_logs())
        another = httpx.post(f"{http_server}/api/matches/stream", json=MATCH, timeout=2)
        assert another.headers["x-stream-id"] != stream_id
    finally:
        release_second.set()


def test_http_disconnect_stops_stream_worker_and_later_games(monkeypatch, http_server, stream_logs):
    simulate = web_app._simulate_match_game
    second_started = threading.Event()
    second_stopped = threading.Event()
    cleanup = threading.Event()
    games = []

    def controlled(*args):
        game = args[-1]
        games.append(game)
        if game == 2:
            second_started.set()
            try:
                while not cleanup.wait(0.005):
                    check_search()
            finally:
                second_stopped.set()
        return simulate(*args)

    monkeypatch.setattr(web_app, "_simulate_match_game", controlled)
    try:
        with httpx.stream("POST", f"{http_server}/api/matches/stream", json=MATCH | {"games": 3}, timeout=2) as response:
            stream_id = response.headers["x-stream-id"]
            assert json.loads(next(response.iter_lines()))["game"]["game"] == 1
            assert second_started.wait(2)
        assert second_stopped.wait(2), "disconnected stream kept computing"
        wait_for_log(stream_logs, f"id={stream_id} game=2 event=finished outcome=stopped")
        wait_for_log(stream_logs, f"id={stream_id} event=closed outcome=cancelled completed_games=1")
        assert games == [1, 2]
        assert not any(f"id={stream_id} game=3 " in line for line in stream_logs())
        assert httpx.get(f"{http_server}/api/health", timeout=2).status_code == 200
        assert httpx.post(f"{http_server}/api/matches", json=MATCH, timeout=2).status_code == 200
    finally:
        cleanup.set()


def test_stream_timeout_does_not_log_worker_completion_before_it_exits(monkeypatch, http_server, stream_logs):
    simulate = web_app._simulate_match_game
    started = threading.Event()
    release = threading.Event()

    def controlled(*args):
        started.set()
        assert release.wait(5), "test did not release worker"
        return simulate(*args)

    monkeypatch.setattr(web_app, "_simulate_match_game", controlled)
    monkeypatch.setattr(web_app, "AI_MATCH_TIMEOUT_SECONDS", 0.05)
    try:
        response = httpx.post(f"{http_server}/api/matches/stream", json=MATCH, timeout=2)
        stream_id = response.headers["x-stream-id"]
        assert started.is_set()
        assert response.status_code == 200
        assert response.json() == {"type": "error", "detail": "AI computation timed out"}
        wait_for_log(stream_logs, f"id={stream_id} event=closed outcome=error completed_games=0")
        assert not any(f"id={stream_id} game=1 event=finished" in line for line in stream_logs())
        assert app.state.work_gate._semaphore._value == 1
        release.set()
        wait_for_log(stream_logs, f"id={stream_id} game=1 event=finished outcome=stopped")
    finally:
        release.set()
