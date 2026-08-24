from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Optional

from game.state import GameState


@dataclass
class MCTSNode:
    game_state: GameState
    parent: Optional["MCTSNode"] = None
    move: Optional[tuple[int, int]] = None
    children: list["MCTSNode"] = field(default_factory=list)
    visits: int = 0
    score: float = 0.0

    def __post_init__(self) -> None:
        self.untried_moves = self.game_state.get_available_moves().copy()

    def ucb(self, exploration: float) -> float:
        if self.visits == 0:
            return float("inf")
        return self.score / self.visits + exploration * math.sqrt(math.log(self.parent.visits) / self.visits)


class MCTSAgent:
    def __init__(self, iterations: int = 1000, exploration: float = math.sqrt(2), rng: random.Random | None = None) -> None:
        self.iterations = iterations
        self.exploration = exploration
        self.rng = rng or random.Random()

    def find_move(self, game_state: GameState) -> Optional[tuple[int, int]]:
        moves = game_state.get_available_moves()
        if game_state.is_game_over() or not moves:
            return None
        if len(moves) == 1:
            return moves[0]
        root = MCTSNode(game_state.clone())
        root_player = game_state.current_player
        for _ in range(self.iterations):
            node = self._select(root)
            result = self._simulate(node.game_state, root_player)
            self._backpropagate(node, result, root_player)
        return max(root.children, key=lambda child: child.visits).move if root.children else self.rng.choice(moves)

    def _select(self, node: MCTSNode) -> MCTSNode:
        while not node.game_state.is_game_over():
            if node.untried_moves:
                move = self.rng.choice(node.untried_moves)
                node.untried_moves.remove(move)
                child_state = node.game_state.clone()
                child_state.make_move(*move)
                child = MCTSNode(child_state, node, move)
                node.children.append(child)
                return child
            node = max(node.children, key=lambda child: child.ucb(self.exploration))
        return node

    def _simulation_move(self, state: GameState) -> tuple[int, int]:
        moves = state.get_available_moves()
        for player in (state.current_player, -state.current_player):
            for move in moves:
                candidate = state.clone()
                candidate.current_player = player
                candidate.make_move(*move)
                if candidate.get_winner() == player:
                    return move
        weighted = []
        for move in moves:
            weighted.extend([move] * (3 if move == (1, 1) else 2 if move in ((0, 0), (0, 2), (2, 0), (2, 2)) else 1))
        return self.rng.choice(weighted)

    def _simulate(self, game_state: GameState, root_player: int) -> float:
        state = game_state.clone()
        while not state.is_game_over():
            state.make_move(*self._simulation_move(state))
        winner = state.get_winner()
        return 1.0 if winner == root_player else 0.0 if winner == -root_player else 0.5

    @staticmethod
    def _backpropagate(node: MCTSNode, result: float, root_player: int) -> None:
        while node is not None:
            node.visits += 1
            player_who_moved = -node.game_state.current_player
            node.score += result if player_who_moved == root_player else 1.0 - result
            node = node.parent


def find_best_move(game_state: GameState, iterations: int = 1000, rng: random.Random | None = None) -> Optional[tuple[int, int]]:
    empty_cells = len(game_state.get_available_moves())
    capped = min(iterations, 500 if empty_cells > 7 else 800 if empty_cells > 4 else 1000)
    return MCTSAgent(capped, rng=rng).find_move(game_state)
