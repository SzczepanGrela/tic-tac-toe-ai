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
runs `python -m web.healthcheck` inside the container. Coolify health checks remain
disabled so Coolify does not replace that image-defined command; Docker still
reports the resulting health state to Coolify.

## Coolify application contract

[`coolify-production.json`](coolify-production.json) records the nonsecret fields
that must match before a release may change production. The application is a
Docker Image resource that exposes only container port `8080`, has no host port
mapping and no persistent storage. Its `fqdn` supplies the public Traefik route;
the newer `domains` field and custom labels remain empty. It uses these effective
runtime settings:

- non-root user supplied by the image;
- `--cap-drop ALL` and `--init`;
- 1 CPU, 512 MiB memory, 128 MiB reservation, and no additional swap;
- the Docker daemon's bounded `local` logs;
- two retained application images and generated container names, which allow
  Coolify rolling updates.

The current Coolify version does not apply an application PID limit or
`no-new-privileges` through the Docker Image resource's custom option parser.
Those settings are not claimed by the contract. The application is stateless, so
an automated rollback cannot conflict with a database migration. Reuse of this
release procedure for a stateful service requires a separate migration and
rollback design.

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

- `COOLIFY_READ_TOKEN` with `read` only;
- `COOLIFY_WRITE_TOKEN` with `write` only;
- `COOLIFY_DEPLOY_TOKEN` with `deploy` only;
- `TS_CLIENT_ID` and `TS_AUDIENCE` for the short-lived Tailscale identity.

The three Coolify tokens must be distinct. Do not grant `read:sensitive` or
`root`. Coolify tokens are scoped to a team rather than to one application, so
the deployment job can still affect other resources in that team if its code is
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
4. Record the current digest and public revision, smoke-test the current release,
   and verify that the saved digest contains that revision label.
5. Change only the image digest, read it back, submit one deployment request, and
   track the exact deployment UUID returned by Coolify.
6. During the rolling update, accept the previous or target revision from the
   public health endpoint. Three consecutive failures cancel the deployment and
   require a confirmed terminal state before rollback begins.
7. After Coolify reports completion, require the target revision repeatedly,
   rerun the complete public smoke test, and observe it for an additional window.
8. On a failure with a known deployment state, restore the saved digest through
   Coolify, deploy it, and verify the previous public revision and full smoke test.

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
installed Coolify `4.3.14` API. Repeat the contract check and the controlled
cancellation test after a Coolify upgrade before relying on automatic rollback.

## Activation sequence

Keep the repository variable `PRODUCTION_DEPLOY_ENABLED` set to `false` while the
new path is being reviewed. Before changing it:

1. Create and protect the `production` environment.
2. Move the five deployment secrets into that environment and set its three
   nonsecret variables.
3. Confirm the Tailscale ACL and Coolify API allowlist using the workflow identity.
4. Exercise a successful deployment against the temporary isolated application.
5. Exercise a controlled unhealthy candidate and confirm cancellation, rollback,
   container cleanup, and the deployment history in Coolify.
6. Run one manually approved production release and confirm its public revision.
7. Enable automatic calls by setting `PRODUCTION_DEPLOY_ENABLED` to `true`.

The older `deploy.sh` and forced-command launcher remain available only as a
reviewed emergency fallback during this transition. The current workflow does not
use SSH or Nginx Proxy Manager. Remove the legacy scripts, their deployment key,
and their tests after the Coolify path has completed the activation sequence.
