"""Bounded 5x5 MCTS. The classic 3x3 policy lives in mcts.py."""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from functools import lru_cache

from ai.execution import check_search
from game.state import GameRules, GameState


POLICY_VERSION = "mcts-5x5-v1"
# Fixed work, never a wall-clock-dependent best-so-far answer.
PROFILES = {3: 256, 4: 256, 5: 256}
FULL = (1 << 25) - 1


def bits(mask: int) -> list[int]:
    result = []
    while mask:
        bit = mask & -mask
        result.append(bit)
        mask ^= bit
    return result


@lru_cache(maxsize=3)
def line_masks(length: int) -> tuple[int, ...]:
    state = GameState(rules=GameRules(5, length))
    return tuple(sum(1 << (r * 5 + c) for r, c in line) for line in state.get_line_segments())


def winning_moves(own: int, other: int, lines: tuple[int, ...]) -> int:
    result = 0
    occupied = own | other
    for line in lines:
        if line & other:
            continue
        empty = line & ~occupied
        if empty and not empty & (empty - 1):
            result |= empty
    return result


def has_won(own: int, lines: tuple[int, ...]) -> bool:
    return any(own & line == line for line in lines)


@dataclass(slots=True)
class Node:
    # Boards are relative to the player to move. Values belong to the parent.
    own: int
    other: int
    move: int = 0
    terminal: bool = False
    untried: list[int] = field(default_factory=list)
    children: list["Node"] = field(default_factory=list)
    visits: int = 0
    score: float = 0.0


def make_node(own: int, other: int, lines: tuple[int, ...], move: int = 0) -> Node:
    terminal = has_won(other, lines) or (own | other) == FULL
    return Node(own, other, move, terminal, [] if terminal else bits(FULL & ~(own | other)))


def rollout(own: int, other: int, lines: tuple[int, ...], rng: random.Random) -> float:
    """Return reward for the player to move at the start of this rollout."""
    sign = 1
    while True:
        check_search()
        if has_won(other, lines):
            return 0.0 if sign == 1 else 1.0
        empty = FULL & ~(own | other)
        if not empty:
            return 0.5
        candidates = winning_moves(own, other, lines) or winning_moves(other, own, lines)
        if candidates:
            move = rng.choice(bits(candidates))
        else:
            choices = bits(empty)
            # Prefer central cells without omitting any legal continuation.
            weights = [5 - max(abs((b.bit_length() - 1) // 5 - 2),
                               abs((b.bit_length() - 1) % 5 - 2)) for b in choices]
            move = rng.choices(choices, weights=weights, k=1)[0]
        own, other = other, own | move
        sign = -sign


def find_best_move(state: GameState, rng: random.Random, *, iterations: int | None = None) -> tuple[int, int] | None:
    if state.board_size != 5:
        raise ValueError("This search supports 5x5 only")
    check_search()
    if state.is_game_over():
        return None
    lines = line_masks(state.win_length)
    own = other = 0
    for r in range(5):
        for c in range(5):
            value = int(state.board[r, c])
            if value == state.current_player:
                own |= 1 << (r * 5 + c)
            elif value == -state.current_player:
                other |= 1 << (r * 5 + c)
    forced = winning_moves(own, other, lines) or winning_moves(other, own, lines)
    if forced:
        return divmod(rng.choice(bits(forced)).bit_length() - 1, 5)
    root = make_node(own, other, lines)
    if len(root.untried) == 1:
        return divmod(root.untried[0].bit_length() - 1, 5)
    count = PROFILES[state.win_length] if iterations is None else iterations
    if count not in (256, 512, 1024, 2048):
        raise ValueError("Unsupported simulation profile")
    for _ in range(count):
        check_search()
        node = root
        path = [root]
        while not node.terminal:
            if node.untried:
                move = node.untried.pop(rng.randrange(len(node.untried)))
                child = make_node(node.other, node.own | move, lines, move)
                node.children.append(child)
                node = child
                path.append(node)
                break
            log_visits = math.log(node.visits)
            node = max(node.children, key=lambda c: c.score / c.visits + math.sqrt(2 * log_visits / c.visits))
            path.append(node)
        result = rollout(node.own, node.other, lines, rng)
        for visited in reversed(path):
            result = 1 - result
            visited.visits += 1
            visited.score += result
    chosen = max(root.children, key=lambda c: (c.visits, c.score / c.visits)).move
    return divmod(chosen.bit_length() - 1, 5)
