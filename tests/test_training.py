from pathlib import Path

import numpy as np

from ai.agent_q_learning import QLearningAgent
from game.state import GameState
from training.dataset import generate_dataset


def test_minimax_dataset_contains_normalized_targets():
    features, targets = generate_dataset()
    assert features.shape == (4520, 18)
    assert targets.shape == (4520, 9)
    np.testing.assert_allclose(targets.sum(axis=1), 1.0, atol=1e-6)


def test_q_table_npz_round_trip(tmp_path: Path):
    agent = QLearningAgent()
    state = GameState()
    agent.update_transition(state.board.copy(), 1, (1, 1), 0.5, state, True)
    output = tmp_path / "model.npz"
    agent.save(output)
    loaded = QLearningAgent()
    loaded.load(output)
    assert dict(agent.q_table) == dict(loaded.q_table)
