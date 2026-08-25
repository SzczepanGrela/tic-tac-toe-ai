from __future__ import annotations

import asyncio
import math
import random
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, Field

from ai.registry import AgentId, AgentRegistry
from game.state import GameState

STATIC_DIR = Path(__file__).parent / "static"
STATIC_ASSETS = {
    "app.js": ("application/javascript", (STATIC_DIR / "app.js").read_bytes()),
    "favicon.svg": ("image/svg+xml", (STATIC_DIR / "favicon.svg").read_bytes()),
    "styles.css": ("text/css", (STATIC_DIR / "styles.css").read_bytes()),
}
LOCALE_ASSETS = {
    language: (STATIC_DIR / "locales" / f"{language}.json").read_bytes()
    for language in ("en", "pl")
}
INDEX_HTML = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
AI_SEMAPHORE = asyncio.Semaphore(2)


class MoveRequest(BaseModel):
    board: list[list[int]]
    algorithm: AgentId
    seed: int | None = None


class Move(BaseModel):
    row: int
    column: int
    player: int


class MoveResponse(BaseModel):
    move: Move
    seed: int


class MatchRequest(BaseModel):
    x_algorithm: AgentId
    o_algorithm: AgentId
    games: int = Field(default=1, ge=1, le=10)
    seed: int | None = None


class GameTrace(BaseModel):
    game: int
    winner: int
    moves: list[Move]


class MatchSummary(BaseModel):
    x_wins: int
    o_wins: int
    draws: int


class MatchResponse(BaseModel):
    seed: int
    x_algorithm: AgentId
    o_algorithm: AgentId
    games: list[GameTrace]
    summary: MatchSummary


class TokenBucket:
    def __init__(self, capacity: int, refill_per_second: float) -> None:
        self.capacity = float(capacity)
        self.refill_per_second = refill_per_second
        self.buckets: dict[str, tuple[float, float]] = {}
        self.lock = asyncio.Lock()

    async def consume(self, key: str, cost: int = 1) -> int | None:
        if cost < 1 or cost > self.capacity:
            raise ValueError("Token cost must be between 1 and bucket capacity")
        now = time.monotonic()
        async with self.lock:
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


move_limiter = TokenBucket(capacity=10, refill_per_second=0.5)
match_limiter = TokenBucket(capacity=30, refill_per_second=0.5)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.registry = AgentRegistry(require_models=True)
    yield


app = FastAPI(title="Tic-Tac-Toe AI Lab", version="2.0.0", lifespan=lifespan)


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
    return {"status": "ok", "agents": request.app.state.registry.health()}


def _select_move(registry: AgentRegistry, agent_id: AgentId, state: GameState, rng: random.Random) -> tuple[int, int]:
    try:
        move = registry.get(agent_id).select_move(state.clone(), rng)
    except KeyError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if move not in state.get_available_moves():
        raise HTTPException(status_code=500, detail=f"{agent_id.value} returned an illegal move")
    return move


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
        state = GameState.from_board(payload.board)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if state.is_game_over():
        raise HTTPException(status_code=409, detail="The game is already over")
    seed = payload.seed if payload.seed is not None else secrets.randbits(32)
    async with AI_SEMAPHORE:
        move = _select_move(request.app.state.registry, payload.algorithm, state, random.Random(seed))
    return MoveResponse(move=Move(row=move[0], column=move[1], player=state.current_player), seed=seed)


def _simulate_matches(payload: MatchRequest, registry: AgentRegistry, seed: int) -> MatchResponse:
    rng = random.Random(seed)
    traces = []
    x_wins = o_wins = draws = 0
    for game_number in range(1, payload.games + 1):
        state = GameState()
        moves = []
        while not state.is_game_over():
            agent_id = payload.x_algorithm if state.current_player == 1 else payload.o_algorithm
            player = state.current_player
            move = _select_move(registry, agent_id, state, rng)
            state.make_move(*move)
            moves.append(Move(row=move[0], column=move[1], player=player))
        winner = int(state.get_winner())
        x_wins += winner == 1
        o_wins += winner == -1
        draws += winner == 0
        traces.append(GameTrace(game=game_number, winner=winner, moves=moves))
    return MatchResponse(
        seed=seed,
        x_algorithm=payload.x_algorithm,
        o_algorithm=payload.o_algorithm,
        games=traces,
        summary=MatchSummary(x_wins=x_wins, o_wins=o_wins, draws=draws),
    )


@app.post("/api/matches", response_model=MatchResponse)
async def calculate_matches(payload: MatchRequest, request: Request) -> MatchResponse:
    retry_after = await match_limiter.consume(_client_ip(request), cost=payload.games)
    if retry_after is not None:
        raise HTTPException(
            status_code=429,
            detail="Match-series rate limit exceeded",
            headers={"Retry-After": str(retry_after)},
        )
    seed = payload.seed if payload.seed is not None else secrets.randbits(32)
    async with AI_SEMAPHORE:
        return _simulate_matches(payload, request.app.state.registry, seed)
