from __future__ import annotations

import random
from pathlib import Path

import numpy as np
import onnxruntime as ort

from ai.encoding import encode_board
from game.state import GameState


class OnnxPolicyAgent:
    def __init__(self, model_path: str | Path) -> None:
        self.model_path = Path(model_path)
        self.session = ort.InferenceSession(str(self.model_path), providers=["CPUExecutionProvider"])
        self.input_name = self.session.get_inputs()[0].name

    def scores(self, game_state: GameState) -> np.ndarray:
        features = encode_board(game_state.board, game_state.current_player)[None, :]
        return np.asarray(self.session.run(None, {self.input_name: features})[0][0], dtype=np.float32)

    def select_move(self, game_state: GameState, rng: random.Random) -> tuple[int, int] | None:
        moves = game_state.get_available_moves()
        if not moves:
            return None
        scores = self.scores(game_state)
        return max(moves, key=lambda move: (float(scores[move[0] * 3 + move[1]]), -(move[0] * 3 + move[1])))
