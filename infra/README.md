# Production release notes

## Release contract

Every pull request runs the Python test matrix, training smoke suite, browser suite,
container build, container smoke test, and vulnerability scan. A push to `main`
publishes one image as `ghcr.io/szczepangrela/tic-tac-toe-ai:<full-commit-sha>` with
an SBOM, provenance, and a GitHub artifact attestation. Production receives the
immutable manifest digest and never rebuilds the image.

The container smoke test starts that exact image with the production CPU and
memory limits, `--cap-drop ALL`, and `--init`. It waits for the Dockerfile health
check, then verifies the health payload and source revision, the main page, the
favicon, and a real move request. Nothing is published to a host port during this
test.

The image uses a digest-pinned Python base and the hash-locked
`requirements-web.lock`. Its OCI `org.opencontainers.image.revision` label and
`/api/health` response contain the full source commit. The Dockerfile health check
runs `python -m web.healthcheck` inside the container and remains the portable
default for runtimes outside Coolify. The Coolify application enables a managed
CMD health check with the same command. Docker executes the effective check inside
the container, while Coolify waits for Docker to report the new container as
healthy before completing a rolling update and removing the previous container.

## Updating Python dependencies

Change the requirement ranges and regenerate `requirements-web.lock` together.
The image installs the lock with hash verification. Backend and browser CI use
that same lock, then install `requirements-test.txt`; the training smoke job
uses its separate training requirements. Development installs include all three.

Regenerate in a clean **Python 3.12** environment using `pip-tools`:

```bash
python -m pip install pip-tools
pip-compile --generate-hashes --resolver=backtracking --output-file=requirements-web.lock requirements-web.txt
python -m pip install --require-hashes -r requirements-web.lock
python -m pip install -r requirements-test.txt
python -m infra.check_runtime_lock --installed
python -m pip check
```

The check rejects missing runtime packages, stale pins, missing extras and test
tooling that changes installed locked versions. Hash verification and `pip check`
cover artifact integrity and installed transitive requirements. CI exercises both
Python 3.12 and 3.13; changing the production Python version remains a separate
compatibility decision. Review the lock diff, pass backend/browser/training and
container checks, and deploy the resulting tested digest.

## Coolify application contract

[`coolify-production.json`](coolify-production.json) records the nonsecret fields
that must match before a release may change production. The application is a
Docker Image resource that exposes only container port `8080`, has no host port
mapping. With Jev disabled, the game requires no persistent storage. Enabling
Jev adds the shared spend ledger described in [Jev operations](../docs/jev-operations.md);
the existing JSON contract does not validate mounts, their contents or secrets.
Its `fqdn` supplies the public Traefik route;
the newer `domains` field and custom labels remain empty. It uses these effective
runtime settings:

- non-root user supplied by the image;
- `--cap-drop ALL` and `--init`;
- 1 CPU, 512 MiB memory, 128 MiB reservation, and no additional swap;
- a managed CMD health check using `python -m web.healthcheck`, with a 5-second
  interval, 5-second timeout, 10 retries, and a 10-second start period;
- the Docker daemon's bounded `local` logs;
- two retained application images and generated container names, which allow
  Coolify rolling updates.

The current Coolify version does not apply an application PID limit or
`no-new-privileges` through the Docker Image resource's custom option parser.
Those settings are not claimed by the contract. The original game release is
stateless. With Jev enabled, rolling replicas must share one durable ledger;
rollback changes the image only and must never restore an older spend balance.
Missing/incompatible accounting disables Jev while local readiness stays healthy.
Mount, backup/restore and same-host replica acceptance are required before
activation; they are not established by the original stateless release tests.

Coolify stores a digest as a tag in the form `sha256-<64 hex characters>`. The
release program accepts only the standard `sha256:<64 hex characters>` form and
performs the conversion itself.

## Production workflow

The deployment job uses the protected GitHub environment `production`. Configure
that environment with a required reviewer and allow deployments only from
`main`. This is the operator approval gate for every automatic or manually
selected release. The repository-level `production` concurrency group serializes
deployments and does not cancel a run that is publishing or observing production.

Environment variables:

- `COOLIFY_URL` — the private Coolify origin reachable through Tailscale; the
  value may optionally end with `/api/v1`;
- `COOLIFY_APPLICATION_UUID` — the production application UUID;
- `PRODUCTION_URL` — `https://tictactoe.grela.dev`.

Environment secrets:

- `COOLIFY_TOKEN` with `read`, `write`, and `deploy`, created only for this
  repository's production deployment;
- `TS_CLIENT_ID` and `TS_AUDIENCE` for the short-lived Tailscale identity.

Do not grant `read:sensitive` or `root`. Keep a separate read-only token for
operator and MCP audits; never reuse the deployment token for those tasks.
Coolify tokens are scoped to a team rather than to one application, so the
deployment job can still affect other resources in that team if its code is
changed. A separate Coolify team or a narrow policy gateway is needed for strict
per-application authorization.

Restrict the Tailscale workload tag to the Coolify API address and port. The
Coolify API IP allowlist must also accept this workload identity. Verify both
controls before enabling deployment; do not broaden either rule to the public
internet.

The release proceeds as follows:

1. Validate the immutable digest, GitHub attestation, image revision label, and
   freshness of an automatic `main` release.
2. Connect to the private Coolify API using the short-lived Tailscale identity.
3. Require an exact match with the checked-in Coolify contract and a healthy
   production application, with no deployment already running for it.
4. Record the current digest and public revision, check the stable health and
   asset contract shared with earlier releases, and verify that the saved digest
   contains that revision label. Version-specific checks must not block an
   upgrade from a release that predates a newly added endpoint.
5. Change only the image digest, read it back, submit one deployment request, and
   track the exact deployment UUID returned by Coolify.
6. During the rolling update, accept the previous or target revision from the
   public health endpoint. Three consecutive failures cancel the deployment and
   require a confirmed terminal state before rollback begins.
7. After Coolify reports completion, require the target revision repeatedly,
   rerun the complete public smoke test, and observe it for an additional window.
8. On a failure with a known deployment state, restore the saved digest through
   Coolify, deploy it, and verify the previous public revision and stable
   cross-version smoke test.

Read requests can be retried. Mutation requests are never blindly retried. If a
deployment or cancellation response is uncertain, the workflow stops for manual
reconciliation instead of risking two concurrent deployments. Manual workflow
cancellation or runner failure can interrupt observation and automatic rollback;
the operator must then inspect the exact Coolify deployment and current public
revision before taking another action.

The manual workflow accepts a previously attested digest and derives its revision
from the image. It supports an operator-approved rollback without rebuilding an
older commit. An automatic queued release is skipped if its source commit is no
longer the current `main` head.

The read-only contract and deployment-history calls were verified against the
installed Coolify `4.3.14` API. Before relying on changed behavior after a
Coolify upgrade, prepare a new isolated acceptance plan and repeat the relevant
contract, cancellation, restoration, rolling-capacity, and public rollback
checks.

## Activation status

The protected production environment, Tailscale path, Coolify contract,
managed health gate, failed-candidate cancellation, public-smoke rollback,
release serialization, rolling capacity, and automatic tested-digest promotion
were accepted in September 2026. `PRODUCTION_DEPLOY_ENABLED` is enabled. The
isolated canary, source harness and manual validation workflows were retired
after acceptance on September 17. Historical run evidence remains in GitHub
Actions and the private infrastructure record.

Coolify is the production release and recovery path. To restore a release,
select a previous qualified digest through the protected deployment workflow;
preserve the current Jev ledger. The unused SSH deployment scripts and their
dedicated tests were removed after their workflow and credential consumers had
been retired. There is no alternate SSH deployment procedure to configure.

The [limits and streaming acceptance guide](../docs/delivery-verification.md)
separates automated endpoint/queue/real-HTTP checks from public ingress and
rolling-replica acceptance. Per-process budgets are not shared across replicas.

## Jev ledger backup

[`tictactoe-jev-backup`](tictactoe-jev-backup) and the units in
[`systemd`](systemd) implement the production ledger backup described in
[Jev operations](../docs/jev-operations.md). They contain no credentials. Install
the script as `/usr/local/libexec/grela-infra/tictactoe-jev-backup`, install the
units under `/etc/systemd/system`, and keep the bucket-scoped R2 credentials in
the root-only `/etc/rclone/tictactoe-jev.conf`. Do not enable the timer until a
manual run and restore drill have succeeded.

The first production acceptance completed on 2026-09-21 with rclone 1.75.1,
successful upload/readback verification, an isolated fail-closed restore and an
active daily timer. The application remains at `JEV_ENABLED=false` pending paid
evaluation. Keep the script's 30-day remote retention aligned with the bucket's
independent lifecycle rule.

## Operating exception and documentation changes

The operator accepts process-local request budgets for one normal process and
brief managed-rolling overlap. See [scope and reconsideration triggers](../docs/delivery-verification.md#accepted-single-instance-operating-boundary).
This does not relax Jev accounting or authorize permanent replicas without a
new consistency decision. Shared request counters are not installed.

Documentation-only merges do not need a production rollout. Run the required
checks; if Quality creates a protected deployment for such a merge, leave the
production approval ungranted and cancel that pending run after verification.
Record the deployed code revision separately from the newer documentation head.
