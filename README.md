# Tic-Tac-Toe AI Lab

A web-based Tic-Tac-Toe laboratory for playing against and comparing classic search algorithms, tabular reinforcement learning, and neural policies.

The original project was created on GitLab; this repository is its GitHub fork. The original source remains available at [gitlab.com/Tomakao/kolkokrzyzyk](https://gitlab.com/Tomakao/kolkokrzyzyk).

## Agents

- Random baseline
- Rule-based expert system
- Minimax with alpha-beta pruning
- Monte Carlo Tree Search
- Tabular Q-learning
- Deep Q-Network (DQN)
- Minimax imitation network
- REINFORCE policy-gradient network
- TypeSafe Jev (optional remote agent, experimental)

The trained agents are pure policies: no hidden win/block heuristic is applied during inference. Neural networks are trained with PyTorch and deployed as compact ONNX models.

## Web application

The interface supports Human vs AI, local Player vs Player, and AI vs AI series. The focused board sizes are 3×3, 5×5, and 9×9, with a configurable winning line from 3 marks through the board size. A newly selected size defaults to `min(size, 5)` marks in a row. Classic 3×3 series stream completed games; larger-board and Jev series show moves as they are calculated. The board and score update during the series, and a completed or stopped series can be replayed move by move with pause, step, and speed controls. Local agents support seeded series; Jev replays use recorded moves because seeds do not reproduce fresh provider responses. English/Polish language and light/dark theme preferences are stored in first-party cookies.

Random and Rules support every board variant. MCTS supports 3×3 and 5×5
(all three winning lengths); 9×9 MCTS remains disabled. Minimax, Q-learning, DQN,
Imitation, and REINFORCE remain available for classic 3×3 only. The interface
gets this compatibility matrix from `/api/agents` and disables unsupported
choices; the API also rejects an incompatible pair before inference. Existing
learned artifacts are unchanged and were not retrained for this feature.

Jev supports every variant when configured, but is disabled by default. It uses
one paid API call per non-forced move and a shared persistent monthly budget.
See [Jev setup, cost control and recovery](docs/jev-operations.md) before enabling
it. Larger-board and Jev series use one move request at a time; stopping or
failing a series preserves completed games and does not score a partial game.

```bash
git clone https://github.com/SzczepanGrela/tic-tac-toe-ai.git
cd tic-tac-toe-ai
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-web.txt
python -m uvicorn web.app:app --host 127.0.0.1 --port 8080
```

Fish users should activate with `source .venv/bin/activate.fish`. On Windows PowerShell use `.venv\Scripts\Activate.ps1`.

Open `http://127.0.0.1:8080`, or run the container:

```bash
docker build -f infra/Dockerfile -t tic-tac-toe-ai .
docker run --rm -p 127.0.0.1:8080:8080 tic-tac-toe-ai
```

## Training laboratory

Training dependencies are intentionally separate from the production runtime:

```bash
python -m pip install -r requirements-training.txt
python -m training train --agent q_learning --preset standard --seeds 42
python -m training train --agent dqn --preset standard --seeds 42
python -m training train --agent imitation --preset standard --seeds 42
python -m training train --agent reinforce --preset standard --seeds 42
```

Presets are `smoke` for pipeline checks, `standard` for local experiments, and `full` for three-seed production experiments. Every run stores its configuration, checkpoint, metrics, seed, and ONNX validation result under the ignored `training/runs/` directory.

Evaluate and promote a candidate only when it meets the documented quality gates:

```bash
python -m training evaluate --agent dqn --run training/runs/<run> --games 1000
python -m training promote --agent dqn --run training/runs/<run>
```

See [benchmark results](docs/benchmark.md) for the promoted models.

The [variable-board implementation record](docs/variable-board-plan.md) covers
the rules, compatibility contract, safety limits, measurements, validation and
MCTS/model/provider work. The [5×5 MCTS report](docs/mcts-5x5.md) records the
selected simulation budget, latency and separate results against Random/Rules.

## Tests

```bash
python -m pip install -r requirements-dev.txt
pytest
```

Browser tests additionally require `python -m playwright install chromium` and `RUN_E2E=1 pytest tests/test_e2e.py`.

The original desktop application is preserved on the `archive/desktop-original` branch.

## Delivery

Every release is tested once and published to the public GitHub Container Registry with an SBOM and provenance. Production deploys the same immutable image digest that passed Python, training, browser, container, and vulnerability checks. See the [deployment notes](infra/README.md) for the release and rollback contract.

## Authors

- Szczepan Grela
- Tomasz Kosiński ([Tomakao](https://gitlab.com/Tomakao))
- Karolina Broniewska

Licensed under the [MIT License](LICENSE).
