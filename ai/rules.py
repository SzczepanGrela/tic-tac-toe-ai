from __future__ import annotations

import random
from typing import Optional

from game.state import Coordinate, GameState


def _winning_moves(game_state: GameState, player: int) -> list[Coordinate]:
    winning_moves = []
    for move in game_state.get_available_moves():
        candidate = game_state.clone()
        candidate.current_player = player
        candidate.make_move_assuming_active(*move)
        if candidate.get_winner() == player:
            winning_moves.append(move)
    return winning_moves


def _winning_move(game_state: GameState, player: int) -> Optional[Coordinate]:
    moves = _winning_moves(game_state, player)
    return moves[0] if moves else None


def _fork_moves(game_state: GameState, player: int) -> list[Coordinate]:
    forks = []
    for move in game_state.get_available_moves():
        candidate = game_state.clone()
        candidate.current_player = player
        candidate.make_move_assuming_active(*move)
        candidate.current_player = player
        threats = len(_winning_moves(candidate, player))
        if threats >= 2:
            forks.append(move)
    return forks


def _find_classic_move(game_state: GameState, chooser: random.Random) -> Coordinate:
    moves = game_state.get_available_moves()
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
            candidate.make_move_assuming_active(*move)
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


def _position_score(game_state: GameState, move: Coordinate, player: int) -> float:
    opponent = -player
    attack = defense = 0.0
    attack_threats = defense_threats = 0
    for segment in game_state.get_line_segments(through=move):
        own_marks = sum(game_state.board[coordinate] == player for coordinate in segment)
        opponent_marks = sum(game_state.board[coordinate] == opponent for coordinate in segment)
        if opponent_marks == 0:
            attack += 4.0 ** own_marks
            attack_threats += own_marks == game_state.win_length - 2
        if own_marks == 0:
            defense += 4.0 ** opponent_marks
            defense_threats += opponent_marks == game_state.win_length - 2

    center = (game_state.board_size - 1) / 2
    distance = abs(move[0] - center) + abs(move[1] - center)
    centrality = game_state.board_size - distance
    return (
        attack
        + 0.9 * defense
        + 25.0 * attack_threats
        + 20.0 * defense_threats
        + 0.01 * centrality
    )


def _find_general_move(game_state: GameState, chooser: random.Random) -> Coordinate:
    moves = game_state.get_available_moves()
    player = game_state.current_player
    own_wins = _winning_moves(game_state, player)
    if own_wins:
        return chooser.choice(own_wins)
    opponent_wins = _winning_moves(game_state, -player)
    if opponent_wins:
        return chooser.choice(opponent_wins)

    scored = [(move, _position_score(game_state, move, player)) for move in moves]
    best_score = max(score for _, score in scored)
    best_moves = [move for move, score in scored if abs(score - best_score) < 1e-9]
    return chooser.choice(best_moves)


def find_best_move(
    game_state: GameState,
    rng: random.Random | None = None,
) -> Optional[Coordinate]:
    moves = game_state.get_available_moves()
    if game_state.is_game_over() or not moves:
        return None
    chooser = rng or random.Random()
    if game_state.board_size == 3 and game_state.win_length == 3:
        return _find_classic_move(game_state, chooser)
    return _find_general_move(game_state, chooser)
