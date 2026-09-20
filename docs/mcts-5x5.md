# MCTS on 5×5

Observed locally on 2026-09-20, working tree `feature/mcts-jev`, based on
`0e38480f17b7fd2c17fc0bb5f7a3dc4c58fd0488`. This is local qualification,
not evidence of a production deployment.

`mcts-5x5-v1` uses bitboards, cached winning-line masks, win/block rollout
priorities and centre-weighted sampling. Every legal cell remains a candidate.
The tree is request-local and expands by at most one node per simulation:
256 simulations mean at most 257 nodes, each with at most 25 legal actions.
There is no cross-request tree cache or newly trained model. Classic 3×3 MCTS
retains its algorithm and seeded move sequence; cooperative cancellation checks
do not consume random numbers. MCTS on 9×9 is not enabled.

The minimum tested profile, **256 simulations for each K**, passed the accepted
90% non-loss threshold against Random separately for X and O. Higher profiles
(512/1024/2048) were therefore not needed for this gate. A five-second safety
deadline or client disconnect aborts the search; it never returns a variable-work
best-so-far move as a successful deterministic result.

## Measurements

Python 3.12.14 in the application image on a development AMD Ryzen 7 7700 host,
Docker limit 1 CPU / 512 MiB / no extra swap, cap-drop ALL and init. These are
not VPS measurements. Reproduce with:

```bash
python -m benchmarks.mcts --games 100 --iterations 256
```

The runner in [benchmarks/mcts.py](../benchmarks/mcts.py) uses random legal
playouts (seeds 20000–20019) and samples plies 0/4/10/16/22 when still active.
Repeated opening positions are retained. Latency covers one legal move per
position, seeded by corpus index; p95 is the sorted sample at floor(0.95×(n−1)).
Games use seeds 10000–10099 per side/opponent. W/D/L are from MCTS's perspective.

| K | Positions | Median | p95 | Maximum | Random as X W/D/L | Random as O W/D/L |
| --- | ---: | ---: | ---: | ---: | --- | --- |
| 3 | 55 | 6.15 ms | 19.41 ms | 20.13 ms | 100/0/0 | 99/0/1 |
| 4 | 77 | 19.66 ms | 36.58 ms | 37.16 ms | 100/0/0 | 100/0/0 |
| 5 | 95 | 14.54 ms | 34.00 ms | 34.56 ms | 67/33/0 | 61/39/0 |

| K | Rules as X W/D/L | Rules as O W/D/L |
| --- | --- | --- |
| 3 | 100/0/0 | 0/0/100 |
| 4 | 6/26/68 | 0/7/93 |
| 5 | 0/100/0 | 0/100/0 |

Passing the Random threshold does not mean MCTS dominates Rules. Starting side
and winning length materially affect results. These finite samples do not prove
optimal play. API tests cover all K, legal moves, metadata and seed repeatability;
tactical tests cover immediate wins/blocks, off-centre lines and cancellation.

The final image also passed full smoke and a local HTTP check with two concurrent
5×5 K=4 moves (`python -m benchmarks.runtime`): 83.46/83.04 ms per move, three
successful health samples, maximum 41.08 ms. The container's cgroup memory peak
through smoke and this sample was 83,689,472 bytes (79.81 MiB). This small
concurrency sample verifies responsiveness; it is not a sustained-load percentile.
