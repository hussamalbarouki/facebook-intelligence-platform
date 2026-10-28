# Facebook Intelligence Platform

**Self-hosted Facebook CLI gateway**, admin UI (Arabic, mobile-first), API keys, multi-account sessions, PostgreSQL task queue, Swagger/OpenAPI docs, and an **experimental** independent Playwright reactor-list extension.

## Important accuracy boundary

The upstream `facebook-cli` engine is **unchanged**: our Docker build copies `/usr/bin/fb` from the published `ghcr.io/tamnd/fb:0.3.0` container. No original parser is modified or redistributed. All upstream features depend on Facebook's visibility to signed-out or signed-in users. Facebook may restrict access, return partial results, or change its markup. **Complete lists of reactor identities are NOT guaranteed.** The experimental browser adapter only reads names shown in a visible reaction dialog; it cannot and does not bypass login challenges, visibility restrictions, or access checks. If nothing is visible it returns `unavailable`, and when some names appear it returns `partial` (never claims complete).

Original author/repository: https://github.com/tamnd/facebook-cli (Apache-2.0). Their complete docs: https://facebook-cli.tamnd.com/. Not affiliated with Meta.

## Easy Coolify installation

### Option A: Public GitHub repository (recommended first deployment)

1. In GitHub create a **public** repository named `facebook-intelligence-platform` and push these project files into it. Do **not** commit any filled `.env` file, passwords, Cookies, database backups, or session data.
2. In Coolify select **New Resource → Public Repository** and paste your actual GitHub repo URL.
3. Choose **Docker Compose** as build method, using `compose.yaml`.
4. Under Environment Variables, set three random secrets:
   - `ADMIN_PASSWORD`: strong password for the web admin login.
   - `APP_SECRET`: a persistent 64-character random hex string for session-signing and encrypting Facebook sessions. **Do not change after storing accounts** without re-importing all sessions.
   - `POSTGRES_PASSWORD`: strong URL-safe password (A-Z, a-z, 0-9, underscore, hyphen). This is used inside a PostgreSQL DSN.
5. Coolify: assign an HTTPS domain to **platform** on internal port `8000`. **Do not expose database port or the browser directly.** Enable TLS.
6. Deploy. Check `https://YOUR-DOMAIN/healthz`, then open `https://YOUR-DOMAIN/admin` and use `ADMIN_PASSWORD`.
7. Create an API key on the API Keys page, click `/docs`, hit **Authorize**, paste the key and press **Try it out** on any operation.

### Option B: Prebuilt GHCR image (only after GitHub Actions succeeds)

A GitHub workflow publishes `ghcr.io/OWNER/facebook-intelligence-platform:latest` on `main`. Substitute OWNER with your actual GitHub username. **This image does not exist until the repository is created and its GitHub Action completes successfully.** In Docker Compose replace `build: .` with `image: ghcr.io/OWNER/facebook-intelligence-platform:latest`; keep database, secrets, volumes and port settings as in `compose.yaml`.

## REST API

The root `/admin` is browser-only and cookie-authenticated. Public API operations require header `X-API-Key: fip_...` (one-time key displayed in dashboard). The OpenAPI UI includes its own **Authorize** control.

* `GET /healthz`: PostgreSQL / application health.
* `GET /v1/capabilities`: full operation list, which ones are HTTP originals vs CLI extras.
* `POST /v1/operations/{operation}`: **every** original approved CLI capability individually documented in Swagger; body `{ "ref":"nasa", "flags":{"limit":20}, "account_id":null }`.
* `POST /v1/run/{operation}`: one generic variant.
* `GET /v1/read/{operation}?ref=nasa&limit=20`: short form for read ops.
* `POST /v1/jobs`: queue a slow operation; body `{ "operation":"comments", "ref":"https://www.facebook.com/...", "flags":{"limit":100} }`.
* `GET /v1/jobs` and `GET /v1/jobs/{id}`: track and retrieve results.
* `POST /v1/jobs` with `operation:"download-media", flags:{"media_type":"photo"}`: download photo/photos/video using the original CLI into a ZIP owned by the server; `GET /v1/jobs/{id}/artifact` (API key) retrieves it. `operation:"archive"` similarly exports a redacted diagnostic capture.
* `POST /v1/jobs` with `operation:"reaction-identities"`: start an **experimental** Playwright read of publicly/viewably listed reactor names on a full post URL (requires a saved browser or cookie session).
* `/docs`, `/redoc`, `/openapi.json`: interactive OpenAPI docs, with API-key authentication in Swagger.

Core categories preserved through the original binary:

| Category | Functions |
|---|---|
| Pages & posts | page, profile, feed, post, comments, reactions |
| Media | photos, photo, videos, video, reel |
| Events & groups | events, event, group, group-feed |
| Discovery | search, discover, id, explain, surfaces, tiers, routes, fields |
| Relations | edges, graph, rdf |
| Local dataset | crawl, db-stats, query, export |
| Diagnostics | archive, cache, config, whoami |

**Not exposed**: `serve` (this platform is the server), `mcp` (separate protocol), CLI completions, and arbitrary write/delete commands. Downloads and archives are supported via sandboxed server-owned ZIP jobs only, not arbitrary user-selected server paths. Every user-exposed flag is allowlisted to prevent arbitrary commands or files. For advanced original flags use the original CLI directly.

### PHP example

```php
<?php
$host = 'https://YOUR-DOMAIN';
$key = getenv('FB_API_KEY');
$url = $host . '/v1/read/comments?' . http_build_query([
    'ref' => 'https://www.facebook.com/NASA/posts/1587860636042640/',
    'limit' => 100,
]);
$ch = curl_init($url);
curl_setopt_array($ch, [
    CURLOPT_HTTPHEADER => ['X-API-Key: ' . $key],
    CURLOPT_RETURNTRANSFER => true,
    CURLOPT_TIMEOUT => 180,
]);
$resp = curl_exec($ch);
$code = curl_getinfo($ch, CURLINFO_HTTP_CODE);
curl_close($ch);
if ($code !== 200) throw new Exception('API error: ' . $resp);
$data = json_decode($resp, true);
print_r($data['records'] ?? []);
```

## Multi-account and browser login

* Admin → Accounts: add a label only, then open its live Chrome screenshot. Click/enter credentials **only inside your own HTTPS admin session**, then save. The platform saves browser storage state + imports two cookies into a separate original `fb` data directory. Browser control is **experimental**, Facebook may challenge or disallow automated browsers.
* Or supply your already authorized account's `c_user` and `xs` cookies directly in the encrypted admin form. Never put cookies in public repo or API responses.
* Multiple accounts have separate per-account data dirs. Choose a specific account for extraction, or leave blank to use the designated primary. Automatic fallback is restricted to expired/invalid login state; it must never rotate to evade platform rate limits, access checks or restrictions.
* API keys are SHA-256 hashed; the raw key is displayed once and can be revoked by the admin. 
* Cookies and browser storage state are Fernet encrypted in PostgreSQL; the original engine additionally persists its session file in the private `/data/accounts` Docker volume. Protect backups and the `APP_SECRET`.

## Runtime

- `platform`: FastAPI + unmodified `fb` binary + optional Playwright via system Chromium.
- `db`: PostgreSQL 16.
- Persistent volumes: `platform_data` and `postgres_data`. A one-worker polling queue persists queued jobs; interrupted jobs restart on application boot. Scale to one instance until an external distributed worker/lease is implemented.
- Chromium requires extra shared memory; `shm_size: 512mb` is set. A 4GB RAM VPS can run low-concurrency tasks, but monitor memory pressure.
- Internet access to Facebook is required. Free means no commercial scraping API fees; your VPS and bandwidth are still your costs.

## Security and limitations

Use a real HTTPS domain, strong secrets, secure backups and admin access. Do not connect or extract data from accounts/groups you cannot lawfully access. The platform does not automatically defeat Facebook checkpoints, CAPTCHAs or rate limits. Explicitly does **not** guarantee all comments or reactor identities. On public repositories keep credentials out of commits. Database and primary Go engine have no externally published ports.

## Local tests

Set `FIP_TEST=1`, `DATABASE_URL=sqlite:////tmp/fip-test.db`, and use `python -m pytest tests -q`; these are API and validation tests, not Facebook end-to-end evidence. Docker build and Facebook extraction require a separate environment and actual authorization.