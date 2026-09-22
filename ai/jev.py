"""Optional remote policy. Local readiness never depends on this service."""
from __future__ import annotations

import asyncio
import logging
import math
import os
import time
from contextlib import asynccontextmanager

from typesafe_sdk import (
    AsyncTypeSafeClient, Choice, RetryPolicy, TypeSafeError,
    TypeSafeAPITimeoutError, TypeSafeAPIResponseValidationError,
)

from game.state import GameState
from web.jev_budget import BudgetExhausted, BudgetLedger, DEFAULT_PATH, LedgerUnavailable, MODEL

PROMPT_VERSION = "jev-game-v2"
POLICY_VERSION = f"{MODEL}:{PROMPT_VERSION}"
logger = logging.getLogger(__name__)


class JevError(Exception):
    def __init__(self, code: str, status: int = 503, *, diagnostic: str | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.status = status
        self.diagnostic = diagnostic


def _invalid_response(diagnostic: str) -> JevError:
    # Keep upstream payloads out of logs and public errors. The controlled
    # category is sufficient for a private evaluation to locate our failed
    # contract check without exposing prompts, probabilities or credentials.
    logger.warning("Jev provider response rejected diagnostic=%s", diagnostic)
    return JevError("provider_response_invalid", 502, diagnostic=diagnostic)


class JevService:
    def __init__(self, client=None, ledger: BudgetLedger | None = None, *, reason="disabled",
                 timeout: float = 5.0, queue_timeout: float = 2.0) -> None:
        self.client = client
        self.ledger = ledger
        self.configuration_reason = None if client is not None and ledger is not None else reason
        self.timeout = timeout
        self.queue_timeout = queue_timeout
        self._semaphore = asyncio.Semaphore(2)
        self._waiting = 0
        self._unavailable_until = 0.0

    @classmethod
    def from_environment(cls):
        if os.getenv("JEV_ENABLED", "false").lower() != "true":
            return cls()
        key = os.getenv("TYPESAFE_API_KEY", "").strip()
        if not key:
            return cls(reason="not_configured")
        try:
            client = AsyncTypeSafeClient(api_key=key, model=MODEL,
                                        base_url="https://api.typesafe.ai",
                                        retry=RetryPolicy(max_retries=0, timeout=5.0), timeout=5.0)
        except TypeSafeError:
            return cls(reason="not_configured")
        return cls(client, BudgetLedger(os.getenv("JEV_USAGE_DB", DEFAULT_PATH)))

    async def aclose(self):
        if self.client is not None:
            await self.client.aclose()

    async def reason(self) -> str | None:
        if self.configuration_reason:
            return self.configuration_reason
        if time.monotonic() < self._unavailable_until:
            return "provider_unavailable"
        try:
            status = await asyncio.to_thread(self.ledger.status)
            return None if status["available"] else "budget_exhausted"
        except LedgerUnavailable:
            return "accounting_unavailable"

    async def capability(self) -> dict:
        reason = await self.reason()
        return {"id": "jev", "available": reason is None, "reason": reason,
                "policy_version": POLICY_VERSION, "work_profile": "one-choice-request",
                "series_mode": "incremental", "seed_reproducible": False,
                "experimental": True}

    @asynccontextmanager
    async def slot(self):
        queued = self._semaphore.locked()
        if queued:
            if self._waiting >= 4:
                raise JevError("queue_full", 429)
            self._waiting += 1
        try:
            try:
                if queued:
                    await asyncio.wait_for(self._semaphore.acquire(), self.queue_timeout)
                else:
                    # Acquire without spawning a task: a simultaneous burst must
                    # see these slots occupied before counting the waiting queue.
                    await self._semaphore.acquire()
            except (TimeoutError, TypeSafeAPITimeoutError) as exc:
                raise JevError("queue_full", 429) from exc
        finally:
            if queued:
                self._waiting -= 1
        try:
            yield
        finally:
            self._semaphore.release()

    @staticmethod
    def question(state: GameState) -> tuple[dict, dict, dict]:
        moves = {f"r{r + 1}c{c + 1}": (r, c) for r, c in state.get_available_moves()}
        symbols = {1: "X", -1: "O", 0: "."}
        player = symbols[state.current_player]
        opponent = symbols[-state.current_player]
        context = {
            "board_size": state.board_size, "marks_to_win": state.win_length,
            "player_to_move": player, "opponent": opponent,
            "board_rows": [" ".join(symbols[int(cell)] for cell in row) for row in state.board],
            "cell_symbols": {
                "X": "A cell occupied by player X.",
                "O": "A cell occupied by player O.",
                ".": "An empty cell where the current player may move.",
            },
            "coordinate_system": {
                "row_origin": 1, "column_origin": 1,
                "meaning": "Rows increase from top to bottom; columns increase from left to right.",
            },
        }
        question = Choice(
            instructions={
                "question": (
                    "Which provided legal move should `player_to_move` choose in this tic-tac-toe position?"
                ),
                "inspect": [
                    "`board_size` and `marks_to_win`",
                    "`player_to_move` and `opponent`",
                    "the complete current position in `board_rows`",
                    "`cell_symbols` and `coordinate_system`",
                    "every legal move supplied in this question's criteria",
                ],
                "game_rules": [
                    "The board is square: `board_size` rows by `board_size` columns.",
                    "X moves first; X and O then alternate exactly one mark per turn.",
                    (
                        "A player wins immediately upon forming at least `marks_to_win` consecutive marks "
                        "horizontally, vertically, or diagonally. A longer consecutive line also wins."
                    ),
                    "The game stops on its first winning move.",
                    "A full board without a winning line is a draw.",
                    "Every criterion is a currently empty legal cell; no unlisted move is legal.",
                ],
                "decision_objective": [
                    "Choose the move with the best achievable game result for `player_to_move`.",
                    "Prefer a forced win over a possible win, a possible win over a draw, and a draw over a loss.",
                    "Assume `opponent` will respond with moves that are best for them.",
                    "Take an immediate winning move when one exists.",
                    (
                        "If there is no immediate win, prevent an immediate opponent win whenever that is "
                        "necessary to avoid losing."
                    ),
                    "Consider threats and responses beyond the next single move.",
                ],
                "output_constraint": "Return exactly one option from the supplied criteria.",
            },
            criteria={
                key: (
                    f"Place {player} in the currently empty legal cell at row {r + 1}, "
                    f"column {c + 1}."
                )
                for key, (r, c) in moves.items()
            },
        )
        return context, {"move": question}, moves

    async def select_move(self, state: GameState, *, kind: str = "production") -> tuple[tuple[int, int], dict]:
        # This service is also used by the opt-in evaluator, not only HTTP handlers.
        state = GameState.from_board(state.board, state.rules)
        if state.is_game_over():
            raise JevError("game_over", 409)
        if reason := await self.reason():
            raise JevError(reason)
        metadata = {"policy_version": POLICY_VERSION, "work_profile": "one-choice-request",
                    "model": MODEL, "prompt_version": PROMPT_VERSION, "seed_reproducible": False}
        context, questions, moves = self.question(state)
        if len(moves) == 1:
            return next(iter(moves.values())), {**metadata, "work_profile": "forced-legal-move", "model": None}
        async with self.slot():
            try:
                async with asyncio.timeout(self.timeout):
                    reservation = await asyncio.to_thread(self.ledger.reserve, kind)
                    response = await self.client.system_one(state=context, questions=questions, model=MODEL)
                    if response.model != MODEL:
                        await asyncio.to_thread(self.ledger.pause)
                        raise _invalid_response("model_mismatch")
                    # The provider can omit usage: retain the full reservation then.
                    if response.usage.input_tokens is not None:
                        await asyncio.to_thread(self.ledger.settle, reservation, response.usage.input_tokens)
                    answer = response.choices.get("move")
                    if answer is None:
                        raise _invalid_response("missing_choice_answer")
                    if answer.choice not in moves:
                        raise _invalid_response("choice_not_in_criteria")
                    probabilities = answer.probabilities
                    if set(probabilities) != set(moves):
                        raise _invalid_response("probability_key_mismatch")
                    if any(not math.isfinite(p) or not 0 <= p <= 1 for p in probabilities.values()):
                        raise _invalid_response("probability_value_invalid")
                    if not math.isclose(sum(probabilities.values()), 1.0, abs_tol=0.01):
                        raise _invalid_response("probability_sum_invalid")
                    if not math.isfinite(answer.confidence) or not 0 <= answer.confidence <= 1:
                        raise _invalid_response("confidence_invalid")
                    logger.info("Jev move accounted model=%s prompt=%s reservation=%s", MODEL, PROMPT_VERSION, reservation)
                    return moves[answer.choice], {**metadata, "model": response.model}
            except BudgetExhausted as exc:
                raise JevError("budget_exhausted") from exc
            except LedgerUnavailable as exc:
                raise JevError("accounting_unavailable") from exc
            except (TimeoutError, TypeSafeAPITimeoutError) as exc:
                self._unavailable_until = time.monotonic() + 60
                raise JevError("provider_timeout", 504) from exc
            except TypeSafeAPIResponseValidationError as exc:
                raise _invalid_response("response_schema_invalid") from exc
            except TypeSafeError as exc:
                # No raw upstream text, URLs, headers or credentials in HTTP errors.
                self._unavailable_until = time.monotonic() + 60
                raise JevError("provider_unavailable") from exc
