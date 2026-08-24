import random
from typing import Optional

from game.state import GameState


def find_random_move(game_state: GameState, rng: random.Random | None = None) -> Optional[tuple[int, int]]:
    moves = game_state.get_available_moves()
    return (rng or random).choice(moves) if moves else None
