from __future__ import annotations

import random
from pathlib import Path

import numpy as np
import torch
from torch import nn

from ai.encoding import encode_board
from game.state import GameState


class PolicyNetwork(nn.Module):
    def __init__(self) -> None:
        super().__init__()
        self.layers = nn.Sequential(
            nn.Linear(18, 128),
            nn.ReLU(),
            nn.Linear(128, 128),
            nn.ReLU(),
            nn.Linear(128, 9),
        )

    def forward(self, features: torch.Tensor) -> torch.Tensor:
        return self.layers(features)


class TorchPolicyAgent:
    def __init__(self, network: PolicyNetwork) -> None:
        self.network = network.eval()

    def select_move(self, state: GameState, rng: random.Random) -> tuple[int, int] | None:
        moves = state.get_available_moves()
        if not moves:
            return None
        features = torch.from_numpy(encode_board(state.board, state.current_player)).unsqueeze(0)
        with torch.no_grad():
            scores = self.network(features)[0]
        return max(moves, key=lambda move: (float(scores[move[0] * 3 + move[1]]), -(move[0] * 3 + move[1])))


def export_onnx(network: PolicyNetwork, output: Path) -> float:
    output.parent.mkdir(parents=True, exist_ok=True)
    network = network.cpu().eval()
    sample = torch.zeros((1, 18), dtype=torch.float32)
    torch.onnx.export(
        network,
        sample,
        output,
        input_names=["board"],
        output_names=["scores"],
        dynamic_axes={"board": {0: "batch"}, "scores": {0: "batch"}},
        opset_version=17,
        dynamo=False,
    )
    import onnxruntime as ort

    session = ort.InferenceSession(str(output), providers=["CPUExecutionProvider"])
    samples = torch.randn((16, 18), dtype=torch.float32)
    with torch.no_grad():
        expected = network(samples).numpy()
    actual = session.run(None, {session.get_inputs()[0].name: samples.numpy()})[0]
    return float(np.max(np.abs(expected - actual)))
