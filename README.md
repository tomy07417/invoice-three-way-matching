# Invoice Three-Way Matching

A supplier-invoice validation system for the Machines Like Me technical challenge. Three-Way Matching compares what a supplier invoices with what was ordered and what was actually received, so discrepancies can be explained before a payment decision. This repository is an incremental implementation, not yet an end-to-end invoice-processing application.

## How the system works

The three documents serve different purposes:

| Document | What it establishes |
| --- | --- |
| Invoice | What the supplier is billing: quantities, unit prices, and purchase-order references. |
| Purchase Order (PO) | What was ordered, from which supplier, and at what agreed price. |
| Goods Received Note (GRN) | What was received; several GRNs can contribute to the received quantity of one PO line. |

The intended workflow is to upload an invoice, extract structured fields with Azure OpenAI, retrieve the relevant reference data, and run deterministic Python checks. **The model extracts data; it does not decide payment outcomes.** The opt-in backend accepts private uploads, processes durable Azure extraction jobs and atomically persists deterministic evaluations when mandatory evidence is available. The Intake frontend and human-review workflows remain pending. See [durable processing setup and retry rules](docs/INVOICE_PROCESSING.md). Structured invoices can also be evaluated through the application use case below.

The matching engine evaluates resolved input data with this precedence:

1. **Rejected:** a rejection rule is triggered, such as a missing/nonexistent PO, supplier mismatch, or an invoice dated before its PO.
2. **Held for Review:** no rejection takes precedence, but a discrepancy is found, such as excess invoiced quantity, a unit price outside tolerance, or a confirmed unmatched line.
3. **Auto-Pay:** every mandatory check is complete and none finds a discrepancy. This is an evaluation status, not execution of a payment.

Price tolerance defaults to **2% per line**, using exact Decimal arithmetic. The engine exposes a provisional outcome separately from its final outcome: missing mandatory evidence or unsupported comparison bases remain explicit, available checks still run, and **any pending mandatory check prevents a final status**. See [the engine contract and runnable example](#in-memory-matching-no-database-or-model).

## What works today

| Capability | Current state |
| --- | --- |
| Local stack | Next.js placeholder UI, FastAPI backend, PostgreSQL, and Docker Compose. |
| Reference data | Domain entities, explicit migrations, and an atomic, repeatable JSON importer for suppliers, POs, and GRNs. |
| PO inspection | Read-only HTTP endpoint with PO lines and received quantities aggregated across GRNs. |
| Matching | Pure in-memory engine for confirmed matching policies, explicit historical/approval inputs, structured reasons and missing-evidence reporting; not connected to an API or the UI. |
| Evaluation integration | Structured-input use case, consistent PostgreSQL context, versioned configuration, append-only evaluation/findings and evidence snapshots. No evaluation API/UI. |
| Extraction adapter | Async single-attempt Azure Responses adapter, strict nullable schema, exact Decimal/date parsing and sanitized retry classification; integrated with durable jobs. |
| Invoice workflow | Opt-in authenticated upload/status/detail/private-document API, durable extraction and atomic evaluation linkage. Frontend, corrections, human review, approvals and payments remain unimplemented. |

The client has clarified the nine previously open business questions; [the PRD](docs/PRD.md#official-client-clarifications) records those requirements separately from the accepted design policies below. The clarified pure rules are implemented independently of operational integration. Approval transactions, human decisions, corrected-invoice versioning, credit-note requests and a user-facing reevaluation workflow remain future work. Explicit evaluation of unchanged input now appends audited evidence without replacing earlier results.

## Architecture at a glance

The backend is a **single hexagonal monolith**, not a set of microservices:

- **Domain** (`backend/app/domain/`): framework-independent entities and `matching/`, which receives resolved facts and evaluates rules without I/O.
- **Application** (`backend/app/application/`): use cases and ports for orchestration. Reference-data import, PO queries and structured invoice evaluation exist.
- **Infrastructure** (`backend/app/infrastructure/`): concrete adapters, including PostgreSQL persistence and JSON reference-data reading.
- **Interfaces** (`backend/app/interfaces/`): HTTP routes and the import CLI. `main.py` wires the backend components together.

The Next.js frontend communicates with the FastAPI backend over HTTP; it never accesses the database or an extraction model directly. PostgreSQL stores reference data, raw invoices and automatic evaluation evidence. Human decisions and payment workflows are not wired up. See [Architecture](docs/ARCHITECTURE.md) for dependency rules and the proposed extension points.

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

Repeating `upgrade head` is safe. Rollback is explicit (`alembic downgrade base`, using the same config/location) and **drops these tables and their stored data**; it is not a troubleshooting step for a persistent development volume. Review backups and target before any downgrade. Downgrading only `0002` retains raw invoice tables but discards evaluation history, configurations, approved-consumption facts and the new identity/state metadata.

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

## Query a purchase order

After explicitly migrating and importing reference data, query the running backend:

```sh
curl --fail http://127.0.0.1:8000/purchase-orders/PO-2026-0102
```

`GET /purchase-orders/{po_number}` returns HTTP 200 with `po_number`, `supplier_id`, `po_date` (ISO `YYYY-MM-DD`), `currency`, `status`, and `lines` sorted by `line_number`. Each line contains `line_number`, `item_code`, `description`, `quantity_ordered`, `unit_price`, and `quantity_received`. All quantities and prices are JSON **strings**, preserving exact Decimal values without float conversion. For the supplied PO-2026-0102, received quantities are `"15"` and `"30"`; PO-2026-0107 receives `"8"` of `"10"` ordered.

Received quantity is the sum across all GRNs for that PO and its local line number. Ordered lines with no receipts remain present with `"0"`. This is a reference-data read, not a matching/payment decision: it does not subtract prior invoices or enforce supplier/PO status policy.

Unknown POs return HTTP 404 with `{"detail":"Purchase order not found"}`. Missing/invalid database configuration or database read failures return HTTP 503 with `{"detail":"Database unavailable"}`, without connection/SQL details. Database configuration and connections are lazy at query time: `/health` and unknown routes remain available without a configured database. API startup and reads never migrate, import, or write reference data. Interactive response schemas are available at `/docs`.

## Evaluate a structured invoice with PostgreSQL

`EvaluateInvoiceUseCase.execute(EvaluateInvoiceCommand(...))` is the public integration interface. It accepts a frozen domain `Invoice`, trusted actor/origin metadata, an explicit supplier ID, reliable zero-based line associations and verified net unit prices. It performs no extraction, fuzzy association, tax derivation or authentication. Entry points must establish caller trust before invoking it.

One PostgreSQL **REPEATABLE READ** transaction registers the input, reads reference data/configuration/other-case history/approved quantities, invokes the existing pure engine, and persists all findings and original context before returning. Failures, including commit failures, roll back staged invoice/evaluation changes; database errors expose no connection details. Reading all reference data is appropriate for the challenge dataset, not a large-scale query strategy.

- Migration `0002` preserves existing invoices and raw/nonexistent references. It adds nullable resolved supplier identity, technical state, `rule_configuration_versions`, `approved_payment_quantities`, `invoice_evaluations` and ordered `evaluation_rule_findings`.
- The **initially empty approval ledger is authoritative**, as confirmed by the owner. Evaluating only reads it: received, rejected, held and Auto-Pay evaluations never create approved consumption or payments. Future approval transactions must revalidate quantities atomically; snapshot isolation alone does not prevent overapproval.
- Configuration is selected by effective evaluation time, seeded with tolerance `0.02` and analyst limit `5000`. Each evaluation retains its configuration ID and exact values. There is no configuration-management endpoint or authorization workflow.
- Operational states are `received`, `evaluated` and `evaluation_incomplete`, separate from automatic outcomes. Missing mandatory evidence preserves `final_status=None`; an unresolved supplier on another invoice makes history unavailable, not authoritatively empty.
- Supply `invoice_id` to reevaluate **identical raw input** as the same case, excluding it from duplicate history. Each invocation appends a new evaluation. Different raw data raises a conflict; corrected versions, linked resubmissions and human-decision invalidation are not implemented.
- Evidence includes actor/origin/time, all PASS/HOLD/REJECT/PENDING findings and JSON snapshots with exact Decimal strings. Snapshots are detached copies and must not be edited by consumers. Append-only is the repository contract, not database-admin tamper protection.

### Application example

**This writes an invoice and evaluation.** Run only against a disposable database you intentionally configured, after `upgrade head` and the reference import above. It uses the supplied `PO-2026-0102`; it does not authorize payment. Re-running without the original `invoice_id` creates a different case and may trigger duplicate rejection. Results also depend on existing history and approvals.

<!-- application-evaluation-example -->
```sh
PYTHONPATH=backend backend/.venv/bin/python - <<'PY'
from decimal import Decimal as D
from app.application.dtos.invoice_evaluation import EvaluateInvoiceCommand
from app.application.use_cases.evaluate_invoice import EvaluateInvoiceUseCase
from app.domain.entities import Invoice, InvoiceLine
from app.domain.matching.types import LineAssociation
from app.infrastructure.persistence.connection import create_database_engine
from app.infrastructure.persistence.invoice_evaluation_repository import SqlAlchemyInvoiceEvaluationRepository
from app.infrastructure.persistence.reference_data_repository import SqlReferenceDataRepository

engine = create_database_engine()
try:
    with SqlReferenceDataRepository(engine).transaction() as transaction:
        reference = transaction.load()
    po = next(item for item in reference.purchase_orders if item.po_number == "PO-2026-0102")
    line = next(item for item in po.lines if item.line_number == 1)
    supplier = next(item for item in reference.suppliers if item.supplier_id == po.supplier_id)
    invoice = Invoice(supplier.legal_name, supplier.tax_id, "APPLICATION-EXAMPLE-001", po.po_date,
                      None, (po.po_number,), po.currency, line.unit_price, D("0"), line.unit_price,
                      (InvoiceLine(line.description, D("1"), line.unit_price, line.unit_price, None),))
    command = EvaluateInvoiceCommand(
        invoice, actor_id="local-example", supplier_id=supplier.supplier_id,
        associations={0: LineAssociation(po.po_number, line.line_number)},
        net_unit_prices={0: line.unit_price},
    )
    saved = EvaluateInvoiceUseCase(SqlAlchemyInvoiceEvaluationRepository(engine)).execute(command)
    print(f"invoice_id={saved.invoice_id} evaluation_id={saved.evaluation_id} "
          f"status={saved.result.final_status} complete={saved.result.complete}")
finally:
    engine.dispose()
PY
```

The printed IDs identify persisted records. Use `repository.transaction()` and `transaction.get_evaluation(id)` / `list_evaluations(invoice_id)` to retrieve historical evidence, rather than rerunning matching against today's context. A successful complete result can still be Held for Review or Rejected.

## Extract a validated document (backend adapter only)

`AzureOpenAIExtractor.extract(ValidatedDocument)` returns an `ExtractionResult`, not a payment outcome or a domain invoice ready for evaluation. Missing fields remain `None`; warnings and raw PO references are retained. Decimal values must be finite decimal strings, dates must be valid `YYYY-MM-DD`, and unexpected fields are rejected. Negative values are preserved without inventing credit-note policy. Non-invoices return `is_invoice=False` and `invoice=None`; malformed, truncated or inconsistent responses raise a safe `ExtractionError`.

The backend-only API example below assumes `validated_document` was obtained from a trusted validator. **File validation and upload are not implemented by this adapter.** It reads already-exported variables, never loads `.env`, and must not run in the browser. The endpoint must be the exact supplied HTTPS Responses URL; the adapter does not append paths, change API versions or follow redirects. Set the backend-only `AZURE_OPENAI_DEPLOYMENT` to the assigned `TomasAmundarain-LIA3` deployment. Both constructors require an explicit deployment; the adapter sends it verbatim as `model`, with no inferred default or normalization. Empty, non-string or whitespace-containing deployment values are rejected before any request.

```python
import os
from app.infrastructure.extraction.azure_openai import AzureOpenAIExtractor, AzureExtractionConfig

# Create once at backend startup, reuse across extractions.
extractor = AzureOpenAIExtractor.create(
    endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
    api_key=os.environ["AZURE_OPENAI_API_KEY"],
    deployment=os.environ["AZURE_OPENAI_DEPLOYMENT"],
    config=AzureExtractionConfig(max_connections=1, max_output_tokens=3000),
)
try:
    result = await extractor.extract(validated_document)
    # Retain uncertainty; establish mandatory evidence before domain evaluation.
finally:
    await extractor.aclose()  # application shutdown, not after every document
```

To borrow an existing reusable `httpx.AsyncClient`, pass it as the first constructor argument with `endpoint`, `api_key`, `deployment` and optional `config`. Its owner configures bounded connection limits and must disable transport retries, credential-forwarding hooks and unsafe logging. `extractor.aclose()` never closes a borrowed client. The owned-client factory disables environment proxy configuration, transport retries and redirects; it uses configurable bounded connection/keepalive limits. Both forms enforce connect/read/write/pool and total-request timeouts, and propagate cancellation.

PDFs are sent directly as base64 `input_file`; PNG/JPEG use base64 `input_image`. Requests use `store:false`, low reasoning effort and strict `text.format` JSON schema with all keys required and unknown values nullable. The model is instructed to extract only, never approve or reject payment. Provider error bodies, transport exception chains, filenames and credentials are not included in adapter errors or logs (the adapter does not log). Successful extracted text/warnings remain private document data, not safe log messages.

**Retry and throughput boundary:** each `extract` call makes one HTTP request, without retry sleeps. HTTP 429, 5xx, network failures/timeouts and malformed responses are retryable attempt failures; 401/403 and other non-200 responses (including redirects/400) are terminal. Valid `Retry-After` seconds or HTTP dates are returned as a delay, not slept inside the adapter. Unknown token consumption after failure is not zero. Successful responses require valid, consistent token counters. The [durable processor](docs/INVOICE_PROCESSING.md) owns finite retries with exponential backoff/jitter, database-coordinated active-call limits and conservative token reservations; connection limits alone do not provide global concurrency or TPM enforcement.

Verification uses `httpx.MockTransport`, not Azure or invoice uploads:

```sh
PYTHONPATH="$PWD/backend:$PWD" backend/.venv/bin/python -m pytest backend/tests/test_azure_extraction*.py backend/tests/test_document_contracts.py backend/tests/test_evaluation_contracts.py backend/tests/test_evaluate_invoice_use_case.py
```

This adapter remains single-attempt. The [integrated backend](docs/INVOICE_PROCESSING.md) supplies authentication, private storage and durable orchestration without changing deterministic matching rules.

## In-memory matching (no database or model)

The engine implements the confirmed pure rules from the supplied challenge and subsequent official client clarifications. It does **not** expose a matching API, execute payments, persist decisions or implement the human-review/audit workflows. Run this example from the repository root after installing backend dependencies:

```sh
PYTHONPATH=backend backend/.venv/bin/python - <<'PY'
from datetime import date
from decimal import Decimal as D
from app.domain.entities import (
    Invoice, InvoiceLine, PurchaseOrder, PurchaseOrderLine,
    GoodsReceivedNote, GoodsReceivedNoteLine, Supplier,
)
from app.domain.matching.types import LineAssociation
from app.domain.matching.engine import evaluate

po = PurchaseOrder("EXAMPLE-PO", "SUP-X", date(2026, 6, 1), "EUR", "open",
                   (PurchaseOrderLine(7, "PART-X", "A fitting", D("5"), D("100")),))
invoice = Invoice("Example supplier", "RAW-TAX", "NEW-739", date(2026, 6, 2),
                  None, (po.po_number,), "EUR", D("515"), D("0"), D("515"),
                  (InvoiceLine("Extracted fitting", D("5"), D("103"), D("515"), "raw / 7"),))
grn = GoodsReceivedNote("EXAMPLE-GRN", po.po_number, date(2026, 6, 2),
                        (GoodsReceivedNoteLine(7, D("5")),))
supplier = Supplier("SUP-X", "Example supplier", "RAW-TAX", "EUR", "active", "Address")
result = evaluate(
    invoice, (po,), (grn,), supplier=supplier,
    associations={0: LineAssociation(po.po_number, 7)},
    approved_quantities={},  # Authoritative snapshot: nothing approved previously.
    invoice_history=(),       # Authoritative snapshot: no other invoice cases.
    net_unit_prices={0: D("103")},  # Explicit verified net price, not tax inference.
)
print(result.provisional_status, result.final_status, result.complete)
print([rule.code for rule in result.pending])
PY
```

Expected output:

```text
Held for Review Held for Review True
[]
```

### Result and caller contract

- `evaluate` returns a frozen `EvaluationResult`: `provisional_status`, `final_status`, tuple `rules`, the immutable `config` snapshot, plus `pending` and `complete` properties. Each `RuleResult` has a stable code, `PASS`/`REJECT`/`HOLD`/`PENDING`, explanatory reason, and optional PO number, zero-based invoice line index and PO-local line number.
- The engine alone applies `Rejected > Held for Review > Auto-Pay`. Available checks always run; pending checks are not passes. Provisional `Auto-Pay` means only **no discrepancy in available checks**, never permission to pay. Any mandatory pending check makes `complete=False` and `final_status=None`, even with provisional rejection/hold. Only a complete evaluation yields exactly one final business status.
- Supply a resolved `supplier` entity: name/tax ID are not normalized or guessed. Every referenced PO is checked for existence, ownership, date and status. Inactive suppliers and closed POs hold; an explicit rejection always wins. Unknown supplier/PO status remains pending, never silently active/open.
- Association mappings use zero-based invoice indices. `LineAssociation` identifies one reliable whole-line PO/local-line target; absent, `None`, ambiguous or invalid targets hold the invoice. Raw descriptions/reference text are never parsed or fuzzy-matched. Duplicate context identities and out-of-range indices raise `ValueError`.
- Availability is all supplied matching GRN quantities minus `approved_quantities` for that PO-local target. Aggregate invoice lines sharing a target before comparing; any excess holds the whole invoice. GRNs represent actual receipts available at evaluation, with no invoice-date cutoff. History records alone never consume quantity. Application must provide only quantities previously approved for payment, not merely received/held invoices; a future transaction must revalidate this snapshot when approving.
- `approved_quantities` uses `LineAssociation` keys and exact Decimal totals. `None` means unavailable evidence; `{}` is an authoritative empty approved ledger, with absent targets then meaning zero. `invoice_history=None` is missing evidence; `()` is confirmed empty history. These are not policy-bypass switches.
- `InvoiceHistoryEntry` retains supplier ID, original number, date, currency and total for other cases. Same-supplier normalized number rejects regardless of amount/date/currency. A different normalized number with identical total/date holds only within the same supplier and currency. Normalization removes whitespace and ASCII hyphens and ignores case, preserving raw values. Application must exclude this case's versions; linked resubmissions, audit and repeat-payment protection are not implemented here.
- Supply verified `net_unit_prices` by zero-based line index; missing values remain pending. Raw extraction fields are unchanged. Compare prices only in the PO currency; mismatch holds without conversion. Nonpositive PO reference prices and nonfinite comparison inputs remain pending; no per-line tax allocation or credit arithmetic is invented. The former `PolicyFacts` clearance/stub interface is replaced by these explicit inputs.
- `MatchingConfig` defaults to fractional `Decimal('0.02')` price tolerance and `Decimal('5000')` analyst approval limit; both must be finite nonnegative Decimals. Exact ±2% passes, strictly greater differences hold. No arbitrary upper tolerance bound is imposed. The analyst limit is carried only, not enforced as a payment rule.

Focused checks: `backend/.venv/bin/python -m pytest backend/tests/test_matching_rules.py backend/tests/test_matching_duplicates.py backend/tests/test_matching_engine.py -q`.

## Verification

### Application integration verification record

The integrated implementation passed **256 backend tests with no failures or skips** on disposable PostgreSQL 17, including **55 new tests**: seven command contracts, 30 application tests, seven adapter tests, nine cross-layer/example tests and two migration/constraint tests. The full run completed in 101.55s. The application example is tested from its literal README source with exact stdout, empty stderr and persisted evidence checks. Independent verification passed all feature specifications, 55 focused checks and three isolated migration/rollback/concurrent-context probes. Those three probes were then promoted into `test_evaluation_independent.py`, using public repository observations for business state, and passed separately in 3.10s; production code did not change after the 256-test full run.

A separate differential against `f562a53` ran the previous and new import CLI and real HTTP servers against the new schema with persisted incomplete-evaluation data. Repeat imports, rejected source/unsupported inputs, health, PO inspection and unknown resources retained identical CLI exit/stdout/stderr and HTTP status/body. Failed imports and reads left invoice evidence, technical state and approved totals unchanged. Original migration0001, domain rules, HTTP routes and dependencies were not changed.

### Confirmed-policy verification record

The confirmed-policy implementation passed **201 backend tests with no skips** against a disposable PostgreSQL instance, including **129 matching tests**. The documented in-memory example also passed with exact stdout and empty stderr. These are observed local results for this implementation, not production certification or evidence that the deferred workflows exist.

Coverage includes approved-only consumption, grouped quantities, duplicate normalization, same-supplier/currency similarity, net-price tolerance, every referenced PO, inactive/closed states, ambiguous associations, later GRNs, missing evidence, precedence and immutable evaluation snapshots.

Corrections/versioning, human-decision invalidation, credit-note payment blocks, a user-facing reevaluation workflow, authorization and atomic approval recording still require application/persistence work. Automatic evaluation persistence and explicit same-input reevaluation are now implemented through the application interface above. A successful matching evaluation alone never authorizes a payment.

### Run checks

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

With the isolated `TEST_DATABASE_URL` exported, run `backend/.venv/bin/python -m pytest backend/tests/test_purchase_order_query.py -q`. Query tests seed unique databases through the public importer CLI and use real HTTP to verify cross-GRN sums, partial receipts, PO-local isolation, zero receipts, ordering, exact decimals, safe unknown identities, repeat-import stability, and rejected-import preservation. They do not inspect ORM storage.

The health tests cover absent, malformed, and unreachable database configuration (including safe query 503 responses). They start a bounded, temporary Uvicorn subprocess on a random loopback port, assert health status/content type/JSON and unknown-route 404 behavior, and stop the process. They require neither PostgreSQL nor an extra HTTP client library.

After Compose startup, check `docker compose ps`, `curl --fail http://127.0.0.1:8000/health`, and `curl --fail http://127.0.0.1:3000/`; also open the UI in a browser.

## Scaffold overview

| Area | Scaffold decision |
| --- | --- |
| Backend | Approved uv, Python 3.12, FastAPI, Uvicorn; pytest for tests. `app/main.py` wires the HTTP router. |
| Boundaries | Domain entities are framework-independent; SQLAlchemy persistence models are separate. Transactional import and read-only PO query adapters reuse the same SQL models and connection factory. |
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

**Scope and guardrail:** Retain a single monolith and introduce ports only at real integration boundaries. Do not create an interface for every class or add abstractions for hypothetical future replacements. The goal is isolated, testable business rules, not maximum abstraction. The directory boundaries now contain domain entities, the pure matching engine, application evaluation and persistence adapters; extraction orchestration, HTTP evaluation integration and human-review workflows remain future work.

### D2 — Synchronous SQLAlchemy persistence and explicit Alembic migrations

**Decision:** Use approved SQLAlchemy 2, Alembic, and Psycopg 3 with synchronous database access, managed by uv. SQL models remain independent of the domain. Versioned migrations live in `backend/migrations`; use the explicit commands above, never startup migrations.

**Rationale/trade-off:** Conventional ORM models and migrations keep constraints reviewable without a custom SQL framework. Synchronous access is sufficient for this monolith; it does not provide asynchronous database concurrency.

**Storage boundaries:** PostgreSQL `NUMERIC` without a fixed scale preserves Decimal money/quantities, and `DATE` stores dates. Composite foreign keys bind each GRN line to the header's PO and the correct PO-local line. Item codes are not globally unique. Invoice references preserve order and raw nonexistent PO references; duplicate invoice numbers remain representable. No payment policy or supplier-activity restriction is introduced. Invoice children use restrictive foreign keys, not destructive cascades.

**Scope:** This decision established tables and migrations. The importer and read-only PO query below reuse them.

### D3 — Atomic, immutable reference-data imports

**Decision:** Read the three challenge JSON arrays through an infrastructure reader, orchestrate identity/content/reference validation in an application use case through a minimal transaction port, and persist using the existing synchronous ORM models and engine factory. The CLI is a separate composition root; API startup is unchanged.

**Rationale/trade-off:** Identical reimports are safe no-ops; conflicting existing content fails instead of silently replacing reference history. The complete reference set is loaded for prospective validation, appropriate for this small challenge dataset rather than large-scale ingestion. PostgreSQL constraints remain the final guard. No new schema, automatic migrations, process-locking framework, invoice import, or business payment policy is introduced.

### D4 — Read-only PO query with exact received totals

**Decision:** Expose a minimal application query port, immutable Decimal/date DTOs, and a use case, wired to a synchronous SQLAlchemy adapter and HTTP response schemas by `main.py`. Aggregate GRN lines by PO and local line before an outer join to ordered lines; bind the incoming PO number as a SQLAlchemy parameter.

**Rationale/trade-off:** Keep the query independent of HTTP and ORM details, preserve zero-receipt lines and exact quantities, and avoid joined-row multiplication. An engine is created lazily for each query and disposed afterward, favoring simple resource ownership over connection-pool reuse for this small read-only milestone. No migrations, imports, schema changes, or payment policy occur on reads.

### D5 — Partial deterministic matching with explicit completeness

**Historical status:** The initial pending-policy interface below was superseded by the official clarifications and decisions D7-D13. Current callers supply explicit supplier/history/approved-quantity/net-price facts; use the current contract above, not the old clearance switches.

**Decision:** Reuse frozen domain entities, pure rejection/quantity/price checks, and one precedence engine. Implement the owner's supplied sections 7/7.1, with S6's correction: unresolved policies do not suppress available checks. Return provisional implemented-check status separately from final status/completeness. This is a partial implementation, not completion of all challenge business policies; the original PDF itself was not inspected for this work.

**Initial trade-off:** Caller-verified supplier identity, associations and named policy facts originally kept unresolved business decisions outside the engine without a generic policy framework. Duplicate criteria, partial invoicing, currency/tax treatment, inactive suppliers, closed POs and split/multi-PO allocation were initially pending; the confirmed rules now replace those placeholders, with grouped quantities preventing receipt reuse. Changing an immutable configuration snapshot affects only the evaluation receiving it; existing results remain unchanged. Automatic reevaluation after parameter changes, corrections or later GRNs remains pending, as do manager configuration storage/endpoints, authorization, human overrides and audit history. No claim of their implementation is made here.

### D6 — Group matching by business capability

**Decision:** Keep the engine, rules, types, and pending-policy hooks together in `domain/matching/`. This is an organizational refactor of domain services, not a new layer or a change to any rule, result contract, configurable parameter, precedence, or pending-policy behavior.

**Rationale and boundary:** A capability-specific module makes the core business responsibility easier to find than a generic `services/` directory. The engine still receives resolved data and performs no I/O. Future application use cases may retrieve data through ports, invoke the engine, and persist results; no `evaluate_invoice.py` use case, repository integration, compatibility wrappers, or new abstractions are introduced here.

### D7 — Received quantity less approved consumption

**Client requirement:** Only quantities previously approved for payment reduce availability; merely received or held invoices do not. Excess quantity holds the entire invoice, not part of it. Receipts actually available at evaluation count regardless of their date relative to the invoice.

**Design:** Application supplies an authoritative PO-local approved-quantity snapshot and the received GRNs. Sum invoice lines sharing a reliable PO-line target before comparing against receipts minus approved quantities. Missing context is not zero consumption. Atomic revalidation and approval recording belong to a later transaction; a pure snapshot is not concurrency protection.

### D8 — Explicit duplicate-number normalization

**Client requirement:** Same supplier and invoice number rejects regardless of file/amount; different number with identical amount/date requires review.

**Design:** Ignore whitespace, hyphens and case in the comparison key, retaining the original number. The approved alert scope is the same supplier and currency, using invoice total and invoice date. Duplicate-number rejection itself does not depend on amount or currency. Application must supply history excluding the current case; linked versions and repeat-payment guards are later workflow responsibilities.

### D9 — Net prices and cross-currency review

**Client requirement:** Net-of-tax unit prices, inclusive symmetric 2% tolerance, no silent comparison across currencies.

**Design:** Supply explicit net unit prices without modifying raw extracted values. Currency mismatch yields Held for Review without conversion. Do not derive per-line net prices from invoice-wide tax totals or invent a percentage basis for nonpositive PO prices.

### D10 — Supplier activity, closed POs and reliable associations

**Client requirement:** Inactive suppliers never Auto-Pay; closed POs alone do not reject; every referenced PO and supplier must be checked; unreliable line associations require review.

**Design:** Inactive supplier and closed PO produce hold findings without suppressing any rejection or matching checks. A line must target one reliable PO-local line; do not guess from descriptions or silently split it across POs. Rejected always wins over Held for Review.

### D11 — Corrections and linked resubmissions

**Client requirement:** Relevant data corrections require reevaluation, invalidate the applicability of earlier human decisions, and preserve all versions/decisions.

**Design:** A corrected rejected submission may create a linked version of the same case. Keep automatic evaluations, operational state, human decisions and audit separate. Linking a version never authorizes a second payment. Persisted versioning, decision invalidation, audit and transactional payment protection are not implemented by the pure engine.

### D12 — Receipt and parameter reevaluation policy

**Client requirement:** Use receipts available when evaluating; record parameter changes with actor/time.

**Design:** New GRNs permit manual audited reevaluation rather than automatic import-triggered processing. New parameter snapshots affect future evaluations only, leaving historical snapshots/results intact. Explicit same-input reevaluation now appends automatic evaluation evidence; a user-facing manual workflow and parameter-management authorization/audit remain later stages.

### D13 — Credit-note request as a payment block

**Client requirement:** Request comment and audit are mandatory; payment is blocked while pending; no full credit-note workflow.

**Design:** Keep this operational block separate from the automatic matching result and historical human decisions. Request persistence, comment validation at that workflow boundary, audit and payment authorization checks are deferred; an Auto-Pay evaluation alone never authorizes payment.

### D14 — Transactional structured-invoice evaluation

**Decision:** Add one evaluation transaction port, immutable command/configuration/record DTOs and a PostgreSQL adapter around the unchanged domain engine. Reuse the session-bound reference repository and existing raw invoice tables, with a new `0002` migration rather than rewriting `0001`. Select effective configuration and retain full input/context, ordered findings and actor/time evidence for every invocation.

**Rationale/trade-off:** REPEATABLE READ keeps multiple context queries coherent and registration/results atomic, but serialization failures may require an explicit caller retry; no automatic retries or concurrent approval protection are provided. Existing `invoice_id` requires identical raw input, enabling self-history exclusion without implementing correction/version workflows. The owner confirmed an initially empty authoritative approved-quantity ledger. Technical state is never approval, and append-only application history is not an admin-proof audit log. No new dependencies or evaluation endpoint are introduced.

### D15 — Strict single-attempt Azure extraction

**Decision:** Use approved httpx with a reusable async client and direct base64 PDF/image input, strict nullable extraction schema and exact Decimal/date conversion into existing draft DTOs. The adapter sends `store:false` to the exact configured URL with the assigned deployment supplied explicitly from backend environment configuration; no executable model default is hardcoded. Prior limited synthetic connectivity/PDF probes informed direct PDF support; this slice adds simulated contract tests, not new live proof.

**Boundary/trade-off:** Retry scheduling belongs to the future durable processor, preserving one request per attempt and unknown token usage on failure. Safe error codes and optional `Retry-After` delays support that processor without retaining provider exception chains. Transport limits and timeouts bound resources, but do not implement durable admission, global concurrency, token reservations or retries. Incomplete evidence is not an automatic payment decision.

## Current limitations

This is a local-demo backend, not a production deployment. Authenticated private uploads, durable extraction retries and deterministic evaluation integration are implemented; the Intake frontend, corrections, human decisions and audit views remain pending. PostgreSQL-backed integration tests passed, but the latest real Azure workflow remained in RETRY_WAIT with provider_unavailable, so full live end-to-end verification is still pending. Container images favor a simple reproducible scaffold over optimized size. The official business answers are implemented in pure matching rules; undefined arithmetic and approval-limit policies are not silently invented.
