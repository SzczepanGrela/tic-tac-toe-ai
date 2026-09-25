from __future__ import annotations

import asyncio
import json
import math
import os
import random
import secrets
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TypeVar

from ai.execution import SearchBudget, SearchStopped, check_search, current_budget
from ai.jev import JevError, JevService
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, Response, StreamingResponse
from pydantic import BaseModel, Field, StrictInt, model_validator

from ai.registry import AgentId, AgentRegistry
from game.state import GameRules, GameState

STATIC_DIR = Path(__file__).parent / "static"
STATIC_ASSETS = {
    "app.js": ("application/javascript", (STATIC_DIR / "app.js").read_bytes()),
    "favicon.svg": ("image/svg+xml", (STATIC_DIR / "favicon.svg").read_bytes()),
    "styles.css": ("text/css", (STATIC_DIR / "styles.css").read_bytes()),
    "AtkinsonHyperlegible-Regular.woff2": ("font/woff2", (STATIC_DIR / "AtkinsonHyperlegible-Regular.woff2").read_bytes()),
    "AtkinsonHyperlegible-Bold.woff2": ("font/woff2", (STATIC_DIR / "AtkinsonHyperlegible-Bold.woff2").read_bytes()),
    "BarlowCondensed-SemiBold.ttf": ("font/ttf", (STATIC_DIR / "BarlowCondensed-SemiBold.ttf").read_bytes()),
}
LOCALE_ASSETS = {
    language: (STATIC_DIR / "locales" / f"{language}.json").read_bytes()
    for language in ("en", "pl")
}
INDEX_HTML = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
RELEASE_REVISION = os.getenv("RELEASE_REVISION", "development")
AI_ACTIVE_LIMIT = 2
AI_WAITING_LIMIT = 8
AI_MOVE_TIMEOUT_SECONDS = 5.0
AI_MATCH_TIMEOUT_SECONDS = 30.0
T = TypeVar("T")


class RulesRequest(BaseModel):
    board_size: StrictInt | None = None
    win_length: StrictInt | None = None

    @model_validator(mode="after")
    def require_complete_rules(self) -> "RulesRequest":
        if (self.board_size is None) != (self.win_length is None):
            raise ValueError("board_size and win_length must be provided together")
        if self.board_size is not None:
            try:
                GameRules(self.board_size, self.win_length)
            except ValueError as exc:
                raise ValueError(str(exc)) from exc
        return self

    def game_rules(self) -> GameRules:
        if self.board_size is None:
            return GameRules()
        return GameRules(self.board_size, self.win_length)


class MoveRequest(RulesRequest):
    board: list[list[StrictInt]]
    algorithm: AgentId
    seed: StrictInt | None = None


class Move(BaseModel):
    row: int
    column: int
    player: int


class MoveResponse(BaseModel):
    move: Move
    seed: int
    board_size: int
    win_length: int
    metadata: dict[str, str | bool | None] | None = None


class MatchRequest(RulesRequest):
    x_algorithm: AgentId
    o_algorithm: AgentId
    games: StrictInt = Field(default=1, ge=1, le=10)
    seed: StrictInt | None = None


class GameTrace(BaseModel):
    game: int
    winner: int
    moves: list[Move]
    board_size: int
    win_length: int


class MatchSummary(BaseModel):
    x_wins: int
    o_wins: int
    draws: int


class MatchResponse(BaseModel):
    seed: int
    x_algorithm: AgentId
    o_algorithm: AgentId
    board_size: int
    win_length: int
    games: list[GameTrace]
    summary: MatchSummary


class TokenBucket:
    def __init__(
        self,
        capacity: int,
        refill_per_second: float,
        cleanup_interval: float = 60.0,
    ) -> None:
        self.capacity = float(capacity)
        self.refill_per_second = refill_per_second
        self.cleanup_interval = cleanup_interval
        self.buckets: dict[str, tuple[float, float]] = {}
        self.lock = asyncio.Lock()
        self._last_cleanup = time.monotonic()

    def _remove_refilled_buckets(self, now: float) -> None:
        if now - self._last_cleanup < self.cleanup_interval:
            return
        self.buckets = {
            key: (tokens, updated_at)
            for key, (tokens, updated_at) in self.buckets.items()
            if tokens + (now - updated_at) * self.refill_per_second < self.capacity
        }
        self._last_cleanup = now

    async def consume(self, key: str, cost: int = 1) -> int | None:
        if cost < 1 or cost > self.capacity:
            raise ValueError("Token cost must be between 1 and bucket capacity")
        now = time.monotonic()
        async with self.lock:
            self._remove_refilled_buckets(now)
            tokens, updated_at = self.buckets.get(key, (self.capacity, now))
            tokens = min(
                self.capacity,
                tokens + (now - updated_at) * self.refill_per_second,
            )
            if tokens >= cost:
                self.buckets[key] = (tokens - cost, now)
                return None
            self.buckets[key] = (tokens, now)
            return max(1, math.ceil((cost - tokens) / self.refill_per_second))


class WorkQueueFull(Exception):
    pass


class WorkTimedOut(Exception):
    pass


class WorkGate:
    def __init__(self, active_limit: int, waiting_limit: int) -> None:
        self._semaphore = asyncio.Semaphore(active_limit)
        self._waiting_limit = waiting_limit
        self._waiting = 0
        self._waiting_lock = asyncio.Lock()

    async def _acquire(self) -> None:
        queued = self._semaphore.locked()
        if queued:
            async with self._waiting_lock:
                if self._waiting >= self._waiting_limit:
                    raise WorkQueueFull
                self._waiting += 1
        try:
            await self._semaphore.acquire()
        finally:
            if queued:
                async with self._waiting_lock:
                    self._waiting -= 1

    @asynccontextmanager
    async def slot(self) -> AsyncIterator[None]:
        await self._acquire()
        try:
            yield
        finally:
            self._semaphore.release()

    async def run(
        self,
        function: Callable[..., T],
        *args: object,
        timeout_seconds: float,
    ) -> T:
        await self._acquire()
        release_on_exit = True
        budget = SearchBudget(time.monotonic() + timeout_seconds)
        def execute():
            token = current_budget.set(budget)
            try:
                return function(*args)
            finally:
                current_budget.reset(token)
        worker = asyncio.create_task(asyncio.to_thread(execute))
        try:
            return await asyncio.wait_for(
                asyncio.shield(worker),
                timeout=timeout_seconds,
            )
        except TimeoutError as exc:
            budget.cancelled.set()
            release_on_exit = False
            worker.add_done_callback(self._release_after_worker)
            raise WorkTimedOut from exc
        except asyncio.CancelledError:
            # asyncio cannot stop a thread that has already started. Keep the
            # work slot occupied until it really exits, then propagate the
            # cancelled request.
            budget.cancelled.set()
            release_on_exit = False
            worker.add_done_callback(self._release_after_worker)
            try:
                await asyncio.shield(worker)
            except SearchStopped:
                pass
            raise
        finally:
            if release_on_exit:
                self._semaphore.release()

    def _release_after_worker(self, worker: asyncio.Task[T]) -> None:
        try:
            worker.exception()
        except asyncio.CancelledError:
            pass
        self._semaphore.release()


move_limiter = TokenBucket(capacity=10, refill_per_second=0.5)
match_limiter = TokenBucket(capacity=30, refill_per_second=0.5)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.registry = AgentRegistry(require_models=True)
    app.state.work_gate = WorkGate(AI_ACTIVE_LIMIT, AI_WAITING_LIMIT)
    app.state.jev = JevService.from_environment()
    try:
        yield
    finally:
        await app.state.jev.aclose()


app = FastAPI(title="Tic-Tac-Toe AI Lab", version="2.1.0", lifespan=lifespan)


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


@app.get("/", include_in_schema=False)
async def index() -> HTMLResponse:
    return HTMLResponse(INDEX_HTML)


@app.get("/static/{asset_name}", include_in_schema=False)
async def static_asset(asset_name: str) -> Response:
    if asset_name not in STATIC_ASSETS:
        raise HTTPException(status_code=404, detail="Asset not found")
    media_type, content = STATIC_ASSETS[asset_name]
    return Response(content, media_type=media_type)


@app.get("/locales/{language}.json", include_in_schema=False)
async def locale_asset(language: str) -> Response:
    if language not in LOCALE_ASSETS:
        raise HTTPException(status_code=404, detail="Locale not found")
    return Response(LOCALE_ASSETS[language], media_type="application/json")


@app.get("/api/health")
async def health(request: Request) -> dict[str, object]:
    return {
        "status": "ok",
        "revision": RELEASE_REVISION,
        "agents": request.app.state.registry.health(),
    }


@app.get("/api/agents")
async def agent_capabilities(
    request: Request,
    board_size: int = Query(default=3),
    win_length: int = Query(default=3),
) -> dict[str, object]:
    try:
        rules = GameRules(board_size, win_length)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "board_size": rules.board_size,
        "win_length": rules.win_length,
        "revision": RELEASE_REVISION,
        "agents": request.app.state.registry.capabilities(rules) + [await request.app.state.jev.capability()],
    }


def _select_move(registry: AgentRegistry, agent_id: AgentId, state: GameState, rng: random.Random) -> tuple[int, int]:
    if not registry.supports_rules(agent_id, state.rules):
        raise HTTPException(
            status_code=422,
            detail=f"{agent_id.value} does not support {state.board_size}x{state.board_size} with {state.win_length} in a row",
        )
    try:
        move = registry.get(agent_id).select_move(state.clone(), rng)
    except KeyError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if move not in state.get_available_moves():
        raise HTTPException(status_code=500, detail=f"{agent_id.value} returned an illegal move")
    return move


async def _run_ai_work(
    request: Request,
    function: Callable[..., T],
    *args: object,
    timeout_seconds: float = AI_MOVE_TIMEOUT_SECONDS,
) -> T:
    try:
        return await request.app.state.work_gate.run(
            function,
            *args,
            timeout_seconds=timeout_seconds,
        )
    except WorkQueueFull as exc:
        raise HTTPException(
            status_code=429,
            detail="AI work queue is full",
            headers={"Retry-After": "1"},
        ) from exc
    except (WorkTimedOut, SearchStopped) as exc:
        raise HTTPException(status_code=504, detail="AI computation timed out") from exc


async def _while_connected(request: Request, operation):
    """Abort computation/provider I/O when the client closes a parsed request."""
    async def disconnected():
        while True:
            if (await request.receive())["type"] == "http.disconnect":
                return

    worker = asyncio.create_task(operation)
    watcher = asyncio.create_task(disconnected())
    try:
        await asyncio.wait((worker, watcher), return_when=asyncio.FIRST_COMPLETED)
        if worker.done():
            return await worker
        raise HTTPException(status_code=499, detail="Client disconnected")
    finally:
        watcher.cancel()
        if not worker.done():
            worker.cancel()
        await asyncio.gather(worker, watcher, return_exceptions=True)


@app.post("/api/move", response_model=MoveResponse)
async def calculate_move(payload: MoveRequest, request: Request) -> MoveResponse:
    retry_after = await move_limiter.consume(_client_ip(request))
    if retry_after is not None:
        raise HTTPException(
            status_code=429,
            detail="Move rate limit exceeded",
            headers={"Retry-After": str(retry_after)},
        )
    try:
        state = GameState.from_board(payload.board, payload.game_rules())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if state.is_game_over():
        raise HTTPException(status_code=409, detail="The game is already over")
    seed = payload.seed if payload.seed is not None else secrets.randbits(32)
    if payload.algorithm is AgentId.jev:
        try:
            move, metadata = await _while_connected(request, request.app.state.jev.select_move(state))
        except JevError as exc:
            raise HTTPException(status_code=exc.status, detail={"code": exc.code},
                                headers={"Retry-After": "2"} if exc.status == 429 else None) from exc
    else:
        move = await _while_connected(request, _run_ai_work(
            request, _select_move, request.app.state.registry,
            payload.algorithm, state, random.Random(seed)))
        capability = next(c for c in request.app.state.registry.capabilities(state.rules)
                          if c["id"] == payload.algorithm.value)
        metadata = {key: capability[key] for key in ("policy_version", "work_profile", "seed_reproducible")}
    metadata["server_revision"] = RELEASE_REVISION
    return MoveResponse(
        move=Move(row=move[0], column=move[1], player=state.current_player),
        seed=seed,
        board_size=state.board_size,
        win_length=state.win_length,
        metadata=metadata,
    )


def _simulate_match_game(payload: MatchRequest, registry: AgentRegistry,
                         rng: random.Random, game_number: int) -> GameTrace:
    rules = payload.game_rules()
    state = GameState(rules=rules)
    moves = []
    while not state.is_game_over():
        check_search()
        agent_id = payload.x_algorithm if state.current_player == 1 else payload.o_algorithm
        player = state.current_player
        move = _select_move(registry, agent_id, state, rng)
        state.make_move_assuming_active(*move)
        moves.append(Move(row=move[0], column=move[1], player=player))
    return GameTrace(
        game=game_number,
        winner=int(state.get_winner()),
        moves=moves,
        board_size=rules.board_size,
        win_length=rules.win_length,
    )


def _simulate_matches(payload: MatchRequest, registry: AgentRegistry, seed: int) -> MatchResponse:
    rng = random.Random(seed)
    traces = [_simulate_match_game(payload, registry, rng, game_number)
              for game_number in range(1, payload.games + 1)]
    return MatchResponse(
        seed=seed,
        x_algorithm=payload.x_algorithm,
        o_algorithm=payload.o_algorithm,
        board_size=payload.game_rules().board_size,
        win_length=payload.game_rules().win_length,
        games=traces,
        summary=MatchSummary(
            x_wins=sum(trace.winner == 1 for trace in traces),
            o_wins=sum(trace.winner == -1 for trace in traces),
            draws=sum(trace.winner == 0 for trace in traces),
        ),
    )


@app.post("/api/matches", response_model=MatchResponse)
async def calculate_matches(payload: MatchRequest, request: Request) -> MatchResponse:
    seed = await _prepare_matches(payload, request)
    return await _while_connected(request, _run_ai_work(
        request,
        _simulate_matches,
        payload,
        request.app.state.registry,
        seed,
        timeout_seconds=AI_MATCH_TIMEOUT_SECONDS,
    ))


async def _prepare_matches(payload: MatchRequest, request: Request) -> int:
    rules = payload.game_rules()
    if AgentId.jev in (payload.x_algorithm, payload.o_algorithm):
        raise HTTPException(status_code=422, detail="Jev series must use the incremental move endpoint")
    if rules != GameRules():
        raise HTTPException(
            status_code=422,
            detail="Larger-board series must use the incremental move endpoint",
        )
    for agent_id in (payload.x_algorithm, payload.o_algorithm):
        if not request.app.state.registry.supports_rules(agent_id, rules):
            raise HTTPException(status_code=422, detail=f"{agent_id.value} does not support these rules")
    retry_after = await match_limiter.consume(_client_ip(request), cost=payload.games)
    if retry_after is not None:
        raise HTTPException(
            status_code=429,
            detail="Match-series rate limit exceeded",
            headers={"Retry-After": str(retry_after)},
        )
    return payload.seed if payload.seed is not None else secrets.randbits(32)


@app.post("/api/matches/stream")
async def stream_matches(payload: MatchRequest, request: Request) -> StreamingResponse:
    seed = await _prepare_matches(payload, request)
    registry = request.app.state.registry

    async def events():
        rng = random.Random(seed)
        deadline = time.monotonic() + AI_MATCH_TIMEOUT_SECONDS
        for game_number in range(1, payload.games + 1):
            try:
                trace = await _run_ai_work(
                    request, _simulate_match_game, payload, registry, rng, game_number,
                    timeout_seconds=max(0.001, deadline - time.monotonic()),
                )
            except HTTPException as exc:
                yield json.dumps({"type": "error", "detail": exc.detail}) + "\n"
                return
            yield json.dumps({"type": "game", "game": trace.model_dump()}) + "\n"
        yield '{"type":"complete"}\n'

    return StreamingResponse(events(), media_type="application/x-ndjson", headers={
        "Cache-Control": "no-cache, no-transform",
        "X-Accel-Buffering": "no",
    })
