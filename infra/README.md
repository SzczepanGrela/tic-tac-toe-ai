# Deployment notes

The application container listens on port `8084` inside the isolated `tictactoe-network`. In Nginx Proxy Manager, forward `tictactoe.grela.dev` to `tic-tac-toe-ai:8084` with the `grela.dev wildcard (CF Origin)` certificate, Force SSL, HTTP/2, and HSTS enabled.

Install `deploy-launcher.example.sh` as `/home/tictactoe-app/deploy-launcher.sh` on the VPS and make both deployment scripts executable. The server-side repository belongs in `/home/tictactoe-app/app`.

The GitHub environment requires `TS_CLIENT_ID`, `TS_AUDIENCE`, `SSH_PRIVATE_KEY`, `SSH_HOST`, `SSH_PORT`, and `SSH_USER`. The Tailscale OIDC subject uses GitHub's immutable owner and repository IDs:

```text
repo:SzczepanGrela@115424220/tic-tac-toe-ai@1013262916:ref:refs/heads/main
```

Deployments use a preflight candidate container. The existing production container remains online while the candidate is checked, and its image is restored automatically if the promoted container fails its health check. Peak application memory can therefore double only during the preflight check; steady state runs one application container.

Cloudflare and Nginx Proxy Manager should provide their own edge/proxy rate limits in addition to the API's 30 requests per minute per IP.
