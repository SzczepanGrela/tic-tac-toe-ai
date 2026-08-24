from __future__ import annotations

import numpy as np


def _variants(board: np.ndarray) -> list[np.ndarray]:
    rotations = [np.rot90(board, turns) for turns in range(4)]
    mirrored = np.fliplr(board)
    return rotations + [np.rot90(mirrored, turns) for turns in range(4)]


def canonicalize(board: np.ndarray, current_player: int) -> tuple[tuple[int, ...], int]:
    relative = np.asarray(board, dtype=np.int8) * current_player
    variants = _variants(relative)
    keys = [tuple(int(value) for value in variant.flatten()) for variant in variants]
    transform = min(range(8), key=keys.__getitem__)
    return keys[transform], transform


def transform_move(move: tuple[int, int], transform: int) -> tuple[int, int]:
    markers = np.arange(9).reshape(3, 3)
    transformed = _variants(markers)[transform]
    location = np.argwhere(transformed == markers[move])
    return int(location[0, 0]), int(location[0, 1])


def inverse_transform_move(move: tuple[int, int], transform: int) -> tuple[int, int]:
    for row in range(3):
        for column in range(3):
            if transform_move((row, column), transform) == move:
                return row, column
    raise ValueError(f"invalid transformed move: {move}")


def encode_board(board: np.ndarray, current_player: int) -> np.ndarray:
    relative = np.asarray(board, dtype=np.int8) * current_player
    own = (relative == 1).astype(np.float32)
    opponent = (relative == -1).astype(np.float32)
    return np.concatenate((own.flatten(), opponent.flatten()))
