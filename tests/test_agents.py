import random

import numpy as np
import pytest

from ai.encoding import canonicalize, encode_board, inverse_transform_move, transform_move
from ai.registry import AgentId, AgentRegistry
from ai.rules import find_best_move as find_rules_move
from game.state import GameRules, GameState
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


def test_registry_reports_variant_capabilities():
    registry = AgentRegistry()
    classic = {item["id"]: item for item in registry.capabilities(GameRules())}
    larger = {item["id"]: item for item in registry.capabilities(GameRules(10, 5))}

    assert all(item["available"] for item in classic.values())
    assert classic["rules"]["policy_version"] == "rules-v2"
    assert classic["dqn"]["policy_version"].startswith("sha256:")
    assert larger["random"]["available"] and larger["random"]["reason"] is None
    assert larger["random"]["work_profile"] == "one-random-legal-move"
    assert larger["rules"]["available"] and larger["rules"]["reason"] is None
    assert larger["rules"]["work_profile"] == "line-tactics"
    assert all(
        item["id"] == agent_id
        and not item["available"]
        and item["reason"] == "only_3x3"
        for agent_id, item in larger.items()
        if agent_id not in {"random", "rules"}
    )
    assert all(item["policy_version"] for item in larger.values())


@pytest.mark.parametrize(
    "board_size,win_length",
    [(board_size, win_length) for board_size in range(3, 11) for win_length in range(3, board_size + 1)],
)
def test_rules_agent_returns_legal_move_for_every_variant(board_size, win_length):
    state = GameState(board_size, win_length)
    move = find_rules_move(state, random.Random(42))

    assert move in state.get_available_moves()


def test_rules_agent_wins_and_blocks_on_larger_board():
    winning = GameState(10, 5)
    blocking = GameState(10, 5)
    for column in range(4):
        winning.board[4, column] = 1
        blocking.board[4, column] = -1

    assert find_rules_move(winning, random.Random(1)) == (4, 4)
    assert find_rules_move(blocking, random.Random(1)) == (4, 4)


def test_rules_agent_uses_one_of_the_even_board_centres():
    state = GameState(4, 3)
    move = find_rules_move(state, random.Random(42))

    assert move in {(1, 1), (1, 2), (2, 1), (2, 2)}
