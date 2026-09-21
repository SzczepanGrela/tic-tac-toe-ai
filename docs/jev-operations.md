# Optional Jev agent

Implementation and local validation dated 2026-09-20. No production activation
or paid provider evaluation has been performed by this change. The operator
approves deployment, runtime credentials/storage and paid evaluation separately.
MCTS can ship first with `JEV_ENABLED=false` (the default).

## Contract and availability

The official `typesafe-sdk==0.7.0` async client uses pinned model `jev-1.13.0`
and prompt `jev-game-v2`. The structured state names the board size, required
line length, side to move, opponent, complete board, cell symbols and 1-based
coordinates. The structured Choice instructions define the game, first-win
termination, result preference, adversarial opponent and tactical priorities.
Its criteria contain every legal empty cell and identify the mark being placed;
facts are not duplicated as an interpolated text board. Supports 3×3 K=3, 5×5 K=3–5,
9×9 K=3–9. At most 81 options fit the provider's 255-option contract.
There is one request per non-forced move, zero SDK retries and no response cache.
A single legal cell needs no provider call. There is no local-agent fallback.
Returned model, legal move, probability keys/values and confidence are validated;
confidence is not a measured probability of winning the game.

The adapter has two active slots and four waiting requests per process, with
a two-second queue timeout and five-second operation timeout. Transport errors
start a 60-second process-local cooldown. Rolling overlap can temporarily double
concurrency, but both replicas share the spend ledger. Client disconnect cancels
network I/O; an uncertain call remains reserved. Cancellation cannot guarantee
that a provider has stopped processing an already submitted request.

`/api/agents` exposes availability/reason, policy, work profile, incremental
series mode and `seed_reproducible=false`. Responses contain move metadata with
model, prompt and server revision. The UI marks Jev experimental. Jev series,
including 3×3, call `/api/move` incrementally; `/api/matches` rejects Jev.
Replay uses recorded moves. Provider/budget failure or Stop preserves completed
games and their score; an interrupted game is not counted as a win/loss/draw.
Normal application 429 pacing also applies; upstream errors are not retried.

| Condition | Public behavior |
| --- | --- |
| Disabled / missing key | Jev unavailable: `disabled` / `not_configured` |
| Missing, invalid, locked or paused ledger | Jev unavailable: `accounting_unavailable` |
| Insufficient reserve for another call | Jev unavailable: `budget_exhausted` |
| Provider failure / timeout / malformed response | Explicit error, no replacement move |
| Optional service unavailable | Local agents and `/api/health` stay available |

## Cost accounting

Approved application tariff: **$0.042 per million input tokens**, free output,
64K context; integer 42 nano-USD per input token. Before each network call, an
SQLite transaction reserves **65,536 tokens = $0.002752512**. Known valid usage
settles the reservation at actual input cost. Missing usage, timeout, cancellation
or process death keeps the full amount. Settlement is idempotent. Invalid usage
cannot release the reservation. A different returned model pauses the ledger.

The shared cap is **$1 per calendar UTC month**, including tests, across rolling
replicas. Evaluations have a **$0.25 monthly sublimit inside that $1**, not an
additional allowance. Budget is checked against the next full reservation, so
availability can end slightly before $1. Calls belong to their reservation's UTC
month, including calls completed across midnight. The ledger stores monthly
usage indefinitely; re-creation, rollback and a new key must not reset it.

The cap is an application accounting guarantee under the approved tariff, not a
provider invoice guarantee if prices change. Verify the live tariff before
activation or a model upgrade; a model pin alone cannot freeze prices. Use a
dedicated provider key/account boundary where available; spending outside this
ledger is not automatically discovered. Do not share it with unrelated clients.

## Runtime setup — operator actions

| Coolify runtime setting | Value |
| --- | --- |
| `JEV_ENABLED` | `false` until acceptance, then `true` |
| `TYPESAFE_API_KEY` | Secret stored in Coolify, runtime only, buildtime disabled |
| `JEV_USAGE_DB` | `/var/lib/tictactoe/jev/usage.sqlite3` (default) |
| Storage | One persistent **directory** at `/var/lib/tictactoe/jev`, shared by old/new replicas |
| Owner / permissions | Image UID/GID 10001; directory 0700, database and backups 0600 |

Mount the directory, not just the file: SQLite needs its rollback journal beside
the database. Use a same-host local filesystem with working file locks; not NFS,
independent per-replica volumes, ephemeral container layers or a read-only mount.
SQLite uses DELETE journaling, FULL synchronization and a 200 ms lock wait.
The application opens an existing version-1 ledger only; it never creates one
automatically. Coolify storage/env readback is separate from the release JSON
contract, which currently does not verify mounts or secrets.

On the first activation, create the empty ledger as the runtime user using the
new image's `python -m web.jev_admin init`. This command refuses to overwrite
existing data and leaves the ledger paused. After confirming the provider's
current monthly spend, resume with:

```bash
python -m web.jev_admin reconcile --spent-usd 0 --confirm-tariff jev-1.13.0:0.042/M
python -m web.jev_admin status
```

`0` is appropriate only for a verified new, unused allocation. Substitute the
confirmed amount when there is previous usage. Never initialize a replacement
ledger to work around lost storage or exhausted budget. `--db PATH` before the
subcommand can select a deliberately prepared alternative file for offline tests.
No command prints the key. Do not put credentials in source, build arguments,
GitHub workflow logs or shell command history.

Before activation, prove that both replacement containers mount the same
directory, the non-root process can transact, two processes cannot overspend,
and backup/restore fails closed until reconciled. Verify the saved runtime-only
flags without dumping env values. Retain current hardening/parser exceptions
and managed health configuration; the optional provider must not become a
rolling-release health dependency.

## Evaluation before public activation

Local tests use the real SDK with an HTTP mock; they prove adapter/accounting
behavior, not provider availability or game quality. Paid evaluation is an
explicit operator step. In the configured container, with its existing shared
ledger and key, run this only after approval (an exec-only `JEV_ENABLED=true`
can enable the evaluator while the web process remains disabled):

```bash
python -m web.jev_evaluate --confirm-paid-evaluation \
  --games-per-side 5 --output /var/lib/tictactoe/jev/jev-evaluation.jsonl
```

It visits every variant, tests an opening plus win/block fixtures, then plays
both sides against Random and Rules with recorded seeds. Each Jev move uses the
evaluation sublimit and shared monthly cap. It records actual moves, latency,
metadata, completed outcomes and interrupted games in a new private 0600 JSONL
file. Budget exhaustion stops it without scoring a partial game. The CLI does
not overwrite prior output. Retain results privately and summarize per variant
and starting side; do not infer quality from mocked responses. There is no
minimum playing-strength gate for the experimental label, but legality,
accounting and usable latency must be confirmed before enabling public play.

## Backup, recovery and rollback

Use the application backup command, not a raw copy of a live SQLite file:

```bash
python -m web.jev_admin backup /var/lib/tictactoe/jev/usage-backup.sqlite3
```

It uses SQLite's backup API and marks the **copy** paused. The live database
remains active. Choose off-host destination and retention explicitly; this
command alone is not disaster recovery. Keep dumps out of the repository/image.

Production uses [`infra/tictactoe-jev-backup`](../infra/tictactoe-jev-backup)
with the matching systemd service and timer in `infra/systemd`. It runs daily at
02:20 UTC with up to five minutes of randomized delay. The job requires the
approved host bind mount and permissions, selects a healthy production replica,
creates the copy through `web.jev_admin`, and checks SQLite integrity, schema,
tariff and the copy's paused flag. It then uploads to the dedicated
`grela-tictactoe-jev-backups` R2 bucket and downloads the small object again to
compare its SHA-256 digest. It keeps 14 local copies and deletes R2 ledger
objects older than 90 days. A failed upload or validation leaves the local copy
and fails the unit; it must be monitored rather than treated as success.

The rclone configuration lives only at `/etc/rclone/tictactoe-jev.conf`, owned
by root with mode 0600. Use a dedicated R2 access key restricted to object
read/write access for this bucket. Do not reuse Coolify's backup credentials or
store an R2 secret in the repository, unit file, application environment or
command history. The root-owned local backup directory is mode 0700 and the
copies are mode 0600. Installation is incomplete until one manual unit run,
remote checksum verification, timer inspection and a restore drill have passed.

Create the private bucket and its bucket-scoped key before configuring the host.
Add a bucket lifecycle rule with no prefix that deletes objects after 90 days;
this enforces remote retention even if the VPS job stops running. The script's
own remote deletion is an independent second enforcement path.
The root-only rclone file has this shape (replace the three placeholders locally;
do not paste their values into an issue, log or chat):

```ini
[tictactoe-jev-r2]
type = s3
provider = Cloudflare
access_key_id = <R2_ACCESS_KEY_ID>
secret_access_key = <R2_SECRET_ACCESS_KEY>
region = auto
endpoint = https://<CLOUDFLARE_ACCOUNT_ID>.r2.cloudflarestorage.com
acl = private
```

Use the jurisdiction-specific endpoint instead when the bucket has an explicit
R2 jurisdiction. Install `rclone`, the versioned script and both units, then run
the service manually. Inspect `systemctl status`, the root-only local file and
`rclone lsl tictactoe-jev-r2:grela-tictactoe-jev-backups` without printing the
configuration. Enable the timer only after the restore drill described below.

For restore, first disable/pause Jev in every replica and allow outstanding calls
to finish. Preserve the current ledger; restore the paused copy with correct
ownership while writers are stopped. Reconcile against provider spending for
the current UTC month, including spending after the backup and unresolved calls,
before enabling it. Reconciliation only increases the recorded total. If the
provider cannot give a reliable total, keep Jev paused or conservatively reconcile
to the full $1. It is safer to lose remaining allowance than spend it twice.

Application rollback restores the previous image digest and **retains the latest
ledger**. Version-1 readers require an exact supported schema/tariff; unknown
versions disable Jev. Future schema changes need old/new compatibility tests.
Rollback to an image without Jev leaves the ledger mounted and unused. Never
restore an older balance as part of an application rollback.

## Sources reviewed (latest review 2026-09-21)

- [TypeSafe models and tariff](https://docs.typesafe.ai/models)
- [Choice contract](https://docs.typesafe.ai/primitives/choice)
- [Python SDK](https://docs.typesafe.ai/sdk/python/usage)
- [SQLite online backup](https://www.sqlite.org/backup.html)
- [Cloudflare R2 authentication](https://developers.cloudflare.com/r2/api/tokens/)
- [rclone Cloudflare R2 configuration](https://rclone.org/s3/#cloudflare-r2)
