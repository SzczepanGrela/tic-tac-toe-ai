# Deployment notes

The application container listens on port `8084` inside the isolated `tictactoe-network`. In Nginx Proxy Manager, forward `tictactoe.grela.dev` to `tic-tac-toe-ai:8084` with the `grela.dev wildcard (CF Origin)` certificate, Force SSL, HTTP/2, and HSTS enabled.

Install `deploy-launcher.example.sh` as `/home/tictactoe-app/deploy-launcher.sh` on the VPS and make both deployment scripts executable. The server-side repository belongs in `/home/tictactoe-app/app`.

The GitHub environment requires `TS_CLIENT_ID`, `TS_AUDIENCE`, `SSH_PRIVATE_KEY`, `SSH_HOST`, `SSH_PORT`, and `SSH_USER`. The Tailscale OIDC subject uses GitHub's immutable owner and repository IDs:

```text
repo:SzczepanGrela@115424220/tic-tac-toe-ai@1013262916:ref:refs/heads/main
```

Deployments use a preflight candidate container. The existing production container remains online while the candidate is checked, and its image is restored automatically if the promoted container fails its health check. Peak application memory can therefore double only during the preflight check; steady state runs one application container.

Cloudflare and Nginx Proxy Manager should provide their own edge/proxy rate limits in addition to the API limits. Human-versus-AI uses a token bucket refilled at 30 moves per minute with a burst of 10. AI series use a separate 30-game bucket, and each request costs its requested number of games. Rate-limit responses include `Retry-After`.

The production request path is `visitor → Cloudflare → VPS firewall → Nginx Proxy Manager → application`. Rate limits and logs must use the visitor IP, not a Cloudflare egress address or the NPM container address.

Allow public origin traffic on `80/443` only from Cloudflare's current IPv4 and IPv6 ranges. Verify the effective `DOCKER-USER`/nftables path from an external non-Cloudflare host because Docker-published ports can bypass ordinary UFW handling on some systems. A direct connection to the origin IP must fail.

Configure NPM's real-IP handling to accept `CF-Connecting-IP` only from the published Cloudflare ranges (`set_real_ip_from`, `real_ip_header CF-Connecting-IP`) and replace the upstream `X-Forwarded-For` value with the resulting canonical visitor address. The deploy script discovers NPM's exact address on `tictactoe-network` and passes only that address to Uvicorn as a trusted forwarding peer. This trust authorizes NPM to assert the normalized visitor address; it does not make NPM's own address the rate-limit key. Do not restore `--forwarded-allow-ips *`, and do not add Cloudflare ranges to Uvicorn because Cloudflare does not connect directly to the application container.
