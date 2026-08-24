from __future__ import annotations

import argparse
import csv
import json
import shutil
from pathlib import Path

from ai.agent_q_learning import QLearningAgent
from ai.neural import OnnxPolicyAgent
from training.benchmark import benchmark_candidate, save_benchmark
from training.common import ROOT, write_json
from training.dataset import generate_dataset
from training.report import generate_benchmark_report
from training.trainers import train_dqn, train_imitation, train_q_learning, train_reinforce

TRAINERS = {
    "q_learning": train_q_learning,
    "dqn": train_dqn,
    "imitation": train_imitation,
    "reinforce": train_reinforce,
}


def load_run_agent(agent_id: str, run_dir: Path):
    if agent_id == "q_learning":
        agent = QLearningAgent()
        agent.load(run_dir / "model.npz")
        return agent
    return OnnxPolicyAgent(run_dir / "model.onnx")


def train_command(args: argparse.Namespace) -> None:
    seeds = args.seeds or ([42] if args.preset == "smoke" else [42, 43, 44])
    for seed in seeds:
        result = TRAINERS[args.agent](args.preset, seed)
        print(result.run_dir)


def evaluate_command(args: argparse.Namespace) -> None:
    agent = load_run_agent(args.agent, args.run)
    report = benchmark_candidate(agent, games=args.games, seed=args.seed)
    output = args.run / "benchmark.json"
    save_benchmark(report, output)
    print(json.dumps(report, indent=2))


def promote_command(args: argparse.Namespace) -> None:
    report_path = args.run / "benchmark.json"
    if not report_path.exists():
        raise SystemExit("benchmark.json is required; run evaluate first")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if not report.get("promotion_passed"):
        raise SystemExit("model did not meet the promotion thresholds")
    metrics = list(csv.DictReader((args.run / "metrics.csv").open(encoding="utf-8")))
    if args.agent == "imitation" and max(float(row.get("optimal_accuracy") or 0) for row in metrics) < 0.99:
        raise SystemExit("imitation model did not reach 99% optimal-action accuracy")
    if args.agent != "q_learning":
        differences = [float(row["onnx_max_difference"]) for row in metrics if row.get("onnx_max_difference")]
        if not differences or max(differences) > 1e-5:
            raise SystemExit("ONNX export did not meet the 1e-5 equivalence tolerance")
    destination = ROOT / "models" / args.agent
    destination.mkdir(parents=True, exist_ok=True)
    model_name = "model.npz" if args.agent == "q_learning" else "model.onnx"
    shutil.copy2(args.run / model_name, destination / model_name)
    shutil.copy2(report_path, destination / "benchmark.json")
    summary = json.loads((args.run / "summary.json").read_text(encoding="utf-8"))
    metadata = {
        "agent": args.agent,
        "format_version": 1,
        "training_seed": summary["seed"],
        "training_episodes": summary["episodes"],
        "training_config": summary["config"],
        "source_run": args.run.name,
    }
    write_json(destination / "metadata.json", metadata)
    print(destination)


def main() -> None:
    parser = argparse.ArgumentParser(description="Tic-Tac-Toe AI training laboratory")
    subparsers = parser.add_subparsers(dest="command", required=True)

    train_parser = subparsers.add_parser("train")
    train_parser.add_argument("--agent", choices=TRAINERS, required=True)
    train_parser.add_argument("--preset", choices=("smoke", "standard", "full"), default="smoke")
    train_parser.add_argument("--seeds", nargs="*", type=int)
    train_parser.set_defaults(handler=train_command)

    dataset_parser = subparsers.add_parser("generate-dataset")
    dataset_parser.add_argument("--output", type=Path, default=ROOT / "training" / "datasets" / "minimax.npz")
    dataset_parser.set_defaults(handler=lambda args: print(generate_dataset(args.output)[0].shape))

    evaluate_parser = subparsers.add_parser("evaluate")
    evaluate_parser.add_argument("--agent", choices=TRAINERS, required=True)
    evaluate_parser.add_argument("--run", type=Path, required=True)
    evaluate_parser.add_argument("--games", type=int, default=1000)
    evaluate_parser.add_argument("--seed", type=int, default=42)
    evaluate_parser.set_defaults(handler=evaluate_command)

    promote_parser = subparsers.add_parser("promote")
    promote_parser.add_argument("--agent", choices=TRAINERS, required=True)
    promote_parser.add_argument("--run", type=Path, required=True)
    promote_parser.set_defaults(handler=promote_command)

    report_parser = subparsers.add_parser("report")
    report_parser.add_argument("--output", type=Path, default=ROOT / "docs" / "benchmark.md")
    report_parser.set_defaults(handler=lambda args: print(generate_benchmark_report(args.output)))

    args = parser.parse_args()
    args.handler(args)


if __name__ == "__main__":
    main()
