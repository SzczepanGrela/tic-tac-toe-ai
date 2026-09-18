from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from numbers import Integral
from typing import Optional

import numpy as np


Coordinate = tuple[int, int]
WinningSegment = tuple[Coordinate, ...]
_DIRECTIONS = ((0, 1), (1, 0), (1, 1), (1, -1))


def _strict_int(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, Integral):
        raise ValueError(f"{name} must be an integer")
    return int(value)


@dataclass(frozen=True, slots=True)
class GameRules:
    board_size: int = 3
    win_length: int = 3

    MIN_BOARD_SIZE = 3
    MAX_BOARD_SIZE = 10

    def __post_init__(self) -> None:
        board_size = _strict_int(self.board_size, "board_size")
        win_length = _strict_int(self.win_length, "win_length")
        if not self.MIN_BOARD_SIZE <= board_size <= self.MAX_BOARD_SIZE:
            raise ValueError(
                f"board_size must be between {self.MIN_BOARD_SIZE} and {self.MAX_BOARD_SIZE}"
            )
        if not 3 <= win_length <= board_size:
            raise ValueError("win_length must be between 3 and board_size")
        object.__setattr__(self, "board_size", board_size)
        object.__setattr__(self, "win_length", win_length)


@lru_cache(maxsize=None)
def _winning_segments(rules: GameRules) -> tuple[WinningSegment, ...]:
    segments = []
    size = rules.board_size
    length = rules.win_length
    for row in range(size):
        for column in range(size):
            for row_delta, column_delta in _DIRECTIONS:
                end_row = row + (length - 1) * row_delta
                end_column = column + (length - 1) * column_delta
                if 0 <= end_row < size and 0 <= end_column < size:
                    segments.append(
                        tuple(
                            (row + offset * row_delta, column + offset * column_delta)
                            for offset in range(length)
                        )
                    )
    return tuple(segments)


@lru_cache(maxsize=None)
def _segments_by_cell(rules: GameRules) -> dict[Coordinate, tuple[WinningSegment, ...]]:
    grouped: dict[Coordinate, list[WinningSegment]] = {
        (row, column): []
        for row in range(rules.board_size)
        for column in range(rules.board_size)
    }
    for segment in _winning_segments(rules):
        for coordinate in segment:
            grouped[coordinate].append(segment)
    return {coordinate: tuple(segments) for coordinate, segments in grouped.items()}


class GameState:
    def __init__(
        self,
        board_size: int = 3,
        win_length: int = 3,
        *,
        rules: GameRules | None = None,
    ) -> None:
        if rules is not None:
            if (board_size, win_length) != (3, 3):
                raise ValueError("pass either rules or board_size/win_length, not both")
            if not isinstance(rules, GameRules):
                raise ValueError("rules must be a GameRules instance")
            self.rules = rules
        else:
            self.rules = GameRules(board_size, win_length)
        self.board = np.zeros((self.board_size, self.board_size), dtype=np.int8)
        self.current_player = 1
        self.last_move: Coordinate | None = None

    @property
    def board_size(self) -> int:
        return self.rules.board_size

    @property
    def win_length(self) -> int:
        return self.rules.win_length

    @classmethod
    def from_board(
        cls,
        board: list[list[int]] | np.ndarray,
        rules: GameRules | None = None,
    ) -> "GameState":
        rules = rules or GameRules()
        array = cls._validated_board(board, rules)
        x_count = int(np.count_nonzero(array == 1))
        o_count = int(np.count_nonzero(array == -1))
        if x_count not in (o_count, o_count + 1):
            raise ValueError("board has an invalid number of X and O marks")

        state = cls(rules=rules)
        state.board = array
        state.current_player = 1 if x_count == o_count else -1
        winners = state.get_winners()
        if len(winners) > 1:
            raise ValueError("both players cannot win on the same board")
        if winners == {1} and x_count != o_count + 1:
            raise ValueError("X win is inconsistent with the move count")
        if winners == {-1} and x_count != o_count:
            raise ValueError("O win is inconsistent with the move count")
        if winners and not state._has_possible_winning_last_move(next(iter(winners))):
            raise ValueError("board contains moves made after the game was won")
        return state

    @staticmethod
    def _validated_board(
        board: list[list[int]] | np.ndarray,
        rules: GameRules,
    ) -> np.ndarray:
        try:
            values = np.asarray(board, dtype=object)
        except (TypeError, ValueError) as exc:
            raise ValueError("board must be a square matrix") from exc
        expected_shape = (rules.board_size, rules.board_size)
        if values.ndim != 2 or values.shape != expected_shape:
            raise ValueError(
                f"board must be a {rules.board_size}x{rules.board_size} matrix"
            )
        normalized = np.empty(expected_shape, dtype=np.int8)
        for coordinate in np.ndindex(expected_shape):
            value = values[coordinate]
            if isinstance(value, bool) or not isinstance(value, Integral):
                raise ValueError("board cells must contain only integer -1, 0, or 1")
            integer = int(value)
            if integer not in (-1, 0, 1):
                raise ValueError("board cells must contain only -1, 0, or 1")
            normalized[coordinate] = integer
        return normalized

    def _has_possible_winning_last_move(self, winner: int) -> bool:
        for row, column in np.argwhere(self.board == winner):
            coordinate = int(row), int(column)
            self.board[coordinate] = 0
            previous_winners = self.get_winners()
            self.board[coordinate] = winner
            if not previous_winners:
                return True
        return False

    def make_move(self, row: int, column: int) -> bool:
        if self.is_game_over():
            return False
        return self.make_move_assuming_active(row, column)

    def make_move_assuming_active(self, row: int, column: int) -> bool:
        """Apply a move after the caller has established that the game is active."""
        try:
            row = _strict_int(row, "row")
            column = _strict_int(column, "column")
        except ValueError:
            return False
        if not (0 <= row < self.board_size and 0 <= column < self.board_size):
            return False
        if self.board[row, column] != 0:
            return False
        self.board[row, column] = self.current_player
        self.last_move = row, column
        self.current_player *= -1
        return True

    def get_available_moves(self) -> list[Coordinate]:
        return [
            (row, column)
            for row in range(self.board_size)
            for column in range(self.board_size)
            if self.board[row, column] == 0
        ]

    def get_winning_segments(
        self,
        player: int | None = None,
        *,
        through: Coordinate | None = None,
    ) -> tuple[WinningSegment, ...]:
        if player is not None and player not in (-1, 1):
            raise ValueError("player must be -1 or 1")
        if through is None:
            candidates = _winning_segments(self.rules)
        else:
            candidates = _segments_by_cell(self.rules).get(through, ())

        matches = []
        for segment in candidates:
            first_row, first_column = segment[0]
            segment_player = int(self.board[first_row, first_column])
            if segment_player == 0 or (player is not None and segment_player != player):
                continue
            if all(self.board[row, column] == segment_player for row, column in segment):
                matches.append(segment)
        return tuple(matches)

    def get_line_segments(
        self,
        *,
        through: Coordinate | None = None,
    ) -> tuple[WinningSegment, ...]:
        if through is None:
            return _winning_segments(self.rules)
        return _segments_by_cell(self.rules).get(through, ())

    def get_winners(self) -> set[int]:
        return {
            int(self.board[segment[0]])
            for segment in self.get_winning_segments()
        }

    def get_winner(self) -> Optional[int]:
        if self.last_move is not None:
            previous_player = -self.current_player
            if self.get_winning_segments(previous_player, through=self.last_move):
                return previous_player
        else:
            winners = self.get_winners()
            if winners:
                return next(iter(winners))
        return 0 if not np.any(self.board == 0) else None

    def is_game_over(self) -> bool:
        return self.get_winner() is not None

    def reset_board(self) -> None:
        self.board.fill(0)
        self.current_player = 1
        self.last_move = None

    def clone(self) -> "GameState":
        clone = GameState(rules=self.rules)
        clone.board = self.board.copy()
        clone.current_player = self.current_player
        clone.last_move = self.last_move
        return clone

    def get_board_copy(self) -> np.ndarray:
        return self.board.copy()

    def __str__(self) -> str:
        symbols = {0: ".", 1: "X", -1: "O"}
        return "\n".join(
            " ".join(symbols[int(cell)] for cell in row)
            for row in self.board
        )
