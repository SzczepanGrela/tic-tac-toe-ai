from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np


@dataclass
class GameState:
    board_size: int = 3
    win_length: int = 3
    board: np.ndarray = field(init=False, repr=False)
    current_player: int = field(default=1, init=False)

    def __post_init__(self) -> None:
        if self.board_size < 1:
            raise ValueError("board_size must be positive")
        if not 1 <= self.win_length <= self.board_size:
            raise ValueError("win_length must be between 1 and board_size")
        self.board = np.zeros((self.board_size, self.board_size), dtype=int)

    @classmethod
    def from_board(cls, board: list[list[int]] | np.ndarray) -> "GameState":
        array = np.asarray(board, dtype=int)
        if array.shape != (3, 3):
            raise ValueError("board must be a 3x3 matrix")
        if not np.isin(array, (-1, 0, 1)).all():
            raise ValueError("board cells must contain only -1, 0, or 1")
        x_count = int(np.count_nonzero(array == 1))
        o_count = int(np.count_nonzero(array == -1))
        if x_count not in (o_count, o_count + 1):
            raise ValueError("board has an invalid number of X and O marks")
        state = cls()
        state.board = array.copy()
        state.current_player = 1 if x_count == o_count else -1
        winners = state.get_winners()
        if len(winners) > 1:
            raise ValueError("both players cannot win on the same board")
        if winners == {1} and x_count != o_count + 1:
            raise ValueError("X win is inconsistent with the move count")
        if winners == {-1} and x_count != o_count:
            raise ValueError("O win is inconsistent with the move count")
        return state

    def make_move(self, row: int, column: int) -> bool:
        if not (0 <= row < self.board_size and 0 <= column < self.board_size):
            return False
        if self.board[row, column] != 0:
            return False
        self.board[row, column] = self.current_player
        self.current_player *= -1
        return True

    def get_available_moves(self) -> list[tuple[int, int]]:
        return [(row, column) for row in range(self.board_size) for column in range(self.board_size) if self.board[row, column] == 0]

    def get_winners(self) -> set[int]:
        winners: set[int] = set()
        directions = ((0, 1), (1, 0), (1, 1), (1, -1))
        for row in range(self.board_size):
            for column in range(self.board_size):
                player = int(self.board[row, column])
                if player and any(self._check_line(row, column, dr, dc, player) for dr, dc in directions):
                    winners.add(player)
        return winners

    def get_winner(self) -> Optional[int]:
        winners = self.get_winners()
        if winners:
            return next(iter(winners))
        return 0 if not self.get_available_moves() else None

    def _check_line(self, row: int, column: int, row_delta: int, column_delta: int, player: int) -> bool:
        count = 0
        while 0 <= row < self.board_size and 0 <= column < self.board_size and self.board[row, column] == player:
            count += 1
            if count >= self.win_length:
                return True
            row += row_delta
            column += column_delta
        return False

    def is_game_over(self) -> bool:
        return self.get_winner() is not None

    def reset_board(self) -> None:
        self.board.fill(0)
        self.current_player = 1

    def clone(self) -> "GameState":
        clone = GameState(self.board_size, self.win_length)
        clone.board = self.board.copy()
        clone.current_player = self.current_player
        return clone

    def get_board_copy(self) -> np.ndarray:
        return self.board.copy()

    def __str__(self) -> str:
        symbols = {0: ".", 1: "X", -1: "O"}
        return "\n".join(" ".join(symbols[int(cell)] for cell in row) for row in self.board)
