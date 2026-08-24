import random
from typing import Optional

from game.state import GameState


def _winning_moves(game_state: GameState, player: int) -> list[tuple[int, int]]:
    winning_moves = []
    for move in game_state.get_available_moves():
        candidate = game_state.clone()
        candidate.current_player = player
        candidate.make_move(*move)
        if candidate.get_winner() == player:
            winning_moves.append(move)
    return winning_moves


def _winning_move(game_state: GameState, player: int) -> Optional[tuple[int, int]]:
    moves = _winning_moves(game_state, player)
    return moves[0] if moves else None


def _fork_moves(game_state: GameState, player: int) -> list[tuple[int, int]]:
    forks = []
    for move in game_state.get_available_moves():
        candidate = game_state.clone()
        candidate.current_player = player
        candidate.make_move(*move)
        candidate.current_player = player
        threats = len(_winning_moves(candidate, player))
        if threats >= 2:
            forks.append(move)
    return forks


def find_best_move(game_state: GameState, rng: random.Random | None = None) -> Optional[tuple[int, int]]:
    chooser = rng or random
    moves = game_state.get_available_moves()
    if not moves:
        return None
    player = game_state.current_player
    opponent = -player
    if move := _winning_move(game_state, player):
        return move
    if move := _winning_move(game_state, opponent):
        return move
    forks = _fork_moves(game_state, player)
    if forks:
        return chooser.choice(forks)
    opponent_forks = _fork_moves(game_state, opponent)
    if len(opponent_forks) == 1:
        return opponent_forks[0]
    if len(opponent_forks) > 1:
        forcing_moves = []
        for move in moves:
            candidate = game_state.clone()
            candidate.make_move(*move)
            if _winning_move(candidate, player) is not None:
                forcing_moves.append(move)
        if forcing_moves:
            return chooser.choice(forcing_moves)
    center = (1, 1)
    if center in moves:
        return center
    opposite_corners = [((0, 0), (2, 2)), ((0, 2), (2, 0))]
    for occupied, opposite in opposite_corners:
        if game_state.board[occupied] == opponent and opposite in moves:
            return opposite
    corners = [move for move in ((0, 0), (0, 2), (2, 0), (2, 2)) if move in moves]
    if corners:
        return chooser.choice(corners)
    return chooser.choice(moves)
