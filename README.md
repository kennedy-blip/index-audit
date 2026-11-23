# IndexAudit

IndexAudit is a lightweight SQLite index-review workbench. It classifies supplied indexes against query samples, highlights left-prefix overlap and unused candidates, and generates a **review-only** cleanup script.

## Stack
- Static HTML, CSS, and vanilla JavaScript in `public/`
- Python 3.12 serverless handlers in `api/`
- Python standard library only; no Node.js runtime, TypeScript, or bundler

## Deploy to Vercel
Import `kennedy-blip/index-audit` as a Vercel project and keep the project root at the repository root. Static assets are in `public/`; Python functions are in `api/`.

## API
- `GET /api/workloads` — returns three built-in workload presets.
- `POST /api/workloads` — stores a custom workload in current-process memory.
- `POST /api/audit` — accepts `{ "ddlSql": "...", "querySamples": [...], "writeRatio": 0.8 }`.

Custom workload saves are ephemeral. Vercel instances can be recycled and do not share process memory; use a persistent external store if saved workloads must survive cold starts.

## Methodology and safety
The analyzer creates a temporary in-memory SQLite database and accepts only `CREATE TABLE` and `CREATE INDEX` statements. It inspects SQLite catalog metadata and compares known schema columns with supplied query text. This matching is heuristic; it does not collect production index usage counters or model actual query plans.

Write-overhead and cleanup impact are directional estimates, not measured latency or storage savings. Review every generated `DROP INDEX` statement against uniqueness, foreign-key dependencies, production query telemetry, representative `EXPLAIN QUERY PLAN` output, and a tested backup/rollback plan. IndexAudit never connects to or modifies a production database.

## Verification
```bash
python3 -m py_compile api/*.py
python3 -m json.tool vercel.json
```
