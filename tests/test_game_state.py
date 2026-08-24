import numpy as np
import pytest

from game.state import GameState


@pytest.mark.parametrize("moves", [
    ((0, 0), (1, 0), (0, 1), (1, 1), (0, 2)),
    ((0, 0), (0, 1), (1, 0), (1, 1), (2, 2), (2, 1)),
    ((0, 0), (0, 1), (1, 1), (0, 2), (2, 2)),
    ((0, 2), (0, 0), (1, 1), (1, 0), (2, 0)),
])
def test_detects_wins(moves):
    state = GameState()
    for move in moves:
        assert state.make_move(*move)
    assert state.get_winner() in (-1, 1)


def test_detects_draw_and_rejects_illegal_moves():
    state = GameState.from_board([[1, -1, 1], [1, -1, -1], [-1, 1, 1]])
    assert state.get_winner() == 0
    assert not state.make_move(0, 0)
    assert not state.make_move(4, 4)


def test_clone_is_independent():
    state = GameState()
    clone = state.clone()
    clone.make_move(1, 1)
    assert np.count_nonzero(state.board) == 0


@pytest.mark.parametrize("board", [
    [[1, 1, 1], [-1, -1, -1], [0, 0, 0]],
    [[1, 1, 0], [0, 0, 0], [0, 0, 0]],
    [[2, 0, 0], [0, 0, 0], [0, 0, 0]],
    [[0, 0], [0, 0]],
])
def test_rejects_invalid_board_states(board):
    with pytest.raises(ValueError):
        GameState.from_board(board)
