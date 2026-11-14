# Invoice Three-Way Matching

Local monorepo: Next.js UI, FastAPI API, PostgreSQL, and explicit schema migrations. Domain entities, persistence tables, and the reference-data import CLI are implemented; read-only PO queries and invoice business logic remain future work. See the [architecture](docs/ARCHITECTURE.md) for the backend dependency boundaries.

## Quick start with Docker Compose

Requires Docker with Compose v2. Generated `backend/uv.lock` and `pnpm-lock.yaml` must be present before building; see dependency setup below.

1. Copy `.env.example` to an untracked `.env` and replace the local PostgreSQL password.
2. Run `docker compose --env-file .env up --build --wait`.
3. Open the UI at <http://127.0.0.1:3000> and API liveness at <http://127.0.0.1:8000/health>.

`GET /health` returns HTTP 200 with `{"status":"ok"}`. It does **not** test database connectivity. Compose checks PostgreSQL separately with `pg_isready`, then starts the API, then the UI. Published ports bind only to `127.0.0.1`.

Use `docker compose ps` to inspect service health. Stop with `docker compose down`; the named `postgres_data` volume persists. Do not remove the volume to troubleshoot startup. PostgreSQL initialization variables apply only when the data volume is empty.

## Local development

Requires Python 3.12, uv, Node.js 24, and npm. pnpm is pinned to 10.28.2; these commands use a repository-local npm cache without a global install.

From the repository root, resolve dependencies once if lockfiles are absent:

```sh
UV_CACHE_DIR="$PWD/.cache/uv" uv lock --project backend
npm exec --cache .cache/npm --yes --package=pnpm@10.28.2 -- pnpm install --lockfile-only --store-dir .cache/pnpm
```

Normal installs use the generated lockfiles without changing their resolution:

```sh
UV_CACHE_DIR="$PWD/.cache/uv" uv sync --project backend --frozen
npm exec --cache .cache/npm --yes --package=pnpm@10.28.2 -- pnpm install --frozen-lockfile --store-dir .cache/pnpm
```

Run each development server in its own terminal:

```sh
UV_CACHE_DIR="$PWD/.cache/uv" uv run --project backend --frozen uvicorn app.main:app --app-dir backend --reload --host 127.0.0.1 --port 8000
npm exec --cache .cache/npm --yes --package=pnpm@10.28.2 -- pnpm dev
```

PostgreSQL is not needed for API liveness or the placeholder UI. To run the database alone, use `docker compose --env-file .env up -d database`.

## Database migrations

Run migrations explicitly against the intended database; API startup never runs them. Local commands require server-side environment variables already exported (no automatic `.env` reading): `DATABASE_URL` takes precedence, otherwise supply `POSTGRES_HOST`, `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, and optionally `POSTGRES_PORT` (default `5432`). URL passwords must be URL-encoded; component passwords are passed unchanged. Never put credentials in command output or frontend configuration.

From the repository root:

```sh
uv run --project backend alembic -c backend/alembic.ini upgrade head
```

For a built Compose backend image, with Compose environment configured:

```sh
docker compose run --rm backend alembic upgrade head
```

Repeating `upgrade head` is safe. Rollback is explicit (`alembic downgrade base`, using the same config/location) and **drops these tables and their stored data**; it is not a troubleshooting step for a persistent development volume. Review backups and target before any downgrade.

## Import reference data

Migrate the intended database first, then run from the repository root using the same exported server-only database configuration (no automatic `.env` loading):

```sh
PYTHONPATH=backend backend/.venv/bin/python -m app.interfaces.cli.import_reference_data --directory data/reference-data/reference-data
```

Alternatively, from `backend/`, run `python -m app.interfaces.cli.import_reference_data --directory ../data/reference-data/reference-data` in the installed environment. `--directory` is required; `--help` describes the policy. The directory must contain the three named JSON arrays. Original challenge files are read-only inputs, not embedded application data.

The first successful import prints exactly this compact JSON line to stdout (header counts only; nested lines are included in the transaction but not counted separately):

```json
{"suppliers":{"inserted":6,"unchanged":0},"purchase_orders":{"inserted":10,"unchanged":0},"goods_received_notes":{"inserted":11,"unchanged":0}}
```

A repeat prints inserted `0` and unchanged `6`, `10`, `11` respectively. Success exits `0` with empty stderr. Validation, conflicting content, file, configuration, or database errors exit `1` with empty stdout and a safe single stderr line, for example `Import failed: GRN references unknown PO-local line`. Missing CLI arguments use argparse's standard exit `2`.

**Import policy:** Supplier IDs, PO numbers, and GRN numbers are unique. Line numbers are unique within each PO/GRN; item codes need not be globally unique. Existing identities with identical full content (including address and nested lines) are no-ops. Decimal scale and line order do not change content. Different content for an existing identity conflicts; the importer never updates or deletes stored data. References may resolve against the complete batch or already persisted records. All three files commit in one transaction; rejected batches persist nothing and emit no counts.

**Validation boundary:** Objects require exactly the domain fields. JSON duplicate fields, malformed arrays/objects, wrong field types, invalid `YYYY-MM-DD` dates, nonfinite numbers, and booleans used as numbers are rejected. Quantities/prices are parsed exactly as Decimal, with no added negativity, supplier-activity, PO-status, or payment limits.

## Verification

From the repository root after dependency installation:

```sh
backend/.venv/bin/python -m pytest backend/tests/test_health.py -q
npm exec --cache .cache/npm --yes --package=pnpm@10.28.2 -- pnpm typecheck
npm exec --cache .cache/npm --yes --package=pnpm@10.28.2 -- pnpm build
docker compose --env-file .env.example config --quiet
```

Run the full backend suite with `backend/.venv/bin/python -m pytest backend/tests -q`.
Persistence integration tests skip unless `TEST_DATABASE_URL` points to an explicitly isolated PostgreSQL service and a database named with a `test_` prefix. The test user must be able to create databases. Each test creates its own unique `test_` database, runs the public Alembic CLI, and drops only that database on teardown. Never point this at the live Compose service or persistent volume; SQLite is not a substitute. Run `backend/.venv/bin/python -m pytest backend/tests/test_persistence.py -q` after exporting the isolated URL. Without it, only configuration/model checks run, not database integration.

Reader checks: `backend/.venv/bin/python -m pytest backend/tests/test_reference_reader.py -q`.
With the same isolated `TEST_DATABASE_URL` exported, run public import integration checks using `backend/.venv/bin/python -m pytest backend/tests/test_reference_import.py -q`. These tests invoke the CLI, copy inputs into pytest temporary directories, and prove failed batches leave no persisted additions via repaired-batch inserted counts and repeat-import no-ops, without inspecting ORM internals.

The backend tests start a bounded, temporary Uvicorn subprocess on a random loopback port, assert health status/content type/JSON and unknown-route 404 behavior, and stop the process. They require neither PostgreSQL nor an extra HTTP client library.

After Compose startup, check `docker compose ps`, `curl --fail http://127.0.0.1:8000/health`, and `curl --fail http://127.0.0.1:3000/`; also open the UI in a browser.

## Scaffold overview

| Area | Scaffold decision |
| --- | --- |
| Backend | Approved uv, Python 3.12, FastAPI, Uvicorn; pytest for tests. `app/main.py` wires the HTTP router. |
| Boundaries | Domain entities are framework-independent; SQLAlchemy persistence models are separate. The transactional import adapter reuses the SQL models and connection factory; read-only PO queries are next. |
| Frontend | Approved pnpm, Next.js App Router, React, TypeScript; no UI framework or extra client libraries. |
| Containers | Python 3.12, Node.js 24, PostgreSQL 17, versioned uv image; frozen dependency installs. App containers run as non-root users. |
| Secrets | PostgreSQL variables are supplied only to database/backend services. No credentials or model/database calls are exposed to the frontend. |
| Health | API liveness only; database readiness is a separate Compose check. |

## Decision log

### D1 — Hexagonal backend architecture (Ports & Adapters)

**Decision:** Organize the single FastAPI backend into Domain, Application, Infrastructure, and Interfaces, with dependencies pointing inward. See [Architecture](docs/ARCHITECTURE.md) for responsibilities and the proposed layout.

**Rationale:** Keep invoice matching and business rules independent of the database, web framework, and external services. Application ports define the contracts these integrations must satisfy; infrastructure adapters provide their concrete implementations. This keeps PostgreSQL and Azure OpenAI details outside the business logic and allows the deterministic Three-Way Matching engine to be tested without external connections.

**Benefits:** Business behavior can evolve separately from integration details, and replacing or modifying an external technology has a more localized impact. This does not make technology changes free: adapters, data migrations, and integration tests may still need to change.

**Trade-off:** Explicit boundaries introduce additional interfaces, data conversions, and indirection compared with a conventional layered implementation. For a small application, that can increase navigation and implementation effort without an immediate payoff.

**Scope and guardrail:** Retain a single monolith and introduce ports only at real integration boundaries. Do not create an interface for every class or add abstractions for hypothetical future replacements. The goal is isolated, testable business rules, not maximum abstraction. The directory boundaries now contain domain entities and persistence adapters; deterministic matching and payment rules remain future work.

### D2 — Synchronous SQLAlchemy persistence and explicit Alembic migrations

**Decision:** Use approved SQLAlchemy 2, Alembic, and Psycopg 3 with synchronous database access, managed by uv. SQL models remain independent of the domain. Versioned migrations live in `backend/migrations`; use the explicit commands above, never startup migrations.

**Rationale/trade-off:** Conventional ORM models and migrations keep constraints reviewable without a custom SQL framework. Synchronous access is sufficient for this monolith; it does not provide asynchronous database concurrency.

**Storage boundaries:** PostgreSQL `NUMERIC` without a fixed scale preserves Decimal money/quantities, and `DATE` stores dates. Composite foreign keys bind each GRN line to the header's PO and the correct PO-local line. Item codes are not globally unique. Invoice references preserve order and raw nonexistent PO references; duplicate invoice numbers remain representable. No payment policy or supplier-activity restriction is introduced. Invoice children use restrictive foreign keys, not destructive cascades.

**Scope:** This decision established tables and migrations. The importer below reuses them; read-only PO queries are the next step.

### D3 — Atomic, immutable reference-data imports

**Decision:** Read the three challenge JSON arrays through an infrastructure reader, orchestrate identity/content/reference validation in an application use case through a minimal transaction port, and persist using the existing synchronous ORM models and engine factory. The CLI is a separate composition root; API startup is unchanged.

**Rationale/trade-off:** Identical reimports are safe no-ops; conflicting existing content fails instead of silently replacing reference history. The complete reference set is loaded for prospective validation, appropriate for this small challenge dataset rather than large-scale ingestion. PostgreSQL constraints remain the final guard. No new schema, automatic migrations, process-locking framework, invoice import, or business payment policy is introduced.

## Current limitations

This is local development infrastructure, not a production deployment: authentication, authorization, Azure OpenAI, uploads, matching, and audit workflows are intentionally absent. Container images favor a simple reproducible scaffold over optimized size; frontend build dependencies remain in the image. No open business-policy questions are resolved by this scaffold.
