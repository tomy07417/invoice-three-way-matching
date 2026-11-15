# Architecture — Initial agreement

## Goal
A small, understandable monorepo that can be run locally with Docker Compose and later deployed as an authenticated service.

## Logical topology
`Next.js frontend` → HTTP/REST → `FastAPI backend` → `PostgreSQL`

The FastAPI backend also calls `Azure OpenAI` securely to extract invoice information. The model is not responsible for payment decisions.

## Backend architecture — Ports & Adapters
The backend uses a hexagonal architecture within a single monolith. This changes only its internal organization; the stack remains Next.js, FastAPI, PostgreSQL, Azure OpenAI, and Docker Compose.

| Component | Responsibility |
|-----------|----------------|
| Domain | Entities, domain services, and pure business rules, including the deterministic Three-Way Matching engine. |
| Application | Use cases, DTOs, and ports (interfaces) defining contracts for external services. Orchestrates domain behavior without depending on concrete adapters. |
| Infrastructure | Adapters implementing application ports for PostgreSQL persistence, Azure OpenAI extraction, and document storage. |
| Interfaces | FastAPI REST endpoints and input/output schemas. Translates HTTP requests into use-case inputs and maps results into responses. |

### Dependency rules
- Domain is independent of FastAPI, PostgreSQL, Azure OpenAI, and the other backend components.
- Application depends on Domain. Infrastructure and Interfaces depend inward on Application and Domain, never the reverse.
- External integrations implement the contracts defined by Application ports. Use cases depend on those ports, not their concrete adapters.
- `main.py` is the composition root: it wires adapters into use cases and connects them to FastAPI endpoints; it contains no business rules.
- The deterministic matching and rules engine receives the data it needs as inputs and can run and be tested without a web server, database, model, or any external connection.
- Keep one backend monolith. Add ports and adapters only for actual integration boundaries; avoid microservices and unnecessary abstractions.

## Proposed repository layout
```text
invoice-three-way-matching/
├── AGENTS.md
├── README.md
├── compose.yaml
├── .env.example
├── .gitignore
├── frontend/
│   ├── app/                  # pages/routes
│   ├── components/           # reusable UI
│   ├── lib/                  # typed API client and helpers
│   ├── public/
│   └── Dockerfile
├── backend/
│   ├── app/
│   │   ├── main.py           # composition root and FastAPI startup
│   │   ├── domain/
│   │   │   ├── entities/     # business entities, independent of persistence
│   │   │   └── matching/     # pure matching business capability
│   │   │       ├── engine.py # evaluation and precedence
│   │   │       ├── rules.py  # independent deterministic checks
│   │   │       ├── types.py  # result, configuration, and input contracts
│   │   │       └── duplicates.py # explicit normalization and history checks
│   │   ├── application/
│   │   │   ├── use_cases/    # extraction, matching, review, audit, and imports
│   │   │   ├── dtos/         # use-case inputs and outputs
│   │   │   └── ports/        # contracts for external services
│   │   ├── infrastructure/
│   │   │   ├── persistence/  # PostgreSQL adapters and SQLAlchemy models
│   │   │   ├── extraction/   # Azure OpenAI adapter and document preparation
│   │   │   └── storage/      # document storage adapters
│   │   └── interfaces/
│   │       ├── api/          # FastAPI REST endpoints and input/output schemas
│   │       └── cli/          # reference-data import command
│   ├── migrations/           # explicit Alembic schema revisions
│   ├── alembic.ini
│   ├── tests/
│   └── Dockerfile
├── docs/
│   ├── PRD.md
│   └── ARCHITECTURE.md
└── data/                     # local samples, if permitted; never secrets
```
The four backend components and dependency direction are agreed; the detailed directory layout is illustrative and can grow incrementally. Confirm major changes with the project owner. Persistence uses approved SQLAlchemy 2 models, Alembic migrations, and synchronous Psycopg 3; uv manages Python dependencies. SQL models remain separate from domain entities. Authentication, validation, and HTTP client library choices remain open.

## Responsibility boundaries
- Frontend: invoice upload, status, review queue, corrections, configuration UI, and audit views according to role.
- Interfaces: HTTP validation, authentication/authorization at the API boundary, use-case invocation, and response/error mapping. The import CLI also invokes application use cases.
- Application: orchestrates extraction, reference-data retrieval, matching, review, and audit through domain behavior and ports; enforces use-case authorization independently of the entry point.
- Domain: supplier/PO/GRN line associations, deterministic rule precedence, configurable tolerances, outcome reasons, and human-review policies. No HTTP or persistence concerns.
- Infrastructure: retrieves and persists reference data, invoices, decisions, and append-only audit events in PostgreSQL; extracts structured invoice data through Azure OpenAI; stores documents through the chosen storage adapter. Extraction uncertainty and errors are surfaced to Application.

## First implementation milestone
1. Scaffold repository and Docker Compose for frontend, backend, database.
2. Confirm database and API startup, plus a health endpoint.
3. Use approved SQLAlchemy 2, Alembic, and synchronous Psycopg 3 with uv.
4. Define supplier, PO, PO line, GRN, GRN line, and raw invoice tables (implemented). Run migrations explicitly; API startup never migrates or connects to PostgreSQL.
5. Build repeatable import command for the three provided JSON files.
6. Add read-only API to inspect a PO with lines and aggregated GRN quantities.
7. Write tests for import and GRN aggregation.

**Acceptance:** a reviewer can start the local stack, import reference data using a documented command, and query a PO. No LLM integration is required in this first milestone.

## Implemented domain matching slice

`domain/matching/types.py` contains immutable result/configuration and invoice-history contracts; `rules.py` contains pure checks; `duplicates.py` implements explicit invoice-number normalization and history comparisons; `engine.py` centrally applies `Rejected > Held for Review > Auto-Pay`. Existing frozen Invoice/PO/GRN records are reused unchanged. The engine imports only domain modules and the standard library and has no API/application/persistence integration. Grouping it by business capability improves cohesion without adding a layer or changing behavior. Application use cases will orchestrate retrieval through ports, engine invocation, and persistence; that orchestration is not part of the engine, and no `evaluate_invoice.py` use case is implemented yet.

Evaluation returns implemented-check `provisional_status` separately from `complete`, `pending` and `final_status`. Pending policies never suppress available checks and never count as passed: any pending result makes the final status `None`. Caller-supplied Supplier, PO-local whole-line associations, other-case InvoiceHistoryEntry records, prior approved quantities and net unit prices establish comparison evidence. Explicit number normalization and confirmed duplicate/similarity criteria are implemented. Invoice quantities sharing one target are grouped and compared against received minus approved quantities; missing snapshots remain incomplete. Unreliable associations, inactive suppliers, closed POs and currency mismatch hold rather than inventing conversions or arbitrary allocations. Supplier identity resolution, fuzzy matching and tax computation are not implemented. See the [README in-memory example](../README.md#in-memory-matching-no-database-or-model) for the public contract and limitations.

Configuration is an immutable evaluation input (Decimal fractional tolerance 0.02; analyst limit 5000 carried for future review). New snapshots affect only evaluations receiving them; existing immutable results do not change. Parameter management/audit, human decisions and reevaluation after corrections or later GRNs remain future work. This slice implements the confirmed pure policies from the supplied challenge and official client answers, not persisted payment/review/audit workflows.

## Confirmed-policy implementation boundary

The nine official client clarifications are recorded in the PRD. Domain evaluation consumes resolved supplier and PO snapshots, actually received GRNs, quantities already approved for payment, comparable net unit prices, and invoice-history evidence. These inputs are not queries: Application must obtain them through ports in a later integration stage. An authoritative empty history/approved-quantity snapshot differs from missing evidence.

Automatic evaluation, operational invoice state, human decisions and audit are separate concerns. Corrections create new evaluated versions and invalidate prior human decisions without deleting them; pending credit-note requests block payment without rewriting an automatic result. Those workflows require persistence, authorization and audit and are not part of this pure-rule stage. Approval transactions must revalidate availability atomically to prevent concurrent overapproval; the engine does not implement locks or ledger writes.

New GRNs are included whenever explicitly reevaluating with a new received snapshot, even when later-dated than the invoice. The accepted design uses manual audited GRN reevaluation and future-only parameter snapshots. See README decisions D7-D13 for the distinction between official requirements and design choices.

## Decisions to confirm later
- Authentication implementation and session approach
- Document file storage strategy
- Extraction SDK/HTTP client, retries and validation approach
- Cloud deployment design
- Remaining business boundaries: nonpositive PO-price percentage basis, negative/credit-line arithmetic, and approval-limit currency/escalation details; the former nine questions now have official answers.
