# Request history and metrics

`POST /query` records metadata in SQLite at `DATABASE_PATH` (default
`data/reflex.db`). The file and parent directories are created on first database
use, not import. SQLite database files and journals are ignored by Git.

## Endpoints

- `GET /requests?limit=50&offset=0` returns newest-first metadata, up to 100 rows.
- `GET /metrics` returns totals, routes, provider attempts, failures, latency,
  known token totals, known estimated cost, and counts of unknown usage/cost.
- `GET /health` remains a liveness endpoint and does not test database access.

History contains request ID, UTC timestamp, status, prediction, route, model,
provider-attempt flag, latency, reported tokens, optional estimated cost, pricing
snapshot and a fixed error code. No prompt, answer, API key, or upstream error
body is stored. Invalid `/query` requests are recorded without their submitted
content, and validation errors no longer echo input values. Other endpoints are
not counted as query events. There is no raw-content storage option.

`metadata_persisted` on query responses indicates whether that event was written.
If SQLite fails, an already-completed answer is still returned; queries are never
retried to repair logging. Aggregate/history reads return 503 if storage fails.
Metrics cover successfully recorded events only, not missing events or requests
interrupted by process termination/client cancellation.

## Token and cost semantics

Provider usage comes from the OpenAI response, including incomplete responses
that return usage. Input, output, cached-input and cache-write counts are retained.
Missing or malformed usage remains unknown (`null`); a missing cached-input
breakdown is also unknown. If System 2 was never invoked, its usage and cost are
known zero, including locally rejected or unconfigured-provider requests.
Classifier costs are not measured; the current classifier is local rules.

Enable USD estimates by configuring `PRICING_MODEL`, `PRICING_DATE`,
`INPUT_USD_PER_MILLION`, and `OUTPUT_USD_PER_MILLION`. Cached input additionally
requires `CACHED_INPUT_USD_PER_MILLION` when cache reads are nonzero. Rates must be
nonnegative. No default model rates are assumed or fetched.

The returned model name must exactly match `PRICING_MODEL`, including snapshot
suffixes. Cost is calculated using decimal arithmetic:

```text
((input - cached_input) × input_rate
 + cached_input × cached_rate
 + output × output_rate) / 1,000,000
```

Missing usage, missing required rates, a model mismatch, or nonzero cache-write
usage produces unknown cost. Separate cache-write pricing is not implemented.
The pricing snapshot (model, date, rates, USD currency) is saved with every priced
provider event, so changing settings does not rewrite history. Values are string
encoded decimals, not floating-point billing totals. Estimates exclude taxes,
tools, service-tier adjustments, classifier usage, and any other charges. Set
rates appropriate to the model and request tier; these estimates are not invoices.
The adapter currently sends text without tools and leaves the provider tier at its
default. Usage fields follow the [OpenAI documentation](https://developers.openai.com/api/docs/guides/prompt-caching).

`known_estimated_cost_usd` sums only known estimates; always interpret it alongside
`unknown_cost_requests`. No savings, routing accuracy, or answer-quality claims
are inferred from traffic. Those require the next module's labeled evaluation and
measured baseline. Provider attempts count invocations, including failed ones;
they are not a measure of billable completions.

## Latency and retention

Latency measures gateway classification, routing and provider execution, excluding
HTTP body parsing, serialization and SQLite writes. Validation failures have no
measured gateway latency and are excluded from latency statistics. Mean and
nearest-rank P95 include both successful and failed measured gateway requests.
Empty datasets return null latency/token sums, zero request counts and zero known
cost. Metrics cover all retained rows rather than an implicit time window.

`RETENTION_DAYS` defaults to 30. Expired metadata is deleted on subsequent history
reads, metrics reads, and writes, not by a background scheduler. An idle application
retains old rows until the next database operation. Deletion is logical retention,
not secure erasure of SQLite pages, journals or backups.

Schema migrations use `PRAGMA user_version`: version 0 initializes schema 1;
unknown newer versions fail without overwriting data. Connections use WAL and a
100 ms lock wait. Operations are short synchronous transactions in this prototype;
large scans or slow disks can delay the API event loop. A worker-backed database
layer or PostgreSQL is future work before high-throughput deployment.

These endpoints currently have no authentication. The documented server binds to
localhost; access controls must be added before exposing request history publicly.
