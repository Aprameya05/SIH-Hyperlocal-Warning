# Alert Worker: rate limiting and deployment preflight

The Worker (`worker/index.js`) has an application-level, best-effort limiter (KV fixed window per `CF-Connecting-IP`:
5 `/subscribe` and 20 `/unsubscribe` requests per 10 minutes, then HTTP 429 with `Retry-After`). KV is eventually
consistent, so a distributed or very fast burst can exceed it. **A Cloudflare WAF rate-limiting rule must sit in front of
the Worker in production.** It cannot be configured from this repository; it is account configuration.

## Required Cloudflare configuration (dashboard: Security > WAF > Rate limiting rules)

Replace `<worker-host>` with the Worker's hostname (the value of the `WORKER_URL` secret).

| Rule | Expression | Characteristic | Threshold | Action |
|---|---|---|---|---|
| `subscribe-burst` | `http.host eq "<worker-host>" and http.request.uri.path eq "/subscribe" and http.request.method eq "POST"` | IP | 5 requests / 10 min (or the closest period your plan allows) | Block, duration >= the period |
| `unsubscribe-guess` | `http.host eq "<worker-host>" and http.request.uri.path eq "/unsubscribe"` | IP | 20 requests / 10 min | Block |
| `admin-list` | `http.host eq "<worker-host>" and http.request.uri.path eq "/subscribers"` | IP | 10 requests / 10 min | Block |

Plan limits: available counting periods, number of rules and mitigation durations depend on the Cloudflare plan; use
the shortest allowed period that keeps the same average rate (for example 1 request per 2 minutes) if 10 minutes is not
offered. Verify the plan limits in the dashboard; they are not checked by this repository.

Also recommended: Security > Bots > Bot Fight Mode, and a custom rule that blocks `/subscribers` for requests without
the `X-Admin-Key` header (the Worker already returns 401, this only reduces noise).

## Secrets (never in `wrangler.toml` or chat)

```
npx wrangler secret put TOKEN_SECRET   # long random value (32+ bytes); the Worker returns 503 without it
npx wrangler secret put ADMIN_KEY      # must equal the GitHub secret WORKER_ADMIN_KEY used by send_alerts.py
```

## Preflight before any deployment

1. Run the **Worker preflight (read-only)** workflow (Actions tab > Worker preflight > Run workflow). It runs
   `python scripts/check_worker_config.py --remote` with the repository's `CLOUDFLARE_API_TOKEN` secret: static config
   checks plus a check that both secrets exist on the deployed Worker. It deploys nothing.
2. Only if it passes, deploy with `cd worker && npx wrangler deploy`.
3. Confirm the WAF rules above exist. Then smoke test: `POST /subscribe` six times from one client returns 429 on the
   sixth; `GET /subscribers` without a key returns 401.

The API token used must be allowed to read Worker secrets (Workers Scripts: Read). If the preflight reports it cannot list
secrets, the token's permissions, not the Worker, are the problem.
