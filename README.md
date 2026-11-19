# Invoice Three-Way Matching

A supplier-invoice validation system for the Machines Like Me technical challenge. Three-Way Matching compares what a supplier invoices with what was ordered and what was actually received, so discrepancies can be explained before a payment decision. This repository is an incremental implementation, not yet an end-to-end invoice-processing application.

## How the system works

The three documents serve different purposes:

| Document | What it establishes |
| --- | --- |
| Invoice | What the supplier is billing: quantities, unit prices, and purchase-order references. |
| Purchase Order (PO) | What was ordered, from which supplier, and at what agreed price. |
| Goods Received Note (GRN) | What was received; several GRNs can contribute to the received quantity of one PO line. |

The intended workflow is to upload an invoice, extract structured fields with Azure OpenAI, retrieve the relevant reference data, and run deterministic Python checks. **The model extracts data; it does not decide payment outcomes.** The opt-in backend accepts private uploads, processes durable Azure extraction jobs and atomically persists deterministic evaluations when mandatory evidence is available. The Intake frontend and AP review workspace are implemented; controlled-provider browser acceptance passed. Independent AP verification covered backend execution and frontend contract inspection; independent frontend execution was unavailable. Live Azure end-to-end proof remains a separate gap. See [Intake setup and limitations](docs/INVOICE_INTAKE.md) and [durable processing setup and retry rules](docs/INVOICE_PROCESSING.md). Structured invoices can also be evaluated through the application use case below.

The matching engine evaluates resolved input data with this precedence:

1. **Rejected:** a rejection rule is triggered, such as a missing/nonexistent PO, supplier mismatch, or an invoice dated before its PO.
2. **Held for Review:** no rejection takes precedence, but a discrepancy is found, such as excess invoiced quantity, a unit price outside tolerance, or a confirmed unmatched line.
3. **Auto-Pay:** every mandatory check is complete and none finds a discrepancy. This is an evaluation status, not execution of a payment.

Price tolerance defaults to **2% per line**, using exact Decimal arithmetic. The engine exposes a provisional outcome separately from its final outcome: missing mandatory evidence or unsupported comparison bases remain explicit, available checks still run, and **any pending mandatory check prevents a final status**. See [the engine contract and runnable example](#in-memory-matching-no-database-or-model).

## What works today

| Capability | Current state |
| --- | --- |
| Local stack | Next.js Intake/AP/Manager/Audit routes, FastAPI backend, PostgreSQL, and Docker Compose. Real browser acceptance uses an isolated database and controlled extractor. |
| Reference data | Domain entities, explicit migrations, and an atomic, repeatable JSON importer for suppliers, POs, and GRNs. |
| PO inspection | Read-only HTTP endpoint with PO lines and received quantities aggregated across GRNs. |
| Matching | Pure deterministic engine with explicit historical/approval inputs, structured reasons and missing-evidence reporting; invoked by durable processing. |
| Evaluation integration | Structured-input use case, consistent PostgreSQL context, versioned configuration, append-only evaluation/findings and evidence snapshots; available through document detail. |
| Extraction adapter | Async single-attempt Azure Responses adapter, strict nullable schema, exact Decimal/date parsing and sanitized retry classification; integrated with durable jobs. |
| Invoice workflow | Private Intake, durable extraction/evaluation, versioned AP corrections, manual reevaluation, human decisions, quantity-safe approval, escalation and audit views. No banking payments. |

The client has clarified the nine previously open business questions; [the PRD](docs/PRD.md#official-client-clarifications) records those requirements separately from the accepted design policies below. The clarified pure rules are implemented independently of operational integration. The AP workflow below implements approval transactions, versioned corrections, human decisions, credit-note payment blocks and manual reevaluation, separately from the unchanged deterministic engine. Explicit evaluation of unchanged input now appends audited evidence without replacing earlier results.

## Architecture at a glance

The backend is a **single hexagonal monolith**, not a set of microservices:

- **Domain** (`backend/app/domain/`): framework-independent entities and `matching/`, which receives resolved facts and evaluates rules without I/O.
- **Application** (`backend/app/application/`): use cases and ports for orchestration. Reference-data import, PO queries and structured invoice evaluation exist.
- **Infrastructure** (`backend/app/infrastructure/`): concrete adapters, including PostgreSQL persistence and JSON reference-data reading.
- **Interfaces** (`backend/app/interfaces/`): HTTP routes and the import CLI. `main.py` wires the backend components together.

The Next.js frontend communicates with the FastAPI backend over HTTP; it never accesses the database or an extraction model directly. PostgreSQL stores reference data, raw invoices and automatic evaluation evidence. Versioned human decisions and compensating approval releases are persisted by the AP workflow; banking payments are out of scope. See [Architecture](docs/ARCHITECTURE.md) for dependency rules and the proposed extension points.

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
- The **initially empty approval ledger is authoritative**, as confirmed by the owner. This low-level structured evaluation interface only reads it and never authorizes payment. Document processing and AP reevaluation separately perform the operational automatic approval described in D23, revalidating quantities atomically; snapshot isolation alone does not prevent overapproval.
- Configuration is selected by effective evaluation time, seeded with tolerance `0.02` and analyst limit `5000`. Each evaluation retains its configuration ID and exact values. There is no configuration-management endpoint or authorization workflow.
- Operational states are `received`, `evaluated` and `evaluation_incomplete`, separate from automatic outcomes. Missing mandatory evidence preserves `final_status=None`; an unresolved supplier on another invoice makes history unavailable, not authoritatively empty.
- Supply `invoice_id` to reevaluate **identical raw input** as the same case, excluding it from duplicate history. Each invocation appends a new evaluation. Different raw data raises a conflict through this low-level interface; uploaded cases use the separate AP correction workflow for versioning and human-decision invalidation.
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

The instruction explicitly distinguishes thousands/grouping separators from the decimal separator using the document's consistent formatting, not its currency or supplier location: `1,500.40` and `1.500,40` must both become `"1500.40"`, while `1,831.94` becomes `"1831.94"`, never `"1.83194"`. Ambiguous formatting must produce a null field and warning, not a guess. This is model guidance, not proof that a live extraction is correct; it neither repairs stored invoices nor changes the strict parser, precision, price-basis policy or deterministic matching.

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

At that historical checkpoint, corrections and atomic approval still required application work. The PRD03 AP workflow below now implements these boundaries; the historical pure-rule result is not their verification record. Automatic evaluation persistence and explicit same-input reevaluation are now implemented through the application interface above. A successful matching evaluation alone never authorizes a payment.

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

## AP review workspace (PRD03)

AP Analyst and Manager accounts can open **AP review** in the navigation or visit `/review`. Intake accounts cannot access this workspace or its backend API. The queue defaults to Held for Review; choose **All outcomes / incomplete cases** to find a case after its automatic result changes. Filters cover supplier, invoice date, exact amount, reason, operational state and age, with sorting and pagination.

Initial processing and AP reevaluation now resolve a line against referenced PO lines using a valid explicit identity first, otherwise a unique exact description after case/whitespace normalization. Codes, numbers and punctuation are retained; no price/quantity filtering, fuzzy matching or arbitrary first-candidate selection occurs. Partial structured references remain unresolved even when raw text could fill the missing half. Approval repeats the same resolution against fresh reference data before checking summed receipts minus active approved consumption. Extracted PO fields are never rewritten, and unknown net price basis remains pending, not Auto-Pay.

The detail pairs the private original with original/current extracted headers and evaluation-time PO/GRN evidence. **Correct extraction** opens nullable header/line fields and explicit PO-local associations. Decimal inputs remain strings; blank fields mean unknown, while an empty known PO list means none. Enter a reason, preview changed fields, then confirm the correction. This revokes existing authorization with audit; enter a new comment and select **Reevaluate** before making a new decision. Reevaluation also supersedes the current decision and never calls Azure.

Approval, rejection, credit-note requests and Manager escalation require comments. Approval, rejection and credit-note requests also require explicit confirmation before a mutation is sent; cancelling retains the comment and changes no revision or audit. Controls reflect server eligibility rather than browser amount arithmetic. A stale version or uncertain mutation pauses actions until **Refresh case**; mutations are not automatically replayed. History distinguishes current versions/decisions from historical evaluations, decisions and compensating reversals.

Observed self-verification: **599 backend tests**, no skips, against isolated PostgreSQL; **31 frontend tests on Node 24**, TypeScript and production build; real Chrome AP acceptance plus unchanged Intake regression. Independent backend verification passed 156 focused tests and two additional quantity/receipt probes, now retained as regression tests and separately passed by the parent. It found a comparison-row rendering defect: focused actual Chrome reproduced RED, then passed after the type/render correction and a fresh Node24 build. One targeted independent contract inspection confirmed that original blocker resolved, with browser evidence explicitly attributed to the parent. Its optional frontend test/typecheck attempt remained unavailable because scratch dependency links lacked the pnpm store. A third supplemental probe observed one approval/correction winner (200/409) before its own stale-CSRF fixture failed; remaining probe assertions were not executed. No independent frontend execution or native approval is claimed. These checks use a controlled extractor and make no Azure or banking calls.

Use the existing opt-in processing setup and run `alembic upgrade head` before starting the API. Migration `0005` adds review tables without rewriting original uploads, raw invoices, automatic evaluations or approval facts. Provision AP accounts with the existing operator CLI (`--role ap_analyst` or `--role ap_manager`); no public signup or new authentication mechanism is introduced.

| Endpoint | Purpose |
| --- | --- |
| `GET /review` | Held queue by default; supplier, invoice-date range, exact min/max amount, readable reason, operational state, minimum age, order and pagination filters. `outcome=all` includes other automatic outcomes/incomplete cases. |
| `GET /review/{id}` | Current AP extraction, original extraction, evaluation-time PO/GRN comparison, eligibility and payment block. |
| `GET /review/{id}/history` | Original and corrected versions, automatic evidence, human decisions, actor/time/comment and applicability. |
| `PATCH /review/{id}` | Full nullable extraction under `invoice` plus mandatory `comment`; blocks decisions until reevaluation. Decimal inputs must be strings; the body limit is 256 KiB. |
| `POST /review/{id}/reevaluations` | Deterministic reevaluation with mandatory `comment`; no Azure call. |
| `POST /review/{id}/decisions` | `action`: `approve`, `reject` or `request_credit_note`, with mandatory `comment`. |
| `POST /review/{id}/escalations` | Audited request for above-limit/non-USD Manager approval, with mandatory `comment`. |

All routes require an AP session; mutations additionally require the existing CSRF header and `If-Match` containing the quoted revision returned by the detail/history ETag (for example `"0"`). Missing revisions return 428; stale edits/decisions return 409 without new audit or consumption. Refresh and review current evidence instead of blindly retrying an uncertain mutation.

The initial Analyst approval limit is **5,000 USD inclusive**. Eligibility uses the limit captured by the case's current evaluation, not a later parameter edit. Above that evaluated limit or in another currency, Manager is required; no FX occurs. Approval additionally requires complete current evidence, reliable positive PO-line quantities/net prices, no active same-supplier normalized-number authorization, and sufficient currently received/unconsumed quantities. An override does not invent missing evidence or receipts. A rejected case consumes nothing. Credit-note requests retain their operational payment block across reevaluation; the external resolution workflow is out of scope.

A relevant correction revokes the prior authorization and releases its consumption with an append-only audited reversal. The original decision, raw versions and automatic results remain historical evidence. Initial document processing and AP reevaluation automatically create operational `APPROVED` with payment unblocked for complete `Auto-Pay`, with a system decision and fresh receipt consumption in the same transaction. A pending credit-note request blocks this transition. A human rejection remains applicable across unchanged reevaluation, even when the new outcome becomes Auto-Pay; only a correction invalidates it. Reevaluation does not rerun extraction. Original Intake endpoints retain the original document/extraction/evaluation; use the AP detail/history for the versioned review state.

One short PostgreSQL READ COMMITTED transaction serializes initial document completion and review mutations using the same global advisory lock, with shared reference-table locks against imports. AP transactions retain bounded statement/lock waits. Fresh consumption is read after waiting; concurrent approvals cannot reserve the same receipts twice. This deliberately favors understandable local-demo correctness over high throughput; no queue, cache or service was added. Approval authorizes payment but does not execute a bank transfer.

### Reproduce AP acceptance

After dependencies are installed, export `TEST_DATABASE_URL` for an isolated PostgreSQL service/database with a `test_` prefix and create-database permission. The fixture creates and drops only its own disposable database. It provisions synthetic accounts and uses a controlled extractor; never point it at application data.

```sh
node --test frontend/tests/api.test.mjs frontend/tests/backend-proxy.test.mjs frontend/tests/intake-state.test.mjs frontend/tests/review-transport.test.mjs frontend/tests/review-state.test.mjs
node --test frontend/tests/cdp-cleanup.test.mjs
```

Browser checks require installed Google Chrome at `/usr/bin/google-chrome` and an already-built/running frontend on loopback port 3212, configured with server-only `BACKEND_INTERNAL_URL=http://127.0.0.1:3211`, `FRONTEND_ORIGIN=http://127.0.0.1:3212` and `PORT=3212`. The script starts/stops its own real FastAPI fixture on 3211; reserve both ports. It checks role denial, comment enforcement, double-click protection, correction/release/reevaluation/history, stale edits, Manager escalation, credit-note payment blocks, narrow mobile layout and real session-expiry return paths.

```sh
node frontend/tests/review-browser.mjs
# Narrow regression for readable per-line messages and PASS filtering:
AP_REVIEW_FOCUS=comparison node frontend/tests/review-browser.mjs
# Run separately, after the first fixture has stopped, to verify existing Intake behavior:
node frontend/tests/intake-browser.mjs
```

The host CDP tooling may use the installed Node runtime; the application build/server and transport test evidence above used the declared Node 24. The earlier acceptance attempt passed AP flows but failed profile cleanup (`ENOTEMPTY`); after a focused RED/GREEN cleanup fix, the final AP+Intake command exited 0. This is not independent security certification. Documents are verified via private links/bytes, not a saved download artifact.

## Manager administration and invoice audit (PRD04)

AP Managers can open `/manager/parameters`, `/manager/escalations` and `/audit`. Intake and Analyst accounts cannot access the new administration/audit APIs; their existing authorized Intake/AP views are unchanged. Guards and navigation are presentation only: every backend request resolves the persisted session role independently.

### Parameters and escalation

Run `alembic upgrade head`: additive migration `0006` records parameter changes without rewriting invoices, evaluations or approval facts. Price tolerance starts at **2%** and the Analyst limit at **USD 5,000**. Both accept finite, nonnegative exact decimals without an application business maximum, fixed scale or rounding; database numeric capacity and transport limits still apply. The form displays tolerance as a percentage; the API uses its fractional string (`"0.02"`). Comments on parameter changes are optional.

Review the future-only explanation and confirm before saving. A change affects new evaluations and explicit reevaluations only; earlier results and evaluated approval-limit snapshots remain historical evidence. The history shows before/after values, version, recorded actor/time and provenance. An unchanged submission still checks the expected version, but retains that version and creates no audit event or comment. Conflicting or uncertain saves preserve the draft and pause editing until an explicit refresh; they are never blindly replayed.

The escalation queue includes **all automatic outcomes**, not only Held for Review. Select a case to inspect its recorded Analyst comments and evidence, then open the existing AP resolution screen. Approval/rejection still require a comment, confirmation, CSRF and current case revision. No client-side eligibility arithmetic or new payment mechanism is introduced.

| Endpoint | Contract |
| --- | --- |
| `GET /manager/parameters` | Current exact values, version, effective time and recorded actor; quoted version ETag. |
| `PATCH /manager/parameters` | At least one of `price_tolerance` / `analyst_approval_limit` as a decimal string, optional `comment`, CSRF and quoted version `If-Match`. Missing version: 428; stale version: 409 without a write. |
| `GET /manager/parameters/history` | `offset` / `limit` pagination and truthful before/after provenance. |
| `GET /audit/{invoice_id}` | Manager-only read projection; `offset`, `limit` (1–100), `kind`, `actor_id`, inclusive timezone-aware `time_from` / `time_to`. |

### Truthful audit and reproduction

The audit timeline separates recorded facts from derived differences/state transitions, current applicability from historical events, and automatic evidence from human decisions. Missing historical times/identities remain **not recorded**; upload time/owner must not impersonate extraction time/actor. A missing prior value is distinguished from a recorded null. Filters use recorded metadata; an event without a recorded time cannot match a time range. Protected originals remain behind the existing authenticated AP view, never permanent public URLs.

Use the same isolated PostgreSQL/loopback frontend setup described under [AP acceptance](#reproduce-ap-acceptance), with a current Node 24 production build. Run the scripts separately because each owns backend port 3211 and its disposable database:

```sh
node --test frontend/tests/manager-transport.test.mjs frontend/tests/manager-state.test.mjs frontend/tests/audit-state.test.mjs frontend/tests/decision-confirmation.test.mjs
node frontend/tests/manager-browser.mjs
```

Observed PRD04 integration proof: main composition tests passed; Manager/Audit transport, exact-value helpers and TypeScript passed; a fresh Node 24 build and actual Chrome acceptance passed against controlled extraction and isolated PostgreSQL. Chrome checked negative roles, exact parameter persistence/no-op, invalid/stale writes leaving version/audit unchanged, retained drafts, escalation/approval above the evaluated limit, audit state differences/provenance/filters, and cancelled/confirmed rejection. The first browser script failed on expression escaping; a later run found the missing rejection confirmation and passed after its correction. Docker context/name-reuse setup failures are not functional test passes. PRD05 full regression, broader accessibility/demo proof and the final independent verifier remain pending; native assessment is unassessable for the current untracked scope, not approval. No live Azure or banking outcome is claimed.

## Integrated frontend quality (PRD05 — verification in progress)

The central HTTP client validates nested Session, Intake and AP success payloads before components use them, including XHR upload responses. Invalid wire shapes fail with a sanitized `invalid_response`, correlation identifier and retry guidance. Legitimate unknown fields, pending technical states, historical versions and exact Decimal strings remain distinct; validation does not reproduce matching, approval policy or currency conversion. Unexpected JSON error `detail` strings are not echoed; recognized public session failures and the explicit public error envelope retain their contract.

All workspaces share accessible table regions, filters, timeline, evidence/status displays, controlled comment feedback, native confirmation and authenticated original-document access. Required decision comments receive inline errors and keyboard focus before any mutation. Existing CSRF/version/idempotency controls, draft retention and bounded polling remain authoritative. The original viewer only accepts an invoice UUID and derives a same-origin authenticated stream; it does not accept arbitrary URLs or HTML. Sandboxed preview support varies by browser; protected open/download links remain available.

Money display localizes separators without converting amounts to JavaScript numbers, rounding fractional digits, inventing USD or performing FX. Exact originals remain available alongside the localized presentation. Scientific notation expands only within a **1,000-digit rendering budget**; larger values retain their complete original notation and recorded currency. This is a presentation budget, not a business limit. Timestamps show a local timezone only after hydration, retaining their recorded instant; missing/invalid metadata never falls back to the current time. Date-only invoice fields remain calendar dates.

```sh
node --test frontend/tests/*.test.mjs
pnpm --filter frontend typecheck
pnpm --filter frontend build
```

Observed self-checks: 104 native frontend tests and TypeScript passed; the corrected Node 24 production build passed. Actual Chrome then passed Manager/Audit, AP and Intake together against controlled extraction and isolated PostgreSQL. This includes inline comment focus/no-write validation, the preserved private original viewer, correction/release/reevaluation, credit-note blocks, stale conflicts, mobile/keyboard checks and real session expiry. Earlier runs exposed a missing inline comment attachment and a changed iframe title; both were corrected and rechecked. The HTTP network/error matrix remains explicitly CDP-simulated. A separate controlled provider rate-limit scenario now passed through the real durable backend and UI: one attempt, `RETRY_WAIT`, a 120-second retry floor, no extraction/evaluation/final outcome and unchanged scheduling evidence after confirming an existing retry. Manager/Audit mobile bounds, labels, visible keyboard focus and sampled heading contrast also passed. These are self-checks, not a formal accessibility audit. Full backend regression passed (636 tests), as did the Node 24 native frontend suite (103 tests, with the host-Chrome cleanup test verified separately) and the baseline differential checks. The independent verifier found no application/security/data blocker and passed its functional checks, but reported a browser-fixture environment-isolation exception. With explicit owner authorization, the runner was corrected to forward only the isolation `HOME`/`TMPDIR` alongside its existing allowlist. A targeted process/Python check observed RED before the fix and GREEN afterward, including private-file allocation and cleanup inside the requested temporary root and exclusion of application credentials. The full verifier was not rerun; its original exception remains part of the evidence. No native approval, live Azure proof or permission to deploy is implied.

## Reproducible controlled demo and release checklist

### Isolated setup — no Azure or application-data reset

Use Node 24, installed workspace/backend dependencies and the existing PostgreSQL 17 image. Reserve loopback ports **55472** (test PostgreSQL), **3211** (fixture API) and **3212** (frontend). Reuse an existing owned test service if present; never stop an unrelated service or point this fixture at application data. If a disposable database service is needed:

```sh
# Synthetic credentials for this throwaway local fixture only; never reuse them.
docker run --detach --rm --name invoice-demo-postgres \
  -p 127.0.0.1:55472:5432 -e POSTGRES_USER=test \
  -e POSTGRES_PASSWORD=test_only -e POSTGRES_DB=test_ap_review postgres:17-bookworm
docker exec invoice-demo-postgres pg_isready -U test -d test_ap_review
```

Wait for database readiness before starting the fixture. In terminal A:

```sh
export TEST_DATABASE_URL='postgresql+psycopg://test:test_only@127.0.0.1:55472/test_ap_review'
AP_REVIEW_FIXTURE=1 PYTHONDONTWRITEBYTECODE=1 \
  backend/.venv/bin/python frontend/tests/intake-backend.py
```

The fixture refuses non-loopback/non-test URLs, creates its own uniquely named database, migrates it, imports synthetic supplier/PO/GRN data and stores originals in its own private temporary directory. Wait for both `INTAKE_FIXTURE` and the listening message. The first message identifies a generated valid PNG for uploads; it contains no real invoice. In terminal B, use the declared Node version to build/start the frontend:

```sh
pnpm --filter frontend build
BACKEND_INTERNAL_URL=http://127.0.0.1:3211 \
FRONTEND_ORIGIN=http://127.0.0.1:3212 \
  pnpm --filter frontend exec next start --hostname 127.0.0.1 --port 3212
```

Alternatively, build `frontend/Dockerfile` with the **repository root** as Docker context, then use the already-tested Linux host-network configuration above. Do not place either proxy variable behind a `NEXT_PUBLIC_` prefix. Open `http://127.0.0.1:3212/login`.

| Synthetic account | Capability |
| --- | --- |
| `browser_owner` | Intake uploads and owned-case tracking. |
| `browser_other` | Separate Intake owner for negative visibility checks. |
| `browser_analyst` | AP review, correction, reevaluation, allowed decisions and escalation. |
| `browser_manager` | AP capabilities, above-limit decisions, parameters and complete audit. |

The deliberately public fixture password is `browser test password`; these accounts exist only in the disposable fixture. This is not production-user provisioning.

### Demo path and safe reset

This extractor is intentionally **filename-driven**, not an Azure/model accuracy demonstration. Upload the generated PNG, keeping or changing only its upload filename:

1. **Held case:** a normal filename produces quantity 2 at net price 12 USD against a PO price of 10. Inspect the deterministic reason and original evidence.
2. **Correction:** as Analyst, change unit price to 10, line total/subtotal/total to 20, enter a reason, preview and confirm. Reevaluate to inspect Auto-Pay; authorization remains separate from a bank payment.
3. **Escalation:** `manager.png` produces USD 5,001. Request Manager review with a comment, inspect the recorded Analyst reason as Manager, then confirm the decision. Parameter edits demonstrate future-only versions, exact values, optional comments and stale recovery.
4. **Credit note:** use another undecided held case, request a credit note with confirmation/comment, reevaluate and verify the payment block persists.
5. **Rate limit:** `rate-limited.png` produces a **simulated provider 429** via the existing extractor error contract. The real worker records `RETRY_WAIT` with at least 120 seconds before eligibility, no final business result and no reset from **Confirm scheduled retry**. Later retries/exhaustion follow the existing policy; the fixture is not pinned forever to one status.
6. **Audit and boundaries:** inspect recorded/derived provenance and before/after values; check Intake/Analyst denial of administration, separate ownership, keyboard/mobile use and session-expiry return paths.

Stop the fixture with **Ctrl-C** to cancel its worker and remove only its uniquely owned database/private temporary files. Restarting creates fresh demo data; log in again. Stop only the frontend/database containers you created for this demo. No global reset/drop command is provided. An ungraceful SIGKILL can leave an orphan: inspect the exact recorded fixture resource and obtain operator authorization before manual removal; never delete databases by a broad name pattern.

For automated acceptance, stop the interactive fixture first: each script owns API port 3211 and creates/drops its own database. Keep the frontend running and the explicit test URL exported, then run separately:

```sh
PYTHONPATH=backend backend/.venv/bin/python -m pytest -q frontend/tests/test_controlled_provider.py
node frontend/tests/manager-browser.mjs
node frontend/tests/review-browser.mjs
node frontend/tests/intake-browser.mjs
```

### Pre-demo and release gates

- **Identity and health:** verify the authenticated frontend URL, safe return paths and API `/health`; liveness alone is not database/provider readiness. Exercise an authorized list and protected original.
- **Data and schema:** confirm reference-data import and migrations through `0006`; verify Manager parameters and evaluated snapshots. Keep application data separate from the disposable fixture.
- **Secrets and logs:** server-only Azure credentials/backend origin, no session tokens/document contents in screenshots or logs; inspect only sanitized errors and correlation identifiers. The controlled fixture needs no Azure credentials.
- **TLS/session policy:** before any real deployment, review HTTPS, persisted role provisioning, CSRF/origin settings, authorized document storage and `SESSION_SECURE_COOKIE=1`. `FRONTEND_ORIGIN` must be the trusted public frontend origin; forwarded host headers are not authority.
- **Provider fallback:** keep this controlled demo available when Azure throttles/unavailable, clearly label it synthetic and retain the separate live-Azure gap. Do not present its results as model/provider proof.
- **Release:** run relevant tests/build/browser and independent verification, review the atomic local commits and secure-environment configuration. Tagging, pushing, deploying or publishing require separate owner authorization; no environment is certified here.
- **Rollback:** prefer the previous application artifact, preserving immutable audit/approval facts. Configuration reversion is another future-only audited Manager version. Do not delete history or downgrade a database to undo a UI change; restoration/migration actions need an explicit operator plan and backups.

Observed demo checks: five controlled-extractor contract tests and actual expanded Manager/Audit Chrome acceptance passed, including the real backend retry evidence and basic mobile/accessibility checks. Full backend regression (636 tests), the Node 24 native suite (103 tests plus separately verified host-Chrome cleanup) and baseline differential checks passed. Independent functional verification passed. Its recorded browser-fixture isolation exception was followed by an owner-authorized narrow correction and targeted RED/GREEN process/Python proof, not a repetition of the full verifier. To check that boundary alone, run `CHECK_MANAGER_PYTHON_ISOLATION=1 node --test frontend/tests/manager-runner-isolation.test.mjs`; it starts no API, database, Chrome or provider. No native approval or live Azure success is claimed.

## Decision log

Decisions D1–D15 record earlier implementation milestones. References to deferred work describe those checkpoints; the current AP capabilities and limitations are stated above and below.

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

### D16 — USD approval limit without conversion

**Owner decision:** Analyst may approve at most 5,000 USD inclusive. Higher USD totals and every other currency require Manager, without automatic FX. The numeric limit comes from the evaluation's configuration snapshot; currency eligibility is enforced server-side, not through browser arithmetic.

### D17 — Audited release when an approved case is corrected

**Owner decision:** A relevant correction revokes the prior payment authorization and releases consumed quantities with a traceable reversal. Previous decisions and evaluations are not deleted. Payment remains blocked until reevaluation and a new approval. No banking payment has been executed by this application. Pending credit-note requests remain explicit payment blocks; their external resolution is not implemented.

### D18 — Exact, future-only Manager parameters

**Owner decision:** Preserve finite nonnegative tolerance and USD Analyst limit, without added maxima or fixed decimal scale. Versioned Manager edits are audited atomically and select future evaluations/reevaluations only. Historical evaluation and approval-limit snapshots are not rewritten. Unchanged values still perform the version check without creating a new version/audit comment.

### D19 — Honest historical audit provenance

**Design:** Project existing recorded evidence read-only. Keep absent historical actor/time explicit; label derived differences and state transitions instead of manufacturing a past author or extraction timestamp. Full audit administration is Manager-only while existing Analyst case history remains authorized.

### D20 — Explicit numeric-separator extraction guidance

**Owner request:** Clarify the model instruction after an original `1,831.94` total was persisted as `1.83194`. Request dot-decimal strings without grouping separators, include both separator conventions and unchanged sub-thousand examples, preserve magnitude and precision, and retain null/warning for ambiguity. Do not calculate missing totals or infer net price basis. This prompt-only clarification applies to future extractions after the updated backend is running; historical extraction/evaluation evidence remains unchanged. MockTransport checks verify the instruction sent and exact returned values, not live model compliance.

### D21 — Conservative invoice-line identity resolution

**Owner request:** Correct the explicit-only association gap. A pure Domain resolver accepts complete structured references, unambiguous raw line numbers or exact `PO-number/line-number` references, and otherwise a unique normalized exact description within the referenced POs. Invalid, partial, contradictory or ambiguous references are not rescued by descriptions. Shared targets retain the existing grouped-quantity checks. Processing, reevaluation and the fresh approval guard reuse this resolver; the low-level structured evaluation API still consumes caller-supplied associations.

**Evidence compatibility:** New evaluation inputs optionally record `association_methods`; comparative rows expose the recorded `association_method` (`explicit` or `description`). The legacy `association_status: explicit` flag still means a verified target in the supplied snapshot, not necessarily an ID extracted by the model. This preserves existing client validation and approval presentation. Old snapshots get no invented method, and no extraction/evaluation/history migration occurs. Unknown net basis without sufficient accounting evidence (D22), or other missing evidence, still prevents a final Auto-Pay result. Deploying the updated backend and explicit human reevaluation are separate from these isolated source tests; existing acceptance records remain unchanged.

### D22 — Deterministic effective net basis from accounting evidence

**Owner-approved policy:** A pure Domain resolver may establish an **effective** net basis for unknown USD lines when quantities, unit prices and reported line totals reconcile to the before-tax subtotal, separately stated positive tax reconciles subtotal to total, and all discounts/charges/rounding have known scope. It uses `Decimal`, per-line `ROUND_HALF_UP` and absolute **USD 0.01 per reconciliation**, independently of the unchanged **2% commercial price tolerance**. No extracted amount or declared basis is rewritten. Explicit gross is never overridden; absent, contradictory, unsupported or gross/net-ambiguous evidence stays unknown. Zero tax alone does not establish a tax-exclusive basis.

The existing extraction request now asks for visible accounting facts, not model inference or another model call. The optional `accounting_evidence` block has `taxes_separately_stated` (boolean or null) and `adjustments` (array or null). A missing block/null means insufficient evidence; `adjustments: []` explicitly means known none. Each adjustment has `kind` (`discount`, `charge`, `rounding`, `unknown`), `stage` (`before_subtotal`, `after_subtotal`, `unknown`), original exact decimal-string `amount` (or null), and zero-based `line_index` (or null). Before-subtotal amounts are already represented in the displayed subtotal; after-subtotal amounts apply between subtotal and total. Only explicitly scoped before-subtotal adjustments may reference a line. Discount effects subtract the absolute amount without changing the stored sign; unknown scope, missing amounts or contradictory negative charges prevent inference. Accounting inference is bounded to avoid excessive decimal work; extreme inputs remain unchanged/unknown.

For INV-01, `40 × 24.50 = 980.00`, `6 × 89.00 = 534.00`, subtotal `1514.00`, tax `317.94`, total `1831.94` establish effective net only with complete visible tax/adjustment evidence. Normal matching then runs; Auto-Pay still requires every other rule to pass. Historical payloads without the new evidence do not gain it by deployment or reevaluation.

Initial processing, AP reevaluation and the fresh approval guard share one resolver. New immutable evaluation inputs record optional `price_basis` evidence: original declared bases and accounting facts, effective per-line basis/source (`declared`, `arithmetic`, `undetermined`), reconciliation reason/expected totals, rounding and tolerance. AP comparison rows expose `declared_unit_price_basis`, `effective_unit_price_basis`, `unit_price_basis_source` and `price_basis_reason`; old snapshots omit them without invented provenance. The low-level structured evaluation interface still consumes caller-supplied verified net prices. AP correction JSON accepts the optional block; the form preserves it and invalidates it when line deletion/addition changes its indexed scope. Original versions, no-op detection, receipt grouping/active consumption/reversals and automatic-versus-human decision separation remain unchanged. This P1 does not implement the separately discussed automatic `Auto-Pay` → operational `APPROVED` transition.

### D23 — Automatic operational approval with preserved human rejection

**Owner-requested behavior:** Complete `Auto-Pay` from initial document processing or explicit AP reevaluation creates one current `approve` decision, operational `APPROVED`, payment unblocked and grouped receipt consumption atomically. `system:auto-pay` / `Automatic matching` is a reserved workflow identity, not a login account or new authentication role. The evaluation retains its initiating actor separately. AP history records the automatic decision, original evaluation/version and fresh quantity proof; full Audit identifies the approver as system, not human. No bank payment is executed. Existing Analyst/Manager limits continue to govern human overrides, not the requested automatic Auto-Pay transition.

**Owner-selected rejection policy:** Reevaluating unchanged invoice facts cannot override a current human rejection, including when reference/configuration changes yield Auto-Pay. A relevant correction invalidates the old decision's applicability without deleting it; the next complete Auto-Pay may then approve automatically. Pending credit-note requests remain blocking across corrections and reevaluation. Incomplete, Held for Review and Rejected outcomes do not automatically approve.

**Safety and trade-off:** Automatic and manual approval share the same fresh positive-line, verified-net-basis, identity and available-receipt guard. Initial completion now joins AP's READ COMMITTED / advisory-lock / reference-SHARE serialization before reading history or balances. Evaluation, original processing links, case revision, decision, quantity ledger and audit commit or roll back together. Reevaluation reverses a prior authorization before evaluating and replaces it only when eligible; historical decisions/reversals remain, with no duplicate active consumption. A repeated manual approval of an already decided version returns 409 without changes. One operation advances the case revision once (initial automatic approval is revision 1). The standalone structured evaluator remains evaluation-only. Global serialization is deliberately conservative for this local monolith; no migration, dependency, payment integration or retrospective historical approval was added.

**Verification checkpoint:** Full backend regression passed 753 tests before retaining three additional independent public-HTTP probes. Independent verification then passed 31 focused regressions and its own three probes; the parent retained those probes in `backend/tests/test_verifier_auto_approval.py` and separately passed all three. This is not a claimed full 756-test run. Focused frontend (64 tests), TypeScript, production build and 11 baseline HTTP differential vectors passed. The full frontend suite did not finish, and Chrome acceptance was unavailable because the isolated fixture port was occupied; those disclosed limits remain owner-accepted. Native assessment remained unknown/unassessable, not native approval. No live Azure or bank payment was used for these checks.

### D24 — Private native document preview

AP Review embeds the authenticated same-origin original in the browser's native viewer. An empty iframe sandbox disables native PDF rendering in some browsers, so the preview does not apply that sandbox. Instead, the document proxy permits successful original responses only with `application/pdf`, `image/png` or `image/jpeg` (media-type parameters and case are normalized). Missing or unexpected MIME, including HTML and SVG, returns HTTP 502 with `unsupported_document_type` / `Original document could not be displayed safely`, without exposing upstream bytes. Upload content validation and backend authorization remain unchanged; this response check does not replace them.

UUID-only private document URLs, session-cookie authorization, `private, no-store`, `nosniff`, upstream CSP/frame headers and authenticated open/download links remain intact. No public or third-party document viewer is used. Intake's links-only presentation is unchanged. Run focused coverage with `node --test frontend/tests/document-preview.test.mjs frontend/tests/backend-proxy.test.mjs`. These tests render the real shared React component and exercise the proxy, but do not prove browser PDF rendering. The automated Chrome attempt produced no result and was stopped; the owner explicitly chose final visual validation in the local AP Review page. No successful automated PDF-rendering claim is made.

## Current limitations

This is a local demo, not a production deployment. Private Intake and AP correction/decision/audit workflows have controlled-provider browser proof, with independent backend execution and targeted frontend contract inspection, not independent frontend execution: that optional attempt was unavailable because of scratch dependency setup. The comparison-row blocker was corrected and rechecked; parent Node24 build/browser evidence supplies the functional UI proof. The earlier independent PRD02 security run produced no verdict and is not replaced by these functional checks. The latest real Azure workflow remained in RETRY_WAIT with provider_unavailable; live Azure end-to-end proof remains pending. No bank transfer, external credit-note lifecycle or supplier/PO/GRN editor is implemented. Approval transactions deliberately serialize for this small monolith; append-only application history is not protection from a database administrator. Container images favor reproducibility over optimized size. Negative/credit arithmetic and undefined evidence are not silently converted into payment authorization.
