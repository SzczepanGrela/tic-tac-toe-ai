# Deployment notes

## Release contract

Quality runs the complete Python matrix, training smoke suite, Playwright suite, image build, and Trivy scan. A push to `main` publishes exactly one image as `ghcr.io/szczepangrela/tic-tac-toe-ai:<full-commit-sha>`, with an SBOM, provenance, and GitHub artifact attestation. Production receives that immutable manifest digest; the VPS neither checks out Git nor rebuilds the image.

The image uses a digest-pinned Python base and the hash-locked `requirements-web.lock`. Its OCI `org.opencontainers.image.revision` label and `/api/health` response contain the full source commit. Production starts as a non-root user with a read-only filesystem, all Linux capabilities dropped, `no-new-privileges`, a 128-process limit, 1 CPU, 512 MiB RAM, and bounded Docker logs.

Automated deployment is intentionally gated by the repository variable `PRODUCTION_DEPLOY_ENABLED`. Keep it set to `false` until GHCR visibility, the VPS scripts, and the pinned SSH host key are ready; set it to `true` only after the first image package is public.

## GitHub configuration

Repository secrets:

- `TS_CLIENT_ID`
- `TS_AUDIENCE`
- `SSH_PRIVATE_KEY`
- `SSH_HOST`
- `SSH_PORT`
- `SSH_USER`
- `SSH_KNOWN_HOSTS` — the independently verified `ssh-keyscan` line for the exact host and port, never collected inside CI

The current Tailscale workload-identity subject remains branch based because this repository deliberately does not use a GitHub Environment yet:

```text
repo:SzczepanGrela@115424220/tic-tac-toe-ai@1013262916:ref:refs/heads/main
```

After the `Quality gate` check exists on GitHub, protect `main` with a ruleset that requires pull requests, requires the strict `Quality gate` status check, resolves review conversations, and blocks force pushes and deletion. Zero approving reviews is intentional for this single-maintainer repository.

Enable Dependabot security updates, secret scanning, and push protection. Restrict allowed Actions to the publishers used by the pinned workflow files. All workflow references are full commit SHAs; Dependabot proposes future updates.

## VPS installation

The application container listens on port `8080` inside `tictactoe-network`. Nginx Proxy Manager forwards `tictactoe.grela.dev` to `tic-tac-toe-ai:8080` with the `grela.dev wildcard (CF Origin)` certificate, Force SSL, HTTP/2, and HSTS enabled.

Install the reviewed files as root-owned deployment programs:

```text
infra/deploy-launcher.example.sh -> /usr/local/libexec/grela-deploy/tictactoe
infra/deploy.sh                  -> /usr/local/libexec/grela-deploy/tictactoe-deploy
```

Both files should be owned by `root:root` and executable but not writable by `tictactoe-app`. Keep the existing forced-command authorized-key entry pointed at `/usr/local/libexec/grela-deploy/tictactoe`. The launcher accepts only `deploy sha256:<64 lowercase hex>`; arbitrary shell commands and mutable tags are rejected.

The public GHCR package lets the VPS pull by digest without a registry credential. The deployment starts a candidate alongside production, validates health, root, favicon, and a real move request, then renames the old container to `tic-tac-toe-ai-previous` and the candidate to `tic-tac-toe-ai`. NPM configuration is tested and reloaded before the public revision check. Existing traffic gets a drain interval; the old container is then stopped but retained for rollback. A failed promotion restores the previous name and reloads NPM. Only the application image older than the retained rollback release is removed; global Docker pruning is forbidden.

Peak application memory can reach roughly twice its steady-state limit during deployment. Steady state runs one active application container plus one stopped rollback container.

The manual Deploy workflow accepts a previously attested digest and never rebuilds it. Automatic queued deployments compare their source revision with the current `main` head and skip stale releases.

## Request path and client identity

The production request path is `visitor → Cloudflare → VPS firewall → Nginx Proxy Manager → application`. Rate limits and logs use the visitor IP, not a Cloudflare egress address or the NPM container address.

Allow public origin traffic on `80/443` only from Cloudflare's current IPv4 and IPv6 ranges. Verify the effective `DOCKER-USER`/nftables path from an external non-Cloudflare host because Docker-published ports can bypass ordinary UFW handling on some systems. A direct connection to the origin IP must fail.

NPM accepts `CF-Connecting-IP` only from Cloudflare's published ranges and replaces upstream `X-Forwarded-For` with that canonical visitor address. The deployment discovers NPM's exact address on `tictactoe-network` and passes only that peer to Uvicorn. Do not restore `--forwarded-allow-ips *`, and do not add Cloudflare ranges to Uvicorn because Cloudflare does not connect directly to the application container.

Cloudflare and NPM provide edge/proxy rate limits in addition to the API token buckets. Human-versus-AI refills at 30 moves per minute with a burst of 10. AI series use a separate 30-game bucket, and each request costs its requested game count. Idle, fully refilled client entries are evicted so spoofed or rotating addresses cannot grow memory indefinitely. CPU-bound AI work runs in worker threads behind a two-job semaphore so health checks remain responsive.
