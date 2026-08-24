from __future__ import annotations

import csv
import json
import os
import random
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import torch

ROOT = Path(__file__).resolve().parents[1]
RUNS_DIR = ROOT / "training" / "runs"


@dataclass
class TrainingResult:
    agent: str
    seed: int
    episodes: int
    run_dir: Path
    checkpoint: Path
    onnx_path: Path | None = None


def seed_everything(seed: int) -> random.Random:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    return random.Random(seed)


def create_run(agent: str, seed: int) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = RUNS_DIR / f"{agent}-{timestamp}-seed{seed}"
    run_dir.mkdir(parents=True, exist_ok=False)
    return run_dir


def write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_metrics(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    with path.open("w", newline="", encoding="utf-8") as handle:
        fieldnames = sorted({key for row in rows for key in row})
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_run_summary(result: TrainingResult, config: dict, metrics: list[dict]) -> None:
    payload = asdict(result)
    payload["run_dir"] = str(result.run_dir)
    payload["checkpoint"] = str(result.checkpoint)
    payload["onnx_path"] = str(result.onnx_path) if result.onnx_path else None
    payload["config"] = config
    write_json(result.run_dir / "summary.json", payload)
    write_metrics(result.run_dir / "metrics.csv", metrics)
    if metrics:
        config_dir = result.run_dir / ".matplotlib"
        config_dir.mkdir(exist_ok=True)
        os.environ.setdefault("MPLCONFIGDIR", str(config_dir))
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        x_key = "episode" if "episode" in metrics[0] else "epoch"
        numeric_keys = [key for key in metrics[0] if key not in (x_key, "curriculum")]
        for key in numeric_keys:
            points = [(row[x_key], row[key]) for row in metrics if key in row]
            if points:
                plt.plot([point[0] for point in points], [point[1] for point in points], label=key)
        plt.xlabel(x_key)
        plt.legend()
        plt.tight_layout()
        plt.savefig(result.run_dir / "training.png", dpi=150)
        plt.close()
