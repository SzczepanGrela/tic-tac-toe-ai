from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import numpy as np

from ai.encoding import encode_board
from ai.minimax import minimax
from game.state import GameState


def reachable_states() -> list[GameState]:
    states: dict[tuple[int, ...], GameState] = {}

    def visit(state: GameState) -> None:
        key = tuple(int(value) for value in state.board.flatten())
        if key in states or state.is_game_over():
            return
        states[key] = state.clone()
        for move in state.get_available_moves():
            child = state.clone()
            child.make_move(*move)
            visit(child)

    visit(GameState())
    return list(states.values())


def optimal_moves(state: GameState) -> list[tuple[int, int]]:
    player = state.current_player
    scored = []
    cache: dict[tuple, int] = {}
    moves = state.get_available_moves()
    for move in moves:
        child = state.clone()
        child.make_move(*move)
        score = minimax(child, len(moves) - 1, float("-inf"), float("inf"), False, player, cache)
        scored.append((move, score))
    best = max(score for _, score in scored)
    return [move for move, score in scored if score == best]


def generate_dataset(output: Path | None = None) -> tuple[np.ndarray, np.ndarray]:
    states = reachable_states()
    features = np.stack([encode_board(state.board, state.current_player) for state in states])
    targets = np.zeros((len(states), 9), dtype=np.float32)
    for index, state in enumerate(states):
        moves = optimal_moves(state)
        for row, column in moves:
            targets[index, row * 3 + column] = 1.0 / len(moves)
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(output, features=features, targets=targets)
    return features, targets
