from __future__ import annotations

import random
from enum import Enum
from pathlib import Path

from ai.agent_q_learning import QLearningAgent
from ai.base import Agent
from ai.mcts import find_best_move as find_mcts_move
from ai.minimax import find_best_move as find_minimax_move
from ai.neural import OnnxPolicyAgent
from ai.random_player import find_random_move
from ai.rules import find_best_move as find_rules_move
from game.state import GameState

ROOT = Path(__file__).resolve().parents[1]


class AgentId(str, Enum):
    random = "random"
    rules = "rules"
    minimax = "minimax"
    mcts = "mcts"
    q_learning = "q_learning"
    dqn = "dqn"
    imitation = "imitation"
    reinforce = "reinforce"


class FunctionAgent:
    def __init__(self, agent_id: AgentId) -> None:
        self.agent_id = agent_id

    def select_move(self, state: GameState, rng: random.Random) -> tuple[int, int] | None:
        if self.agent_id is AgentId.random:
            return find_random_move(state, rng)
        if self.agent_id is AgentId.rules:
            return find_rules_move(state, rng)
        if self.agent_id is AgentId.minimax:
            return find_minimax_move(state, rng=rng)
        return find_mcts_move(state, iterations=1000, rng=rng)


class QTableAdapter:
    def __init__(self, model_path: Path) -> None:
        self.agent = QLearningAgent()
        self.agent.load(model_path)

    def select_move(self, state: GameState, rng: random.Random) -> tuple[int, int] | None:
        return self.agent.select_move(state, rng)


class AgentRegistry:
    MODEL_PATHS = {
        AgentId.q_learning: ROOT / "models" / "q_learning" / "model.npz",
        AgentId.dqn: ROOT / "models" / "dqn" / "model.onnx",
        AgentId.imitation: ROOT / "models" / "imitation" / "model.onnx",
        AgentId.reinforce: ROOT / "models" / "reinforce" / "model.onnx",
    }

    def __init__(self, require_models: bool = True) -> None:
        self.agents: dict[AgentId, Agent] = {
            agent_id: FunctionAgent(agent_id)
            for agent_id in (AgentId.random, AgentId.rules, AgentId.minimax, AgentId.mcts)
        }
        self.errors: dict[AgentId, str] = {}
        for agent_id, path in self.MODEL_PATHS.items():
            try:
                self.agents[agent_id] = QTableAdapter(path) if agent_id is AgentId.q_learning else OnnxPolicyAgent(path)
            except Exception as exc:
                self.errors[agent_id] = str(exc)
        if require_models and self.errors:
            details = "; ".join(f"{agent.value}: {error}" for agent, error in self.errors.items())
            raise RuntimeError(f"required AI models could not be loaded: {details}")

    def get(self, agent_id: AgentId) -> Agent:
        if agent_id not in self.agents:
            raise KeyError(f"agent is unavailable: {agent_id.value}")
        return self.agents[agent_id]

    def health(self) -> dict[str, str]:
        return {
            agent_id.value: "ready" if agent_id in self.agents else self.errors.get(agent_id, "unavailable")
            for agent_id in AgentId
        }
