import random

import numpy as np
import pytest

from ai.encoding import canonicalize, encode_board, inverse_transform_move, transform_move
from ai.registry import AgentId, AgentRegistry
from game.state import GameState
from training.dataset import reachable_states


@pytest.mark.parametrize("transform", range(8))
@pytest.mark.parametrize("move", [(0, 0), (0, 2), (1, 1), (2, 0), (2, 2)])
def test_move_transform_round_trip(transform, move):
    assert inverse_transform_move(transform_move(move, transform), transform) == move


def test_encoding_is_relative_to_current_player():
    board = np.array([[1, -1, 0], [0, 1, 0], [0, 0, -1]])
    x_features = encode_board(board, 1)
    o_features = encode_board(-board, -1)
    np.testing.assert_array_equal(x_features, o_features)


def test_canonicalization_is_rotation_invariant():
    board = np.array([[1, -1, 0], [0, 1, 0], [0, 0, -1]])
    assert canonicalize(board, 1)[0] == canonicalize(np.rot90(board), 1)[0]


def test_all_registered_agents_return_legal_moves():
    registry = AgentRegistry()
    rng = random.Random(42)
    states = reachable_states()[::23]
    for agent_id in AgentId:
        agent = registry.get(agent_id)
        for state in states:
            assert agent.select_move(state, rng) in state.get_available_moves(), agent_id


def test_registry_health_reports_every_agent_ready():
    health = AgentRegistry().health()
    assert health == {agent.value: "ready" for agent in AgentId}
