from __future__ import annotations

import random
from typing import Protocol

from game.state import GameState


class Agent(Protocol):
    def select_move(self, game_state: GameState, rng: random.Random) -> tuple[int, int] | None: ...
