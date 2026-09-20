"""Run with python -m benchmarks.mcts --games 100; no network calls."""
from __future__ import annotations

import argparse
import json
import platform
import random
import statistics
import time

from ai.mcts_large import find_best_move
from ai.random_player import find_random_move
from ai.rules import find_best_move as rules_move
from game.state import GameState


def measure(games: int, iterations: int, lengths: list[int]) -> dict:
    result = {"python": platform.python_version(), "simulations": iterations, "games_per_side": games, "variants": {}}
    for length in lengths:
        samples = []
        # Legal early/middle/late positions, including the empty board.
        corpus = []
        for seed in range(20):
            state = GameState(5, length)
            rng = random.Random(seed + 20000)
            while not state.is_game_over():
                if sum(state.board.flat != 0) in (0, 4, 10, 16, 22):
                    corpus.append(state.clone())
                state.make_move(*rng.choice(state.get_available_moves()))
        for index, state in enumerate(corpus):
            started = time.perf_counter()
            move = find_best_move(state, random.Random(index), iterations=iterations)
            samples.append(time.perf_counter() - started)
            assert move in state.get_available_moves()
        scores = {}
        for name, opponent in (("random", find_random_move), ("rules", rules_move)):
            for side in (1, -1):
                wins = draws = losses = 0
                for seed in range(games):
                    rng = random.Random(10000 + seed)
                    state = GameState(5, length)
                    while not state.is_game_over():
                        move = (find_best_move(state, rng, iterations=iterations) if state.current_player == side
                                else opponent(state, rng))
                        assert move in state.get_available_moves()
                        state.make_move(*move)
                    winner = state.get_winner()
                    wins += winner == side
                    draws += winner == 0
                    losses += winner == -side
                scores[f"{name}_{'x' if side == 1 else 'o'}"] = {"wins": wins, "draws": draws, "losses": losses}
        result["variants"][str(length)] = {
            "positions": len(samples), "median_seconds": statistics.median(samples),
            "p95_seconds": sorted(samples)[int((len(samples) - 1) * .95)],
            "max_seconds": max(samples), "scores": scores,
        }
        print(json.dumps({"completed_length": length, **result["variants"][str(length)]}), flush=True)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--games", type=int, default=100, choices=range(1, 1001))
    parser.add_argument("--iterations", type=int, default=256, choices=(256, 512, 1024, 2048))
    parser.add_argument("--lengths", type=int, nargs="+", choices=(3, 4, 5), default=[3, 4, 5])
    args = parser.parse_args()
    print(json.dumps(measure(args.games, args.iterations, args.lengths), indent=2))
