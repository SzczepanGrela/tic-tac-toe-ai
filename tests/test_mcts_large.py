import random
import time

import numpy as np
import pytest

from ai.execution import SearchBudget, SearchStopped, current_budget
from ai.mcts_large import FULL, PROFILES, find_best_move, has_won, line_masks, winning_moves
from game.state import GameState


@pytest.mark.parametrize("length", [3, 4, 5])
def test_fast_lines_match_engine_on_legal_positions(length):
    rng = random.Random(139)
    for _ in range(20):
        state = GameState(5, length)
        while not state.is_game_over():
            own = sum(1 << (r * 5 + c) for r in range(5) for c in range(5) if state.board[r, c] == state.current_player)
            other = sum(1 << (r * 5 + c) for r in range(5) for c in range(5) if state.board[r, c] == -state.current_player)
            assert not has_won(own, line_masks(length))
            winning = winning_moves(own, other, line_masks(length))
            for r, c in state.get_available_moves():
                child = state.clone()
                child.make_move(r, c)
                assert bool(winning & (1 << (r * 5 + c))) == (child.get_winner() == state.current_player)
            state.make_move(*rng.choice(state.get_available_moves()))


@pytest.mark.parametrize("length", [3, 4, 5])
@pytest.mark.parametrize("player", [1, -1])
def test_search_wins_and_blocks_off_center_lines(length, player):
    state = GameState(5, length)
    for c in range(length - 1):
        state.board[3, c] = player
    assert find_best_move(state, random.Random(5)) == (3, length - 1)


@pytest.mark.parametrize("length", [3, 4, 5])
def test_search_preserves_board_and_seed(length):
    state = GameState(5, length)
    before = state.board.copy()
    first = find_best_move(state, random.Random(42))
    assert first == find_best_move(state, random.Random(42))
    assert first in state.get_available_moves()
    np.testing.assert_array_equal(state.board, before)
    assert PROFILES[length] == 256


@pytest.mark.parametrize("cancel", [False, True])
def test_search_exits_cooperatively(cancel):
    budget = SearchBudget(time.monotonic() + (10 if cancel else -1))
    if cancel:
        budget.cancelled.set()
    token = current_budget.set(budget)
    try:
        with pytest.raises(SearchStopped):
            find_best_move(GameState(5, 5), random.Random(1))
    finally:
        current_budget.reset(token)


def test_terminal_and_unsupported_board():
    state = GameState(5, 3)
    state.board[4, :3] = 1
    assert find_best_move(state, random.Random()) is None
    with pytest.raises(ValueError):
        find_best_move(GameState(9, 5), random.Random())
