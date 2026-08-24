from __future__ import annotations

import random
from dataclasses import asdict, dataclass

from ai.base import Agent
from game.state import GameState


@dataclass
class MatchSummary:
    x_wins: int = 0
    o_wins: int = 0
    draws: int = 0
    illegal_moves: int = 0


def play_game(x_agent: Agent, o_agent: Agent, rng: random.Random) -> int:
    state = GameState()
    while not state.is_game_over():
        agent = x_agent if state.current_player == 1 else o_agent
        move = agent.select_move(state.clone(), rng)
        if move not in state.get_available_moves():
            raise ValueError(f"agent returned illegal move: {move}")
        state.make_move(*move)
    return int(state.get_winner())


def evaluate_pair(x_agent: Agent, o_agent: Agent, games: int, seed: int) -> MatchSummary:
    rng = random.Random(seed)
    summary = MatchSummary()
    for _ in range(games):
        winner = play_game(x_agent, o_agent, rng)
        if winner == 1:
            summary.x_wins += 1
        elif winner == -1:
            summary.o_wins += 1
        else:
            summary.draws += 1
    return summary


def summary_dict(summary: MatchSummary) -> dict:
    return asdict(summary)
