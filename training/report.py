from __future__ import annotations

import json
import os
from pathlib import Path

from training.common import ROOT


def generate_benchmark_report(output: Path = ROOT / "docs" / "benchmark.md") -> Path:
    agents = ("q_learning", "dqn", "imitation", "reinforce")
    opponents = ("random", "rules", "minimax", "mcts")
    reports = {
        agent: json.loads((ROOT / "models" / agent / "benchmark.json").read_text(encoding="utf-8"))
        for agent in agents
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Model benchmark",
        "",
        "The table shows wins/draws/losses and the non-loss rate. Every candidate alternates between X and O.",
        "",
        "| Model | Opponent | Games | W / D / L | Non-loss |",
        "| --- | --- | ---: | ---: | ---: |",
    ]
    for agent in agents:
        for opponent in opponents:
            result = reports[agent]["results"][opponent]
            lines.append(
                f"| {agent} | {opponent} | {result['games']} | "
                f"{result['wins']} / {result['draws']} / {result['losses']} | "
                f"{result['non_loss_rate']:.1%} |"
            )
    lines.extend([
        "",
        "Promotion requires no illegal moves, at least 99% non-loss against Random, and at least 95% against Rules.",
        "The files under `models/` are the promoted runtime artifacts; training runs and checkpoints are not versioned.",
        "",
        "![Non-loss rates](benchmark.png)",
        "",
    ])
    output.write_text("\n".join(lines), encoding="utf-8")

    config_dir = ROOT / "training" / "runs" / ".matplotlib"
    config_dir.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("MPLCONFIGDIR", str(config_dir))
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    x = np.arange(len(opponents))
    width = 0.2
    for index, agent in enumerate(agents):
        values = [reports[agent]["results"][opponent]["non_loss_rate"] for opponent in opponents]
        plt.bar(x + (index - 1.5) * width, values, width, label=agent)
    plt.xticks(x, opponents)
    plt.ylim(0, 1.05)
    plt.ylabel("Non-loss rate")
    plt.legend(fontsize=8)
    plt.tight_layout()
    plt.savefig(output.with_name("benchmark.png"), dpi=160)
    plt.close()
    return output
