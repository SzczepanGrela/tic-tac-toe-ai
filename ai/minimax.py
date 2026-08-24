from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Optional

from game.state import GameState


@dataclass(eq=False)
class Node:
    game_state: GameState
    move: Optional[tuple[int, int]] = None
    result: Optional[float] = None
    depth: int = 0
    children: list["Node"] = field(default_factory=list)
    is_max_turn: Optional[bool] = None
    alpha: Optional[float] = None
    beta: Optional[float] = None


def evaluate_state(game_state: GameState, ai_player: int, depth: int = 0) -> int:
    winner = game_state.get_winner()
    if winner == ai_player:
        return 10 + depth
    if winner == -ai_player:
        return -10 - depth
    return 0


def minimax(
    game_state: GameState,
    depth: int,
    alpha: float,
    beta: float,
    is_max_turn: bool,
    ai_player: int,
    cache: Optional[dict[tuple, int]] = None,
) -> int:
    cache = cache if cache is not None else {}
    key = (game_state.board.tobytes(), game_state.current_player, depth, is_max_turn, ai_player)
    if key in cache:
        return cache[key]
    if depth == 0 or game_state.is_game_over():
        score = evaluate_state(game_state, ai_player, depth)
        cache[key] = score
        return score
    if is_max_turn:
        score = float("-inf")
        completed = True
        for move in game_state.get_available_moves():
            child = game_state.clone()
            child.make_move(*move)
            score = max(score, minimax(child, depth - 1, alpha, beta, False, ai_player, cache))
            alpha = max(alpha, score)
            if beta <= alpha:
                completed = False
                break
        if completed:
            cache[key] = int(score)
        return int(score)
    score = float("inf")
    completed = True
    for move in game_state.get_available_moves():
        child = game_state.clone()
        child.make_move(*move)
        score = min(score, minimax(child, depth - 1, alpha, beta, True, ai_player, cache))
        beta = min(beta, score)
        if beta <= alpha:
            completed = False
            break
    if completed:
        cache[key] = int(score)
    return int(score)


def select_depth(board_size: int, empty_cells: int) -> int:
    if board_size != 3:
        raise ValueError("only 3x3 boards are supported")
    return min(empty_cells, 9)


def find_best_move(game_state: GameState, depth: Optional[int] = None, rng: random.Random | None = None) -> Optional[tuple[int, int]]:
    moves = game_state.get_available_moves()
    if game_state.is_game_over() or not moves:
        return None
    depth = depth or select_depth(game_state.board_size, len(moves))
    ai_player = game_state.current_player
    scored_moves = []
    cache: dict[tuple, int] = {}
    for move in moves:
        child = game_state.clone()
        child.make_move(*move)
        score = minimax(child, depth - 1, float("-inf"), float("inf"), False, ai_player, cache)
        scored_moves.append((move, score))
    best_score = max(score for _, score in scored_moves)
    return (rng or random).choice([move for move, score in scored_moves if score == best_score])


def _minimax_with_tree(game_state: GameState, depth: int, alpha: float, beta: float, is_max_turn: bool, max_depth: int, ai_player: int) -> tuple[int, Node]:
    node = Node(game_state, depth=max_depth - depth, is_max_turn=is_max_turn, alpha=alpha, beta=beta)
    if depth == 0 or game_state.is_game_over():
        node.result = evaluate_state(game_state, ai_player, depth)
        return int(node.result), node
    best = float("-inf") if is_max_turn else float("inf")
    for move in game_state.get_available_moves():
        child_state = game_state.clone()
        child_state.make_move(*move)
        score, child_node = _minimax_with_tree(child_state, depth - 1, alpha, beta, not is_max_turn, max_depth, ai_player)
        child_node.move = move
        node.children.append(child_node)
        if is_max_turn:
            best = max(best, score)
            alpha = max(alpha, score)
        else:
            best = min(best, score)
            beta = min(beta, score)
        if beta <= alpha:
            break
    node.result = best
    return int(best), node


def find_best_move_with_tree(game_state: GameState, ai_player: int, depth: Optional[int] = None) -> tuple[Optional[tuple[int, int]], Optional[Node]]:
    moves = game_state.get_available_moves()
    if game_state.is_game_over() or not moves:
        return None, None
    depth = depth or select_depth(game_state.board_size, len(moves))
    root = Node(game_state, is_max_turn=True)
    best_score = float("-inf")
    best_moves = []
    for move in moves:
        child_state = game_state.clone()
        child_state.make_move(*move)
        score, child_node = _minimax_with_tree(child_state, depth - 1, float("-inf"), float("inf"), False, depth, ai_player)
        child_node.move = move
        root.children.append(child_node)
        if score > best_score:
            best_score, best_moves = score, [move]
        elif score == best_score:
            best_moves.append(move)
    root.result = best_score
    return random.choice(best_moves), root
