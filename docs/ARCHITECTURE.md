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
│   │   │   └── services/     # pure matching, rules, and domain policies
│   │   ├── application/
│   │   │   ├── use_cases/    # extraction, matching, review, audit, and imports
│   │   │   ├── dtos/         # use-case inputs and outputs
│   │   │   └── ports/        # contracts for external services
│   │   ├── infrastructure/
│   │   │   ├── persistence/  # PostgreSQL adapters, models, and migrations
│   │   │   ├── extraction/   # Azure OpenAI adapter and document preparation
│   │   │   └── storage/      # document storage adapters
│   │   └── interfaces/
│   │       ├── api/          # FastAPI REST endpoints and input/output schemas
│   │       └── cli/          # reference-data import command
│   ├── tests/
│   └── Dockerfile
├── docs/
│   ├── PRD.md
│   └── ARCHITECTURE.md
└── data/                     # local samples, if permitted; never secrets
```
The four backend components and dependency direction are agreed; the detailed directory layout is illustrative and can grow incrementally. Confirm major changes with the project owner. Specific persistence, authentication, validation, and HTTP client libraries are deliberately undecided.

## Responsibility boundaries
- Frontend: invoice upload, status, review queue, corrections, configuration UI, and audit views according to role.
- Interfaces: HTTP validation, authentication/authorization at the API boundary, use-case invocation, and response/error mapping. The import CLI also invokes application use cases.
- Application: orchestrates extraction, reference-data retrieval, matching, review, and audit through domain behavior and ports; enforces use-case authorization independently of the entry point.
- Domain: supplier/PO/GRN line associations, deterministic rule precedence, configurable tolerances, outcome reasons, and human-review policies. No HTTP or persistence concerns.
- Infrastructure: retrieves and persists reference data, invoices, decisions, and append-only audit events in PostgreSQL; extracts structured invoice data through Azure OpenAI; stores documents through the chosen storage adapter. Extraction uncertainty and errors are surfaced to Application.

## First implementation milestone
1. Scaffold repository and Docker Compose for frontend, backend, database.
2. Confirm database and API startup, plus a health endpoint.
3. Agree on persistence/ORM and migration library before adding dependencies.
4. Define supplier, PO, PO line, GRN, and GRN line tables.
5. Build repeatable import command for the three provided JSON files.
6. Add read-only API to inspect a PO with lines and aggregated GRN quantities.
7. Write tests for import and GRN aggregation.

**Acceptance:** a reviewer can start the local stack, import reference data using a documented command, and query a PO. No LLM integration is required in this first milestone.

## Decisions to confirm later
- ORM/migrations and Python dependency manager
- Authentication implementation and session approach
- Document file storage strategy
- Extraction SDK/HTTP client, retries and validation approach
- Cloud deployment design
- Business ambiguities: partial invoicing, duplicates, currency, post-decision correction, inactive suppliers, closed POs, multi-PO mapping, credit notes, later GRNs
