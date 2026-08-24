from __future__ import annotations

import random
from copy import deepcopy
from collections import deque

import numpy as np
import torch
from torch import nn

from ai.agent_q_learning import QLearningAgent
from ai.encoding import encode_board
from ai.minimax import find_best_move as find_minimax_move
from ai.random_player import find_random_move
from ai.rules import find_best_move as find_rules_move
from game.state import GameState
from training.common import TrainingResult, create_run, seed_everything, write_run_summary
from training.dataset import generate_dataset
from training.networks import PolicyNetwork, export_onnx

PRESETS = {
    "smoke": {"q_learning": 500, "dqn": 1_000, "imitation": 3, "reinforce": 1_000},
    "standard": {"q_learning": 100_000, "dqn": 20_000, "imitation": 100, "reinforce": 1_000_000},
    "full": {"q_learning": 500_000, "dqn": 250_000, "imitation": 200, "reinforce": 5_000_000},
}


def train_q_learning(preset: str, seed: int) -> TrainingResult:
    episodes = PRESETS[preset]["q_learning"]
    rng = seed_everything(seed)
    run_dir = create_run("q_learning", seed)
    agent = QLearningAgent()
    metrics = []
    for episode in range(episodes):
        state = GameState()
        epsilon = max(0.02, 1.0 - episode / max(1, episodes * 0.8))
        while not state.is_game_over():
            board = state.board.copy()
            player = state.current_player
            move = agent.select_move(state, rng, epsilon)
            state.make_move(*move)
            winner = state.get_winner()
            reward = 1.0 if winner == player else 0.0
            agent.update_transition(board, player, move, reward, state, winner is not None)
        if (episode + 1) % max(1, episodes // 20) == 0:
            metrics.append({"episode": episode + 1, "epsilon": epsilon, "q_values": len(agent.q_table)})
    checkpoint = run_dir / "model.npz"
    agent.save(checkpoint)
    result = TrainingResult("q_learning", seed, episodes, run_dir, checkpoint)
    write_run_summary(result, {"preset": preset, "learning_rate": 0.25, "discount_factor": 0.99, "epsilon_final": 0.02}, metrics)
    return result


def _masked_max(values: torch.Tensor, legal_mask: torch.Tensor) -> torch.Tensor:
    return values.masked_fill(~legal_mask, -1e9).max(dim=1).values


def train_dqn(preset: str, seed: int) -> TrainingResult:
    episodes = PRESETS[preset]["dqn"]
    rng = seed_everything(seed)
    run_dir = create_run("dqn", seed)
    network, target = PolicyNetwork(), PolicyNetwork()
    target.load_state_dict(network.state_dict())
    optimizer = torch.optim.Adam(network.parameters(), lr=1e-3)
    replay: deque = deque(maxlen=50_000)
    metrics = []
    losses = []
    batch_size = 128
    for episode in range(episodes):
        state = GameState()
        epsilon = max(0.05, 1.0 - episode / max(1, episodes * 0.8))
        while not state.is_game_over():
            features = encode_board(state.board, state.current_player)
            moves = state.get_available_moves()
            if rng.random() < epsilon:
                move = rng.choice(moves)
            else:
                with torch.no_grad():
                    scores = network(torch.from_numpy(features).unsqueeze(0))[0]
                move = max(moves, key=lambda item: float(scores[item[0] * 3 + item[1]]))
            player = state.current_player
            state.make_move(*move)
            winner = state.get_winner()
            next_features = encode_board(state.board, state.current_player)
            legal = np.zeros(9, dtype=bool)
            for row, column in state.get_available_moves():
                legal[row * 3 + column] = True
            replay.append((features, move[0] * 3 + move[1], 1.0 if winner == player else 0.0, next_features, legal, winner is not None))
            if len(replay) >= batch_size:
                batch = rng.sample(list(replay), batch_size)
                states = torch.from_numpy(np.stack([item[0] for item in batch]))
                actions = torch.tensor([item[1] for item in batch])
                rewards = torch.tensor([item[2] for item in batch], dtype=torch.float32)
                next_states = torch.from_numpy(np.stack([item[3] for item in batch]))
                legal_mask = torch.from_numpy(np.stack([item[4] for item in batch]))
                done = torch.tensor([item[5] for item in batch], dtype=torch.bool)
                predicted = network(states).gather(1, actions[:, None]).squeeze(1)
                with torch.no_grad():
                    next_value = _masked_max(target(next_states), legal_mask)
                    next_value = torch.where(done, torch.zeros_like(next_value), next_value)
                    expected = rewards - 0.99 * next_value
                loss = nn.functional.smooth_l1_loss(predicted, expected)
                optimizer.zero_grad()
                loss.backward()
                nn.utils.clip_grad_norm_(network.parameters(), 5.0)
                optimizer.step()
                losses.append(float(loss.detach()))
        if (episode + 1) % 500 == 0:
            target.load_state_dict(network.state_dict())
        if (episode + 1) % max(1, episodes // 20) == 0:
            metrics.append({"episode": episode + 1, "epsilon": epsilon, "loss": float(np.mean(losses[-1000:])) if losses else 0.0})
    checkpoint = run_dir / "model.pt"
    torch.save(network.state_dict(), checkpoint)
    onnx_path = run_dir / "model.onnx"
    difference = export_onnx(network, onnx_path)
    metrics.append({"episode": episodes, "epsilon": epsilon, "loss": metrics[-1]["loss"] if metrics else 0.0, "onnx_max_difference": difference})
    result = TrainingResult("dqn", seed, episodes, run_dir, checkpoint, onnx_path)
    write_run_summary(result, {"preset": preset, "architecture": "18-128-128-9", "learning_rate": 0.001, "discount_factor": 0.99, "replay_size": 50000, "target_update": 500}, metrics)
    return result


def train_imitation(preset: str, seed: int) -> TrainingResult:
    epochs = PRESETS[preset]["imitation"]
    seed_everything(seed)
    run_dir = create_run("imitation", seed)
    features, targets = generate_dataset(run_dir / "dataset.npz")
    x, y = torch.from_numpy(features), torch.from_numpy(targets)
    network = PolicyNetwork()
    optimizer = torch.optim.Adam(network.parameters(), lr=1e-3)
    metrics = []
    for epoch in range(epochs):
        permutation = torch.randperm(len(x))
        losses = []
        for start in range(0, len(x), 256):
            indices = permutation[start:start + 256]
            logits = network(x[indices])
            loss = -(y[indices] * torch.log_softmax(logits, dim=1)).sum(dim=1).mean()
            optimizer.zero_grad()
            loss.backward()
            optimizer.step()
            losses.append(float(loss.detach()))
        with torch.no_grad():
            predictions = network(x).argmax(dim=1)
            optimal_accuracy = float((y[torch.arange(len(y)), predictions] > 0).float().mean())
        metrics.append({"epoch": epoch + 1, "loss": float(np.mean(losses)), "optimal_accuracy": optimal_accuracy})
        if optimal_accuracy >= 0.999:
            break
    checkpoint = run_dir / "model.pt"
    torch.save(network.state_dict(), checkpoint)
    onnx_path = run_dir / "model.onnx"
    difference = export_onnx(network, onnx_path)
    metrics[-1]["onnx_max_difference"] = difference
    result = TrainingResult("imitation", seed, epoch + 1, run_dir, checkpoint, onnx_path)
    write_run_summary(result, {"preset": preset, "states": len(features), "architecture": "18-128-128-9", "learning_rate": 0.001}, metrics)
    return result


def train_reinforce(preset: str, seed: int) -> TrainingResult:
    episodes = PRESETS[preset]["reinforce"]
    seed_everything(seed)
    run_dir = create_run("reinforce", seed)
    network = PolicyNetwork()
    optimizer = torch.optim.Adam(network.parameters(), lr=5e-4, weight_decay=1e-5)
    feature_array, target_array = generate_dataset()
    features = torch.from_numpy(feature_array)
    optimal_mask = torch.from_numpy(target_array > 0)
    legal_mask = features[:, :9].eq(0) & features[:, 9:].eq(0)
    baseline = 0.0
    best_score = (float("-inf"), float("-inf"))
    best_state = deepcopy(network.state_dict())
    metrics = []
    batch_size = 256
    batches = max(1, episodes // batch_size)
    evaluation_interval = max(1, batches // 20)
    for batch_number in range(batches):
        indices = torch.randint(len(features), (batch_size,))
        logits = network(features[indices]).masked_fill(~legal_mask[indices], -1e9)
        distribution = torch.distributions.Categorical(logits=logits)
        actions = distribution.sample()
        rewards = torch.where(optimal_mask[indices, actions], 1.0, -4.0)
        progress = batch_number / max(1, batches - 1)
        entropy_weight = 0.02 - 0.019 * progress
        loss = (-((rewards - baseline) * distribution.log_prob(actions)) - entropy_weight * distribution.entropy()).mean()
        optimizer.zero_grad()
        loss.backward()
        nn.utils.clip_grad_norm_(network.parameters(), 5.0)
        optimizer.step()
        baseline = 0.99 * baseline + 0.01 * float(rewards.mean())
        if (batch_number + 1) % evaluation_interval == 0 or batch_number + 1 == batches:
            decisions = min(episodes, (batch_number + 1) * batch_size)
            evaluation_score = _quick_policy_score(network, seed + decisions)
            with torch.no_grad():
                predictions = network(features).masked_fill(~legal_mask, -1e9).argmax(dim=1)
                optimal_accuracy = float(optimal_mask[torch.arange(len(features)), predictions].float().mean())
            checkpoint_score = (optimal_accuracy, evaluation_score)
            if checkpoint_score > best_score:
                best_score = checkpoint_score
                best_state = deepcopy(network.state_dict())
            metrics.append({
                "episode": decisions,
                "loss": float(loss.detach()),
                "baseline": baseline,
                "evaluation_score": evaluation_score,
                "optimal_accuracy": optimal_accuracy,
                "entropy_weight": entropy_weight,
            })
    network.load_state_dict(best_state)
    # A common positive scale preserves every argmax while keeping float32 ONNX
    # output comfortably inside the export equivalence tolerance.
    with torch.no_grad():
        output_layer = network.layers[-1]
        output_layer.weight.mul_(0.25)
        output_layer.bias.mul_(0.25)
    checkpoint = run_dir / "model.pt"
    torch.save(network.state_dict(), checkpoint)
    onnx_path = run_dir / "model.onnx"
    difference = export_onnx(network, onnx_path)
    metrics[-1]["onnx_max_difference"] = difference
    result = TrainingResult("reinforce", seed, episodes, run_dir, checkpoint, onnx_path)
    write_run_summary(result, {
        "preset": preset,
        "architecture": "18-128-128-9",
        "learning_rate": 0.0005,
        "weight_decay": 0.00001,
        "entropy_weight": {"initial": 0.02, "final": 0.001},
        "reward": "1 for an optimal solver move, -4 otherwise",
        "training_states": len(features),
        "batch_size": batch_size,
        "checkpoint_selection": "best quick evaluation",
        "export_logit_scale": 0.25,
    }, metrics)
    return result


def _quick_policy_score(network: PolicyNetwork, seed: int) -> float:
    """Cheap deterministic checkpoint score; the full benchmark remains authoritative."""
    rng = random.Random(seed)
    results = {"random": [0, 0, 0], "rules": [0, 0, 0]}
    network.eval()
    for opponent_name, opponent_move in (("random", find_random_move), ("rules", find_rules_move)):
        for game_number in range(100):
            state = GameState()
            policy_player = 1 if game_number % 2 == 0 else -1
            while not state.is_game_over():
                if state.current_player == policy_player:
                    features = torch.from_numpy(encode_board(state.board, state.current_player)).unsqueeze(0)
                    with torch.no_grad():
                        scores = network(features)[0]
                    moves = state.get_available_moves()
                    move = max(moves, key=lambda item: float(scores[item[0] * 3 + item[1]]))
                else:
                    move = opponent_move(state, rng)
                state.make_move(*move)
            winner = state.get_winner()
            outcome = 0 if winner is None else 1 if winner == policy_player else -1
            results[opponent_name][outcome + 1] += 1
    network.train()
    random_loss, random_draw, random_win = results["random"]
    rules_loss, rules_draw, rules_win = results["rules"]
    return (random_win + random_draw - 2 * random_loss) + 2 * (rules_win + rules_draw - 2 * rules_loss)
