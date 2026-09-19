import numpy as np
import pytest

from game.state import GameRules, GameState


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


def test_clone_is_independent_and_preserves_rules_and_last_move():
    state = GameState(9, 5)
    assert state.make_move(3, 3)
    clone = state.clone()
    clone.make_move(3, 4)

    assert np.count_nonzero(state.board) == 1
    assert clone.rules is state.rules
    assert clone.last_move == (3, 4)


@pytest.mark.parametrize(
    "board_size,win_length",
    [(board_size, win_length) for board_size in GameRules.SUPPORTED_BOARD_SIZES for win_length in range(3, board_size + 1)],
)
def test_every_supported_variant_detects_a_horizontal_win(board_size, win_length):
    board = np.zeros((board_size, board_size), dtype=int)
    board[0, :win_length] = 1
    board[1, :win_length - 1] = -1

    state = GameState.from_board(board, GameRules(board_size, win_length))

    assert state.get_winner() == 1
    assert state.get_winning_segments(1) == (
        tuple((0, column) for column in range(win_length)),
    )


@pytest.mark.parametrize("direction", [(1, 1), (1, -1)])
def test_detects_off_centre_diagonal_segments(direction):
    rules = GameRules(5, 4)
    board = np.zeros((5, 5), dtype=int)
    start = (1, 1) if direction == (1, 1) else (1, 3)
    segment = tuple(
        (start[0] + offset * direction[0], start[1] + offset * direction[1])
        for offset in range(4)
    )
    for coordinate in segment:
        board[coordinate] = 1
    board[0, :3] = -1

    state = GameState.from_board(board, rules)

    assert segment in state.get_winning_segments(1)


def test_reports_every_segment_created_by_one_move():
    state = GameState(5, 3)
    moves = [
        (2, 1), (0, 0),
        (2, 3), (0, 1),
        (1, 2), (0, 4),
        (3, 2), (4, 0),
        (2, 2),
    ]
    for move in moves:
        assert state.make_move(*move)

    assert state.get_winner() == 1
    assert set(state.get_winning_segments(1, through=(2, 2))) == {
        ((2, 1), (2, 2), (2, 3)),
        ((1, 2), (2, 2), (3, 2)),
    }
    assert not state.make_move(4, 4)


def test_longer_line_reports_overlapping_winning_segments():
    board = np.zeros((5, 5), dtype=int)
    board[2, :4] = 1
    board[0, 0] = board[0, 2] = board[4, 4] = -1

    state = GameState.from_board(board, GameRules(5, 3))

    assert state.get_winning_segments(1) == (
        ((2, 0), (2, 1), (2, 2)),
        ((2, 1), (2, 2), (2, 3)),
    )


def test_reset_clears_last_move_and_board():
    state = GameState(5, 3)
    state.make_move(1, 1)
    state.reset_board()

    assert state.last_move is None
    assert state.current_player == 1
    assert np.count_nonzero(state.board) == 0


@pytest.mark.parametrize("board_size,win_length", [
    (2, 2),
    (4, 3),
    (6, 3),
    (7, 3),
    (8, 3),
    (10, 3),
    (11, 3),
    (3, 2),
    (3, 4),
    (True, 3),
    (3.0, 3),
])
def test_rejects_invalid_rules(board_size, win_length):
    with pytest.raises(ValueError):
        GameRules(board_size, win_length)


@pytest.mark.parametrize("board", [
    [[1, 1, 1], [-1, -1, -1], [0, 0, 0]],
    [[1, 1, 0], [0, 0, 0], [0, 0, 0]],
    [[2, 0, 0], [0, 0, 0], [0, 0, 0]],
    [[0, 0], [0, 0]],
    [[0, 0, 0], [0, 0], [0, 0, 0]],
    [[True, 0, 0], [0, 0, 0], [0, 0, 0]],
    [[1.0, 0, 0], [0, 0, 0], [0, 0, 0]],
    [["1", 0, 0], [0, 0, 0], [0, 0, 0]],
])
def test_rejects_invalid_board_states(board):
    with pytest.raises(ValueError):
        GameState.from_board(board)


def test_rejects_terminal_board_that_cannot_end_on_one_winning_move():
    board = np.zeros((5, 5), dtype=int)
    board[0, :3] = 1
    board[2, :3] = 1
    for coordinate in ((0, 4), (1, 1), (2, 4), (3, 0), (4, 2)):
        board[coordinate] = -1

    with pytest.raises(ValueError, match="after the game was won"):
        GameState.from_board(board, GameRules(5, 3))


def test_rejects_non_integer_move_coordinates_without_mutation():
    state = GameState()

    assert not state.make_move(True, 0)
    assert not state.make_move(0.0, 0)
    assert np.count_nonzero(state.board) == 0
