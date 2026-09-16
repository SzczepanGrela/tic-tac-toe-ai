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
- a managed CMD health check using `python -m web.healthcheck`, with a 5-second
  interval, 5-second timeout, 10 retries, and a 10-second start period;
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

The manual `Validate deployment safety` workflow performs that cancellation test
only on an isolated application. Before changing anything, it requires the exact
application name, no FQDN, no host port mapping, the expected stable digest, a
healthy state, the enabled Coolify CMD health check `python -m web.healthcheck`
with its expected timings, and a UUID different from production. It builds a
short-lived candidate in which that module always fails, waits until Coolify is
actively deploying it, cancels that exact deployment UUID, confirms a terminal
cancelled state, restores the saved digest through a second deployment, and
requires the isolated application to become healthy again. The candidate has no
artifact attestation, so the production workflow rejects it. After a successful
validation, the cleanup step deletes every GHCR version created by this workflow,
including versions retained after an earlier failed run.

The workflow uses the `production` GitHub environment because that environment
holds the Coolify and Tailscale credentials. Its job is still serialized with
production and requires the same operator approval. The hard UUID inequality and
the isolated application contract prevent it from targeting the production
application. After the run, verify on the host that Coolify removed the cancelled
candidate container before deleting the temporary application.

## Activation sequence

Keep the repository variable `PRODUCTION_DEPLOY_ENABLED` set to `false` while the
new path is being reviewed. Before changing it:

1. Create and protect the `production` environment.
2. Move the three deployment secrets into that environment and set its three
   nonsecret variables.
3. Confirm the Tailscale ACL and Coolify API allowlist using the workflow identity.
4. Exercise a successful deployment against the temporary isolated application.
5. Enable on that application the managed CMD health check recorded in the
   production contract, redeploy its stable digest, and confirm that Coolify waits
   for the container to become healthy.
6. Run `Validate deployment safety` with the isolated application's current
   digest. Confirm cancellation and restoration in its summary and deployment
   history, then confirm candidate container cleanup on the host.
7. Apply the same health-check settings to production and run one manually
   approved production release. Confirm its effective Docker health check and
   public revision.
8. Run the temporary `Validate production rollback` workflow with a new attested
   digest and the exact current production digest/revision. It first proves the
   new release with the real public smoke test, then deliberately checks an
   impossible revision without making the application itself unhealthy. Treat
   the run as successful only when the previous digest is restored and its
   revision, health, routes and full smoke test pass. An unrelated endpoint
   failure makes the workflow fail even if recovery succeeds. As with a normal
   release, runner or workflow cancellation can interrupt automatic rollback;
   monitor the approved run until its terminal state.
9. Enable automatic calls by setting `PRODUCTION_DEPLOY_ENABLED` to `true`.

The older `deploy.sh` and forced-command launcher remain available only as a
reviewed emergency fallback during this transition. The current workflow does not
use SSH or Nginx Proxy Manager. Remove the legacy scripts, their deployment key,
and their tests after the Coolify path has completed the activation sequence.
