from __future__ import annotations

import json
import random
from dataclasses import asdict, dataclass
from pathlib import Path

from ai.base import Agent
from ai.registry import AgentId, FunctionAgent
from game.state import GameState


@dataclass
class CandidateResult:
    opponent: str
    games: int
    wins: int = 0
    losses: int = 0
    draws: int = 0
    illegal_moves: int = 0

    @property
    def non_loss_rate(self) -> float:
        return (self.wins + self.draws) / self.games if self.games else 0.0


def evaluate_candidate(candidate: Agent, opponent: Agent, opponent_name: str, games: int, seed: int) -> CandidateResult:
    rng = random.Random(seed)
    result = CandidateResult(opponent_name, games)
    for game_number in range(games):
        candidate_player = 1 if game_number % 2 == 0 else -1
        state = GameState()
        invalid = False
        while not state.is_game_over():
            active = candidate if state.current_player == candidate_player else opponent
            move = active.select_move(state.clone(), rng)
            if move not in state.get_available_moves():
                if active is candidate:
                    result.illegal_moves += 1
                    result.losses += 1
                else:
                    result.wins += 1
                invalid = True
                break
            state.make_move(*move)
        if invalid:
            continue
        winner = state.get_winner()
        if winner == candidate_player:
            result.wins += 1
        elif winner == -candidate_player:
            result.losses += 1
        else:
            result.draws += 1
    return result


def benchmark_candidate(candidate: Agent, games: int = 1000, seed: int = 42) -> dict:
    results = {}
    for index, opponent_id in enumerate((AgentId.random, AgentId.rules, AgentId.minimax, AgentId.mcts)):
        count = games if opponent_id in (AgentId.random, AgentId.rules) else max(100, games // 5)
        result = evaluate_candidate(candidate, FunctionAgent(opponent_id), opponent_id.value, count, seed + index)
        payload = asdict(result)
        payload["non_loss_rate"] = result.non_loss_rate
        results[opponent_id.value] = payload
    passed = (
        all(result["illegal_moves"] == 0 for result in results.values())
        and results["random"]["non_loss_rate"] >= 0.99
        and results["rules"]["non_loss_rate"] >= 0.95
    )
    return {"seed": seed, "promotion_passed": passed, "results": results}


def save_benchmark(report: dict, path: Path) -> None:
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
