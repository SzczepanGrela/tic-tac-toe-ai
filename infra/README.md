# Deployment notes

The application container listens on port `8084` inside the isolated `tictactoe-network`. In Nginx Proxy Manager, forward `tictactoe.grela.dev` to `tic-tac-toe-ai:8084` with the `grela.dev wildcard (CF Origin)` certificate, Force SSL, HTTP/2, and HSTS enabled.

Install `deploy-launcher.example.sh` as `/home/tictactoe-app/deploy-launcher.sh` on the VPS and make both deployment scripts executable. The server-side repository belongs in `/home/tictactoe-app/app`.

The GitHub environment requires `TS_CLIENT_ID`, `TS_AUDIENCE`, `SSH_PRIVATE_KEY`, `SSH_HOST`, `SSH_PORT`, and `SSH_USER`. The Tailscale OIDC subject must be:

```text
repo:SzczepanGrela/tic-tac-toe-ai:ref:refs/heads/main
```

Cloudflare and Nginx Proxy Manager should provide their own edge/proxy rate limits in addition to the API's 30 requests per minute per IP.
