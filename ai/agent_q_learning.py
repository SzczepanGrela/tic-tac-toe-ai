from __future__ import annotations

import random
from collections import defaultdict
from pathlib import Path

import numpy as np

from ai.encoding import canonicalize, transform_move
from game.state import GameState


class QLearningAgent:
    def __init__(self, learning_rate: float = 0.25, discount_factor: float = 0.99, exploration_rate: float = 0.1) -> None:
        self.learning_rate = learning_rate
        self.discount_factor = discount_factor
        self.exploration_rate = exploration_rate
        self.q_table: defaultdict[tuple[tuple[int, ...], tuple[int, int]], float] = defaultdict(float)

    def select_move(self, game_state: GameState, rng: random.Random, epsilon: float = 0.0) -> tuple[int, int] | None:
        moves = game_state.get_available_moves()
        if not moves:
            return None
        if rng.random() < epsilon:
            return rng.choice(moves)
        state_key, transform = canonicalize(game_state.board, game_state.current_player)
        values = [(move, self.q_table[(state_key, transform_move(move, transform))]) for move in moves]
        best_value = max(value for _, value in values)
        best_moves = [move for move, value in values if value >= best_value - 1e-7]
        return rng.choice(best_moves)

    def find_best_move(self, game_state: GameState) -> tuple[int, int] | None:
        return self.select_move(game_state, random.Random(0))

    def update_transition(
        self,
        board: np.ndarray,
        current_player: int,
        action: tuple[int, int],
        reward: float,
        next_state: GameState,
        done: bool,
    ) -> None:
        state_key, transform = canonicalize(board, current_player)
        canonical_action = transform_move(action, transform)
        current_value = self.q_table[(state_key, canonical_action)]
        if done:
            target = reward
        else:
            next_key, next_transform = canonicalize(next_state.board, next_state.current_player)
            next_values = [
                self.q_table[(next_key, transform_move(move, next_transform))]
                for move in next_state.get_available_moves()
            ]
            target = reward - self.discount_factor * max(next_values, default=0.0)
        self.q_table[(state_key, canonical_action)] = current_value + self.learning_rate * (target - current_value)

    def save(self, path: str | Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        entries = sorted(self.q_table.items())
        states = np.asarray([key[0] for key, _ in entries], dtype=np.int8)
        actions = np.asarray([key[1] for key, _ in entries], dtype=np.int8)
        values = np.asarray([value for _, value in entries], dtype=np.float32)
        np.savez_compressed(path, states=states, actions=actions, values=values, format_version=np.asarray([1]))

    def load(self, path: str | Path) -> None:
        with np.load(Path(path), allow_pickle=False) as data:
            if int(data["format_version"][0]) != 1:
                raise ValueError("unsupported Q-table format")
            self.q_table.clear()
            for state, action, value in zip(data["states"], data["actions"], data["values"]):
                key = tuple(int(item) for item in state)
                move = int(action[0]), int(action[1])
                self.q_table[(key, move)] = float(value)
