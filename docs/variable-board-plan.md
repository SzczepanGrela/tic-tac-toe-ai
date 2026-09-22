# Variable-board implementation plan

Status: the initial variable-board release was reviewed, merged, and deployed
to production on 2026-09-18. The focused 3×3/5×5/9×9 follow-up was merged as
PR #23 (`0e38480f17b7fd2c17fc0bb5f7a3dc4c58fd0488`) and passed Quality and
protected deployment in run `35506078903`. G05 and the J01 implementation were
merged as PR #24 (`4141e3a69f68d784eed792b8dea9be905d66c720`) and deployed
with Jev disabled. Durable backup support followed in PRs #26 and #27. On
2026-09-21 the production mount, ledger, rolling persistence, off-host backup
and restore procedure passed operator acceptance. Paid provider evaluation and
public Jev activation remain pending. Measurements are development-machine
observations unless explicitly described otherwise.

Product scope update (2026-09-20): after the initial 3×3–10×10 release was
validated, the operator narrowed the public board-size choices to 3×3, 5×5,
and 9×9 for a clearer interface. References below to all 36 variants record
the broader implementation and its historical validation; they do not describe
the current public input contract.

## Original scope and rules (historical first-release plan)

Support every square board size from 3x3 through 10x10 in local play,
human-versus-agent play, and agent-versus-agent series. Availability depends on
the agent's supported rules and measured runtime budget.

- `board_size` (`N`): integer from 3 through 10.
- `win_length` (`K`): integer from 3 through `N`.
- Proposed default: `K = min(N, 5)`; expose an advanced selector for other
  supported values. This default follows the design discussed with the operator.
- X starts. Players alternate placing one mark on an empty cell.
- At least `K` consecutive marks horizontally, vertically, or along either
  diagonal wins. Longer lines also win; special opening rules are out of scope.
- End the game immediately on a win, or declare a draw when the board is full
  without a winner. Do not introduce an early-draw rule in this change.
- Requests without new rule fields retain the existing 3x3, three-in-a-row
  contract. Explicit larger variants must include both rule fields.

The first release needs no newly trained model. Existing Q-learning, DQN,
Imitation and REINFORCE artifacts remain restricted to `(N=3, K=3)`.
New model training and the optional Jev integration have separate gates below.

## Source baseline before implementation

| Area | Observed source behavior | Implication |
| --- | --- | --- |
| [Game state](../game/state.py) | Constructor and winner detection accept `N` and `K`; `from_board` requires 3x3. | Reuse the engine foundation, generalize input validation and terminal handling. |
| [API](../web/app.py) | No rule parameters; series create default states and compute every game before returning; two operations share a semaphore. | Add rule propagation, bounded work, and incremental large-board play. |
| [Agent registry](../ai/registry.py) | One global list without variant capabilities. | Check support before executing an agent; expose capabilities to the browser. |
| [Encoding](../ai/encoding.py) | Board rotation is generic, but move transforms use a fixed 3x3 marker grid. | Keep current learned agents behind a 3x3 capability guard. |
| [Networks](../training/networks.py) | 18 inputs and 9 outputs; only the ONNX batch axis is dynamic. | Existing weights cannot directly consume larger boards. |
| [Training data](../training/dataset.py) | Enumerates nonterminal 3x3 states and solves their continuations. | Do not extend exhaustive generation to large boards. |
| [Frontend](../web/static/app.js), [CSS](../web/static/styles.css) | Fixed indexing, reset, winner geometry and grid for 3x3. | Generalize board rendering, local rules and replay metadata. |

The initial local exploratory measurements timed one opening MCTS move with
seed 42, current default wrapper settings and `K=min(N,5)`: approximately
0.25 s at 3x3, 1.47 s at 4x4, 5.44 s at 5x5 and 34.32 s at 7x7.
These are single samples on a development machine, not latency percentiles or
production capacity evidence. The 10x10 MCTS call did not finish within the
remaining time of the shared 45-second command; it has no usable measurement.

## Agent availability

| Agent | First larger-board release | Follow-up | Training required? |
| --- | --- | --- | --- |
| Random | Every supported `(N,K)` | None beyond correctness tests | No |
| Rules | Every `(N,K)` that passes G04/G06; intended coverage is all variants | General line scoring, tactical tests, performance tuning | No |
| Minimax | Existing 3x3 only | Optional bounded heuristic search, clearly distinguished from exact search | No |
| MCTS | 3×3 and 5×5 K=3/4/5 | Optional separately evaluated 9×9 policy | No |
| Q-learning | Existing 3x3 only | A new table/training experiment would be a separate project | Yes for larger variants |
| DQN | Existing 3x3 only | New architecture, data and evaluation | Yes for larger variants |
| Imitation | Existing 3x3 only | New architecture and scalable teacher | Yes for larger variants |
| REINFORCE | Existing 3x3 only | New architecture and reward/training pipeline | Yes for larger variants |
| Jev | Optional, not a release dependency | Live adapter and domain evaluation under J01 | No local training; playing strength is unverified |

Unsupported selections must be disabled with a short explanation in the UI and
rejected by the API. Do not silently substitute another agent. Unsupported
variants are capability information, not a failed application healthcheck.

## Ordered checklist

IDs remain stable. `local-complete` means the implementation and focused local
validation are complete but says nothing about CI, merge, or production.
G05, J01 and T01 did not block the first release.

| ID | Status | Task | Depends on | Completion condition / evidence |
| --- | --- | --- | --- | --- |
| G01 | complete (2026-09-18) | Rules and engine | none | Strict rules and board validation; all 36 `(N,K)` variants, terminal consistency and original cases covered. |
| G02 | complete (2026-09-18) | API contract and agent capabilities | G01 | Rules propagated; incompatible agents return 422; absent fields retain 3×3; capabilities include revision, policy and work profile. |
| G03 | complete (2026-09-18) | Variable board, controls and incremental series | G02 | Dynamic 3×3–10×10 UI, SVG result marker, keyboard navigation, cancellation, paced incremental series, calculation pause/step and replay covered by browser tests. |
| G04 | complete (2026-09-18) | General Rules agent | G01, G02 | All variants enabled; wins, blocks, centre selection and legal moves tested; 2,160-sample development benchmark passed. |
| G05 | complete (2026-09-20) | MCTS for 5×5 K=3/4/5 | G01, G02, G04 | Fixed 256-simulation profiles passed legal/tactical, seed, resource and >=90% non-loss-vs-Random gates; [measurements](mcts-5x5.md). PR #24 passed CI and protected production deployment with Jev disabled. 9×9 remains disabled. |
| G06 | complete (2026-09-18) | Resource limits and release validation | G03, G04; G05 if included | Queue, cancellation ownership, deadlines, larger smoke, Python 3.12 API suite and production-sized local container passed. |
| G07 | complete (2026-09-18) | Documentation and controlled release | G06 | PRs #21 and #22 merged; protected digest deployment and public 3×3/10×10 smoke passed for revision `c71b9f91ab7b71834a660516ddb2fac770e866ae`. |
| J01 | partial (updated 2026-09-22) | Optional Jev integration | G02, G03, G06 | Adapter/UI, durable spend control, mounted-ledger acceptance and tested off-host recovery are complete. Paid evaluation and public activation remain. See subtasks below. |
| T01 | deferred | Optional learned policies for larger boards | G01, G04, G06 | Architecture, training budget, teacher/rewards and promotion criteria require a separate project. |

### G01 — Rules and engine

- Introduce one immutable rules object or equivalent validated configuration;
  carry it through construction, cloning and every simulation.
- Bound dimensions before allocating arrays or invoking agents. Reject ragged
  boards, size mismatches, invalid marks, coerced fractional/string/boolean
  values, impossible turn counts and conflicting winners.
- Reject moves after terminal states, and do not mutate a state on an invalid
  move. Keep arbitrary-board validation separate from the fast trusted-state
  path used inside search.
- Return winning segment coordinates, including off-centre diagonals and
  segments shorter than the board width. Handle a last move completing several
  lines consistently in Python and JavaScript.
- For supplied terminal boards, check consistency with a possible final move;
  mark counts alone are insufficient to reject all post-win continuations.
- Precompute candidate line segments per `(N,K)`, and check lines through the
  last move during search. Retain a complete scan for incoming arbitrary states.
- Test every one of the 36 supported `(N,K)` pairs with representative legal
  sequences, edges, both diagonal directions, longer-than-K lines and draws
  where reachable. Do not enumerate the full large-board state space.

### G02 — API and capabilities

- Add optional rule fields to move/match requests with the compatibility
  behavior above; echo explicit rules in new responses and game traces.
- Add `GET /api/agents?board_size=N&win_length=K` or an equivalent capability
  response listing supported agents, modes and server-selected work profiles.
- Reject unsupported agent/variant pairs with a stable 422 error before
  inference. Preserve terminal-state 409 and overload 429 semantics.
- Keep the existing 3x3 match endpoint and seed behavior. Reject unapproved
  larger synchronous series before scheduling work; large-series UI uses the
  per-move flow in G03.
- Preserve the existing local-model readiness check. Capability restrictions
  must not make a healthy deployment fail its health gate.
- Cover defaults, explicit variants, bounds, unknown agents and old request
  shapes in API tests. Validate response coordinates and final outcomes.

### G03 — Interface and larger series

- Add size and win-length selectors. Keep 3x3 as the initial experience.
  Changing a rule starts a new game; a configuration cannot change mid-match.
- Drive the grid, indexing, marks, gaps and reset from `N`. Replace fixed
  winning-line CSS with coordinate-based rendering such as an SVG overlay.
- Keep cells usable on narrow screens with a scrollable board when necessary,
  keyboard navigation and accessible coordinate labels.
- Disable unsupported agents, choose a compatible default after rule changes,
  and retain EN/PL parity.
- For larger agent-versus-agent series, request one move at a time through
  `/api/move`. The browser keeps board, progress and replay; the server remains
  stateless. Allow only one outstanding request per series. No background jobs
  or persistent job queue are needed for this release.
- Define stable per-game/per-ply seed derivation for the new path. Store `N`,
  `K`, seed, agent versions/work profile and actual moves in replay metadata.
  Existing 3x3 series retain their current random-number sequence.
- Pause stops requesting new moves; step requests or reveals one move as
  appropriate. Distinguish computing a game from replaying stored moves.
  Stop/reset aborts the client request and ignores any stale response by game
  revision. Rate limits pause the series for `Retry-After`; they must not cause
  a tight retry loop or discard completed games.
- A server timeout or provider error interrupts a game and is reported
  separately from a legitimate win/loss/draw. No automatic replacement move.
- Browser tests cover local 3x3/10x10, off-centre wins, changing rules during a
  request, disabled selections, series completion and replay round trips.

### G04 — Rules agent

- Preserve immediate-win and immediate-block priority. Generalize threat
  detection and score open segments relative to `K`.
- Replace fixed `(1,1)`/3x3 corners with board-relative priorities. Even-sized
  boards have multiple central cells; use the supplied RNG for tied choices.
- Replace repeated full-board clones/scans in fork detection with bounded
  line-based evaluation. Test multiple simultaneous threats and avoid claiming
  that every double threat can be blocked.
- Benchmark early, middle and late positions for every variant. Require legal
  moves, tactical regression coverage and measured performance before enabling
  larger variants in the registry.
- Keep the previous 3x3 policy where practical. If its tie-breaking or search
  changes, version the policy and document new deterministic traces instead of
  promising historical seed equivalence.

Local evidence from 2026-09-18: 108 early/middle/late positions covering all
36 variants were evaluated 20 times each on an AMD Ryzen 7 7700 with Python
3.14.7. Across 2,160 legal move selections, median latency was 0.744 ms, p95 was
4.585 ms and the maximum was 8.243 ms. This is development evidence, not a VPS
latency promise. The classic 3×3 decision path remains separate; the generalized
policy is identified as `rules-v2` with the `line-tactics` work profile.

### G05 — MCTS expansion

- Remove 3x3 rollout coordinates, use cheaper local win checks and reuse safe
  line calculations from G04. Keep tree/work memory explicitly bounded.
- Evaluate candidate reduction near existing marks, preserving immediate wins
  and blocks. Treat pruning as an approximate policy; test it rather than
  assuming omitted moves are irrelevant.
- Use a fixed, versioned work profile per variant for reproducible successful
  seeded runs. A wall-clock stop returning the current best move can produce a
  different answer on different hardware; do not advertise that as seed-exact.
- Add a separate elapsed-time safety deadline checked inside search and rollout
  loops. If it interrupts before the deterministic work finishes, return a
  controlled timeout and do not record a normal completed game.
- Initially keep trees local to a request. Cross-request tree reuse would need
  separate ownership, eviction and memory accounting.
- Enable only evaluated variants. Additional simulations do not automatically
  prove useful playing strength; compare both X and O against Rules/Random and
  tactical fixtures at a recorded work budget.

### G06 — Runtime and validation

- Proposed acceptance targets under a production-sized CPU/memory limit:
  Rules p95 <= 1 s and MCTS p95 <= 2 s per move; safety deadline 5 s per move,
  excluding bounded queue wait. These are initial targets to validate locally,
  not measured promises. Record the corpus, runtime and hardware.
- Bound the waiting queue as well as active work, return 429 with a retry hint
  when saturated, and keep health/static handlers responsive under contention.
- Cancellation must release computation capacity only when the computation
  has actually stopped. A running thread cannot simply be cancelled with
  `Future.cancel()`; await it or use cooperative checks where supported. See the
  [Python executor cancellation contract](https://docs.python.org/3/library/concurrent.futures.html#concurrent.futures.Future.cancel).
- Retain a global active-work limit and enforce server-side operation costs.
  Per-game quotas alone do not bound a 100-ply game. Reassess per-move quotas and
  frontend pacing together; do not bypass limits through the series endpoint.
- Test burst/exhaustion/recovery, concurrent clients, aborted requests and
  deadline exits. Confirm queued/cancelled work cannot accumulate indefinitely.
  Existing in-memory counters are per process/container; account for temporary
  rolling overlap and retain the shared-counter limitation in deployment docs.
- Add parametrized engine/API tests, relevant browser scenarios and container
  smoke for a larger-board move alongside existing 3x3 smoke. Run the existing
  Quality checks; training smoke remains a regression check of the 3x3 pipeline,
  not a promotion or retraining of production artifacts.
- Benchmark performance outside noisy unit-test timing assertions. Keep model
  and agent quality results separate for each `(N,K)` and starting side; the
  existing 3x3 promotion percentages are not universal larger-board thresholds.

Implemented safeguards use two active work slots and at most eight waiting
requests per application process. A full queue returns 429 with `Retry-After`.
One move has a five-second deadline and a classic server-side series has a
30-second deadline. A timed-out worker returns 504, but retains its slot until
the underlying thread actually finishes. A cancelled request likewise waits for
its worker before releasing capacity. The limits are process-local; temporary
rolling overlap therefore has independent counters and queues.

The public move token bucket remains 10 requests of burst capacity and refills
at 0.5 request per second. Larger browser series use one outstanding move at a
time, respect `Retry-After`, retain completed games, and may therefore run
slowly when they exhaust the burst. This is deliberate pacing rather than a
rate-limit bypass. The server-side classic series remains limited to ten games
and a weighted 30-token bucket.

Local evidence currently includes 171 non-API regressions, all 19 API tests in
the Python 3.12 runtime image, 12 browser scenarios, JavaScript/JSON/Python
syntax checks, queue/cancellation/deadline unit tests and the Rules benchmark
above. The production image was healthy with 1 CPU, 512 MiB memory, 128 MiB
reservation and no added capabilities; its internal smoke check covered 3×3 and
10×10 and it used 57.83 MiB in the post-smoke sample. The host's Python 3.14
runtime hangs while closing an executor after ONNX-backed API tests, so the
complete API suite was deliberately repeated in the Python 3.12 image used by
CI and production.

GitHub Actions run `35363309532` passed Browser, Python 3.12, Python 3.13,
Training smoke, Container and Quality gate for PR #21. The deployment job was
correctly skipped for the pull-request event.

### G07 — Delivery and documentation

- Suggested review units: engine/capabilities (G01/G02), Random/UI/bounded
  execution (G03 and relevant G06 controls), Rules (G04), optional MCTS (G05),
  final acceptance/docs (remaining G06/G07). Every exposed expensive path must
  receive its runtime controls in the same change, not in a later PR.
- Ship backward-compatible backend support before enabling the larger-board
  interface. Check old/new replicas and cached frontend assets during rolling
  updates; an old backend must not receive newly enabled variants unnoticed.
  Re-fetch capabilities and show a recoverable message on version mismatch.
- Continue the existing Quality -> protected production -> tested image digest
  -> Coolify health/rolling update -> public smoke/revision verification flow.
  The implementation plan does not itself trigger a production release.
- Verify legal larger-board moves and the original 3x3 path after release.
  Keep the previous digest available. After rollback, new games refresh server
  capabilities; an in-progress larger variant may need to be restarted.
- Update this checklist after each accepted stage. Add the support matrix and
  rules to README and benchmark docs. Reconcile public roadmap entries and the
  private infrastructure notes at release milestones using actual evidence;
  this feature does not close unrelated delivery/platform tasks.

## Separate optional work

**J01 — Jev:** implementation details, operator commands and recovery are in
[Jev operations](jev-operations.md). Local readiness excludes this optional
service. There is no cache, hidden local fallback or automatic provider retry.
The $1 UTC-month ledger includes a $0.25 evaluation sublimit; reservations are
shared transactionally across processes and retained after uncertain calls.

| Subtask | Status on 2026-09-22 | Dependency and completion condition |
| --- | --- | --- |
| G05.1 | complete | 5×5 policy, tactical/seed tests and production-sized benchmark passed; all three K profiles are enabled. |
| G05.2 | complete | PR #24 passed the normal CI and protected digest deployment flow. The production release supports the focused 3×3/5×5/9×9 contract and shipped with Jev disabled. |
| J01.1 | complete | SDK adapter, capabilities, incremental series, cancellation, shared ledger, private evaluator and mocked/API/browser tests implemented and released in PR #24. |
| J01.2 | complete | The runtime-only key, shared host directory, version-1 ledger, tariff reconciliation, rolling-mount readback, daily local/R2 backup and isolated restore/reconcile drill passed on the VPS. The script and independent R2 bucket lifecycle rule both enforce 30-day remote retention. |
| J01.3 | paid evaluation in progress | J01.2; two approved runs stopped before covering all variants. The probability-sum boundary fix deployed as `fb6806f`; review completed private results and evaluate remaining variants selectively within the shared budget. Confirm every variant's legality/tactics/latency and both sides against Random/Rules. Weak play can remain experimental. |
| J01.4 | pending operator activation | G05.2, J01.2, J01.3; enable optional agent, verify human/series paths, disabled/error behavior and retained ledger after recreation/rollback. |

Local evidence: 198 Python 3.12 tests, 15 browser scenarios and image smoke
passed. Cases include simultaneous queue bursts, missing/paused accounting,
two-process budget contention, process crash, month rollover, backup/reconcile,
upstream failures without retries, disconnect cancellation, and completed-game
replay after stop/error. SDK tests mock HTTP; that implementation stage made no
paid calls or VPS mutations. The game's local policies remain stateless;
enabling Jev introduces persistent accounting and therefore required the
separate J01.2 production acceptance recorded below.

Production acceptance on 2026-09-21 used the non-root image user and a shared
read/write bind mount at `/var/lib/tictactoe/jev`. The mode-0700 host directory
and mode-0600 database were owned by UID/GID 10001. A rolling replacement kept
the mount and initialized ledger, and a container write probe succeeded. The
ledger was reconciled to verified zero provider spend under tariff
`jev-1.13.0:0.042/M`, while `JEV_ENABLED=false` prevented paid application
calls. PRs #26 and #27 added the backup job and R2 compatibility controls.
rclone 1.75.1 created and read back a 20,480-byte paused copy with a matching
SHA-256 digest. Restoring that object to an isolated path failed closed while
paused, resumed only after explicit reconciliation, and did not alter the live
ledger. The test copy was removed. The verified backup script was installed and
its systemd timer enabled for 02:20 UTC daily with up to five minutes of jitter.

**T01 — Larger learned policies:** choose separate models per variant or a
spatial network trained on multiple sizes. Supply rule information and mask
illegal/padded cells. Replace exhaustive minimax labels with a scalable teacher
or self-play, and include symmetry, held-out positions and per-variant evaluation.
Budget training before starting it. The current REINFORCE implementation also
uses solver-labelled states/rewards, so increasing its input size alone does not
solve the data dependency. Keep the original 3x3 artifacts and results intact.

## First release definition of done

G01-G04 and G06-G07 are complete with evidence. Every size 3-10 supports local
play, Random and the accepted generalized Rules policy in all three play modes.
Unsupported agent/rule combinations fail clearly. Original 3x3 models still
work, larger games produce valid replay/outcomes, and bounded concurrent load
does not compromise application readiness. Jev and new training remain
independently tracked follow-ups; the later G05 release added accepted 5×5 MCTS.

The initial release definition was satisfied on 2026-09-18. The later product
decision to expose only 3×3, 5×5, and 9×9 does not invalidate the broader
engine evidence; it narrows the supported public input contract.
