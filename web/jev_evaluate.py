"""Explicit, paid evaluation using the same durable ledger as the application."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import time
from datetime import datetime, timezone

from ai.jev import JevError, JevService, POLICY_VERSION
from ai.random_player import find_random_move
from ai.rules import find_best_move as rules_move
from game.state import GameState

VARIANTS = tuple((n, k) for n in (3, 5, 9) for k in range(3, n + 1))


def parse_variant(value: str) -> tuple[int, int]:
    try:
        size, length = (int(part) for part in value.split(":"))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Variant must be SIZE:K, such as 9:3.") from exc
    if (size, length) not in VARIANTS:
        raise argparse.ArgumentTypeError("Unsupported board size or win length.")
    return size, length


def tactical_positions(size: int, length: int):
    win = GameState(size, length)
    block = GameState(size, length)
    for column in range(length - 1):
        win.make_move(0, column)
        win.make_move(2, column)
        block.make_move(1 + column % 2, column)
        block.make_move(0, column)
    return (("win", win, (0, length - 1)), ("block", block, (0, length - 1)))


async def evaluate(service: JevService, emit, *, games: int, variants: list[tuple[int, int]]):
    async def choose(state):
        started = time.monotonic()
        move, metadata = await service.select_move(state, kind="evaluation")
        emit({"type": "move", "board_size": state.board_size, "win_length": state.win_length,
              "board": state.board.tolist(), "player": state.current_player,
              "move": move, "seconds": time.monotonic() - started, "metadata": metadata})
        return move

    for size, length in variants:
        await choose(GameState(size, length))
        for name, state, expected in tactical_positions(size, length):
            move = await choose(state)
            emit({"type": "tactic", "board_size": size, "win_length": length,
                  "tactic": name, "passed": move == expected})
        for opponent_name, opponent in (("random", find_random_move), ("rules", rules_move)):
            for side in (1, -1):
                for game in range(games):
                    seed = 30000 + game
                    rng = random.Random(seed)
                    state = GameState(size, length)
                    trace = []
                    try:
                        while not state.is_game_over():
                            player = state.current_player
                            move = await choose(state) if player == side else opponent(state, rng)
                            state.make_move(*move)
                            trace.append({"player": player, "move": move})
                    except (JevError, asyncio.CancelledError):
                        emit({"type": "interrupted_game", "board_size": size, "win_length": length,
                              "opponent": opponent_name, "jev_side": side, "seed": seed, "moves": trace})
                        raise
                    emit({"type": "game", "board_size": size, "win_length": length,
                          "opponent": opponent_name, "jev_side": side, "seed": seed,
                          "winner": int(state.get_winner()), "moves": trace})


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm-paid-evaluation", required=True, action="store_true")
    parser.add_argument("--output", required=True, help="New private JSONL file; never overwritten")
    parser.add_argument("--games-per-side", type=int, choices=range(1, 101), default=5)
    parser.add_argument("--variant", type=parse_variant, action="append", metavar="SIZE:K",
                        help="Evaluate only this board and win length; repeat for multiple variants")
    args = parser.parse_args()
    variants = args.variant or list(VARIANTS)
    if len(set(variants)) != len(variants):
        parser.error("Each variant may be selected only once.")

    async def run():
        service = JevService.from_environment()
        try:
            if reason := await service.reason():
                parser.exit(1, f"Evaluation unavailable: {reason}\n")
            # Keep provider evaluations private; no automatic publication.
            fd = os.open(args.output, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd, "w") as output:
                def emit(record):
                    output.write(json.dumps(record) + "\n")
                    output.flush()
                emit({"type": "start", "at": datetime.now(timezone.utc).isoformat(),
                      "policy": POLICY_VERSION, "server_revision": os.getenv("RELEASE_REVISION", "development"),
                      "variants": [f"{size}:{length}" for size, length in variants],
                      "accounting": await asyncio.to_thread(service.ledger.status)})
                try:
                    await evaluate(service, emit, games=args.games_per_side, variants=variants)
                except JevError as exc:
                    record = {"type": "stopped", "reason": exc.code}
                    if exc.diagnostic is not None:
                        record["diagnostic"] = exc.diagnostic
                    emit(record)
                    return 1
                emit({"type": "complete", "accounting": await asyncio.to_thread(service.ledger.status)})
            return 0
        finally:
            await service.aclose()

    try:
        return asyncio.run(run())
    except OSError:
        parser.exit(1, "Cannot create the private evaluation output file.\n")


if __name__ == "__main__":
    raise SystemExit(main())
