# Local release runbook

## Python

```bash
uv sync
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open http://127.0.0.1:8000/ for the dashboard and `/docs` for API documentation.
The dashboard loads same-origin assets only; it uses no CDN, browser storage,
automatic polling, or third-party analytics. Responses and table fields are
inserted as text, never interpreted as HTML. Refresh updates history manually;
sending a query refreshes it once. Metrics show retained recorded requests only.

## Docker

```bash
docker compose up --build -d
docker compose ps
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/query -H 'Content-Type: application/json' \
  -d '{"prompt":"calc: 2+2"}'
docker compose logs --tail=50 gateway
docker compose down
```

Compose defaults to offline operation. To enable GPT, create `.env` from
`.env.example` and configure provider, model and key before starting. Compose
passes those fields explicitly; it does not copy `.env` into the image. The build
context allows only `pyproject.toml` and application files, excluding project
notes, Git history, databases, tests and credentials. Pricing settings are not
forwarded by the default Compose file, so provider cost remains unknown. Add
verified pricing environment settings in a local Compose override when needed.

The container runs as UID 10001. The named `reflex-data` volume retains metadata
across restarts and rebuilds; `docker compose down` preserves it. Do not use
`down -v` unless you intend to erase that volume. Custom bind mounts must be
writable by UID 10001. The image health check only verifies API liveness.

## Checks and release boundaries

CI installs dependencies, runs lint, formatting and tests, creates an offline
evaluation artifact, builds the image, and smoke-tests its dashboard, assets, local query, and persistence. It does not call OpenAI, push an image,
publish a site, or deploy automatically. Offline routing weaknesses are recorded
in the artifact rather than hidden behind a 100% threshold.

Dependency ranges and the Python base tag are not locked. A clean online install
and Docker build are required in CI before treating the image as release-tested.
For reproducible production releases, generate/review a dependency lockfile and
pin the image digest with network access. Local tests currently use cached
packages because this workspace cannot resolve package registries.

The server has no authentication or rate limiting. Compose binds only to localhost;
keep it there for this prototype. Before public deployment, add authentication to
query/history/metrics, request size and rate limits, TLS, access restrictions,
and a database strategy suited to expected concurrency. Guardrails are narrow
rules, not comprehensive moderation; Laya is optional and requires a separately configured server.
SQLite operations are synchronous and suitable only for modest local traffic.

## Data backup and rollback

Stop the service before copying its database and any WAL/SHM companions; store
backups outside version control with appropriate access permissions. Alternatively,
use SQLite's online backup API from an operator-controlled process. Retention is
logical deletion on database access, not secure erasure of backups.

To roll back code, check out the previous reviewed commit and rebuild. Preserve a
backup first; older code may not support a newer schema. Unknown future database
schema versions are rejected rather than overwritten. Do not delete a database to
resolve startup/storage errors without first inspecting and backing it up.

## Outstanding validation

No live GPT benchmark or measured cost saving has been established. The Laya adapter still requires real-model validation. Browser visual inspection, Docker
build/startup, and a clean online dependency install must be verified in an
environment with those tools and network access before a public release.
