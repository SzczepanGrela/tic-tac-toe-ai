# Limits and streaming acceptance

Reviewed 2026-09-28. These checks supplement the accepted Coolify image release,
health gate and rollback. The private infrastructure checklist owns D04 status;
passing CI does not certify the public proxy path or multiple replicas.

## Automated contracts

Run `python -m pytest tests/test_delivery_behavior.py` with the production
runtime lock and test requirements installed, as in Quality. Jev is disabled;
the tests use Random and controlled local workers, with no provider calls.

| Contract | Evidence in this test module |
| --- | --- |
| Weighted series budget | `/api/matches` and `/api/matches/stream` debit the same client bucket by requested game count; switching paths does not reset it |
| Client and operation separation | Another client retains its own series budget; exhausting series does not exhaust the move bucket or block health |
| Trusted identity | Uvicorn accepts forwarded identity only from the configured trusted peer; forged headers from an untrusted peer do not reset the move budget |
| Refill and concurrency | `Retry-After` matches token refill, independent clients recover independently, simultaneous calls cannot overspend one bucket |
| Work saturation | Two active jobs and eight waiting requests fill the process queue; additional work is rejected while health responds; cancelling a queued request makes room and the queue recovers |
| Incremental delivery | A real HTTP client receives game 1 while game 2 is deliberately blocked; the remaining game and one completion event arrive after release |
| Disconnect | Closing a real stream stops its cooperative worker and prevents later games; subsequent health and game requests work |

The real HTTP tests cover both Uvicorn HTTP backends, `h11` and `httptools`.
In-process ASGI transports collect response bodies and cannot alone establish
event arrival timing or a TCP disconnect. Controlled barriers make the ordering
assertions independent of agent speed; short timeouts bound failed tests.

Before response headers, an exhausted budget or full queue returns HTTP 429
with `Retry-After`. Once a stream has started with HTTP 200, a later queue or
computation failure is an NDJSON `error` event, without a `complete` event.
Consumers must read that protocol outcome, not treat HTTP 200 alone as success.
The existing browser tests preserve completed games after a stopped series.

## Public acceptance procedure

1. Record UTC time, public revision and healthy local agents. Use bounded local
   agent requests; do not include Jev or modify provider rules for this check.
2. Read NDJSON lines as they arrive, recording time for each game and the final
   `complete` event. Use a multi-game MCTS series long enough to distinguish
   incremental delivery from a response collected at the end.
3. Close a second stream after its first game and check health again. This
   proves client-side closure; server-side cancellation through the complete
   proxy chain needs correlated server evidence, separately from the local test.
4. Check weighted exhaustion, `Retry-After` and recovery using small Random
   series. Stop at the first expected rejection; distinguish application JSON
   from an edge response. Remain below the edge request-rate threshold when
   testing the application limiter.
5. While client A has insufficient budget for the chosen operation, use the
   same operation from an independent public IP. Rotating forwarded headers or
   opening a second browser behind the same NAT is not a second identity.
6. Record limits during old/new container overlap and long-running request
   termination separately; do not infer them from a steady-state check.

## Correlating public disconnects with work

An accepted stream returns a server-generated `X-Stream-ID` header. Incoming
values of that header are ignored. The normal Uvicorn INFO log records that
ID with `match_stream` opened/closed events and `stream_work` started/finished
events for each game. Fields contain only that random ID, game counts/numbers,
outcome and elapsed time; they do not include client identity, headers, seed,
board, moves or credentials. Existing Docker log rotation applies.

Capture the header and UTC time, consume the first game from a bounded MCTS
series, then close the client connection. Read only log lines with that exact
ID from the corresponding container. A cancelled stream should stop before
all requested games are computed. Every started game must have a finished
event; a cooperative interruption has `outcome=stopped`. A game that finished
before the disconnect can have `outcome=completed`. Check for later game starts
and check public health after the operation. Do not count a client/stream
closure alone as proof of worker completion: timeout/cancellation may close
HTTP while the worker is still exiting. The worker's own finally block emits
its finished event. Pool threads can remain idle for reuse afterwards.

Local real-HTTP regression tests cover these records, including a timed-out
response whose worker finishes later. Public proxy-chain acceptance still
requires the correlated operator log; adding these events does not close D04.

## Explicit limitations

The move and series buckets, two active work slots and eight waiting places
are per process. Each rolling replica has independent limits. The move bucket
has capacity 10; the series bucket has capacity 30; both refill 0.5 tokens per
second. A stream reserves its requested game count before starting, including
games not completed after disconnect. There is no cancellation refund.

The Jev spending ledger is a separate durable, transactional budget and does
not make these local rate/work limits shared. Redis or another shared counter
store requires a separate decision and failure policy.

Local proxy middleware tests do not establish production forwarding trust,
cross-application edge budgets, or independent public-client isolation. Public
event timing does not establish termination of work behind every proxy after
a disconnect. Keep those acceptance scopes explicit in the dated app record.
