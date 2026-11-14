# Reference data and domain
Objective: implement the four owner-requested reference-data steps. Branch: `feat/reference-data-domain`. Delivery: four task-specific commits authorized by owner; no merge/push authorized. Forecast ~1,200 authored lines including functional tests; generated uv.lock excluded.
Checks: Python 3.12 pytest domain tests; isolated PostgreSQL migration/import/query integration tests; existing health regressions and public differential probes. Risk: item 1 (migrations/stored data), item 5 (dependencies/environment).

## Specs
S1. Owner request (verbatim):
> 1. Definir las entidades del dominio
>
> Crear las entidades Supplier, PurchaseOrder, PurchaseOrderLine, GoodsReceivedNote, Invoice e InvoiceLine dentro de domain/.
>
> Por ahora, sin conectarlas a PostgreSQL.

S2. Owner request (verbatim):
> 2. Implementar persistencia
>
> Definir las tablas y relaciones en PostgreSQL, junto con las migraciones.
>
> Acá necesitamos decidir si usamos SQLAlchemy + Alembic o SQL directamente.

S3. Owner request (verbatim):
> 3. Importar los JSON del desafío
>
> Implementar un comando para cargar:
> - suppliers.json
> - purchase_orders.json
> - goods_received_notes.json
> La importación debería validar referencias y evitar duplicaciones al ejecutarse varias veces.

S4. Owner request (verbatim):
> 4. Implementar consultas y tests
>
> Poder obtener una orden de compra con sus líneas y calcular cuánto se recibió realmente por cada línea, sumando los distintos GRN.
>
> Esto será fundamental para validar las cantidades facturadas.

S5. Owner-selected persistence option (verbatim):
> SQLAlchemy + Alembic + Psycopg (recomendado)
> ORM y migraciones versionadas, con acceso síncrono. Modelos SQL separados de las entidades del dominio.

S6. Owner data location (verbatim):
> En la carpeta de /data/reference-data

Assumptions and bounded implementation decisions: actual files are data/reference-data/reference-data/*.json; preserve location and ignore Zone.Identifier. Domain uses standard-library dataclasses, Decimal and date; no framework/ORM imports. Numeric quantities/prices preserve exact decimal values without arbitrary scale truncation. Add a GRN line value to represent actual nested data. Invoice fields follow docs/PRD.md; raw optional PO-line references and nonexistent invoice PO references remain representable, with no invoice-number uniqueness/payment policy invented. Stable supplier/PO/GRN identities plus PO-local line numbers; item codes not globally unique. Unchanged reimports are no-ops; changed content with existing identities is a conflict, not an overwrite. Validate full batch and commit atomically; no rejected batch may alter rows or import counters. Aggregation sums GRN quantities only, retains unreceived PO lines with zero, and does not subtract invoices. Public query: GET /purchase-orders/{po_number}, unknown PO 404; Decimal values serialized as strings. Import CLI: python -m app.interfaces.cli.import_reference_data --directory <path>. Connection uses server-only existing POSTGRES_* environment variables or DATABASE_URL, without reading/logging secret files. No live-volume migrations/imports during checks; use fresh temporary PostgreSQL container/database. No auto-migration at API startup. Existing /health and unknown-route behavior must stay unchanged.

## Tasks
- T1 | S1,S6 | delegated | done | Six entities plus nested GRN line, standard-library-only. RED missing entity; GREEN 7 domain tests, full suite 9 passed. Commit: efa7bc6.
- T2 | S2,S5 | delegated | done | Approved deps installed; schema/migrations/config/container docs written. Focused PostgreSQL suite6 passed in disposable container; existing health preserved. Commit: af2e329.
- T3 | S3,S5,S6 | delegated | done | CLI/reader implemented; 43 reader/import tests passed in60.42s against isolated PostgreSQL, covering no-op reruns, references, conflicts and rollback. Commit: ad49575.
- T4 | S4 | delegated + parent verify | done | API and GRN sums implemented; full suite72 passed, independent verifier PASS S1-S6, baseline differential and container packaging passed. Test service removed; live volume untouched. Native review not approved (assessment unavailable). Commit: this T4 query work-unit commit.

## Log
L1. Original owner request (verbatim):
Próximo paso: datos de referencia y dominio
Lo dividiría en cuatro tareas pequeñas, en este orden:

1. Definir las entidades del dominio

Crear las entidades Supplier, PurchaseOrder, PurchaseOrderLine, GoodsReceivedNote, Invoice e InvoiceLine dentro de domain/.

Por ahora, sin conectarlas a PostgreSQL.

2. Implementar persistencia

Definir las tablas y relaciones en PostgreSQL, junto con las migraciones.

Acá necesitamos decidir si usamos SQLAlchemy + Alembic o SQL directamente.

3. Importar los JSON del desafío

Implementar un comando para cargar:
- suppliers.json
- purchase_orders.json
- goods_received_notes.json
La importación debería validar referencias y evitar duplicaciones al ejecutarse varias veces.

4. Implementar consultas y tests

Poder obtener una orden de compra con sus líneas y calcular cuánto se recibió realmente por cada línea, sumando los distintos GRN.

Esto será fundamental para validar las cantidades facturadas.

L2. Owner selected SQLAlchemy + Alembic + Psycopg, synchronous. Owner then supplied location (verbatim): En la carpeta de /data/reference-data

L3. Read-only exploration: files nested one extra directory, all root arrays. 6 suppliers, 10 POs/14 lines, 11 GRNs/15 lines. PO-2026-0102 line1 receives10+5=15, line2 receives30; PO0107 receives8/10. Parent spot-checked GRN fixture. No missing references observed. Invoice data only in PRD. Do not change/move source files, AGENTS.md, docs/PRD.md, environment files or Zone.Identifier. Main baseline 72702dc, existing healthy containers and volume retained. SQLAlchemy/Alembic/Psycopg dependencies explicitly approved. Source mutations will be delegated sequentially due context backstop, not file count. No existing behavior other than /health needs preservation; main composition root is shared and therefore health regressions/differential probes required.

L4. T1 completed by worker: 7 domain tests after missing-entity RED; full backend suite9 passed. Invoice optional raw reference/due date and nonexistent PO references preserved; no payment policy/runtime coercion. Native assess unavailable due undeclared untracked scope; returned self-verification only, independentVerifier=false for this pure-domain unit. T2 begins with approved dependency installation.

L5. uv installed SQLAlchemy2/Alembic1/Psycopg3, lock updated. T2 pure config/model checks4 passed; initial2 integration skips resolved by parent against fresh owned tmpfs container invoice-reference-test-01a1d6f0, port32773, test_reference DB. Focused PostgreSQL suite6 passed in3.46s. Fixtures create/drop only unique test_ databases; live Compose volume untouched. Native assess escalated item1; independentVerifier=true to be performed once at feature end. Native risk remains unavailable due undeclared untracked scope. Container retained temporarily for T3/T4 isolated checks.

L6. T3 importer/reader written with exact public command result checks, safe errors, full-content canonical comparison and transactional reference validation. Worker observed missing-command RED then repeat-import GREEN, reader20 tests passed. Parent full reader/import suite43 passed in60.42s against isolated PostgreSQL; no failures. Native assess escalated item1 for atomic stored-data import, independentVerifier=true at feature end; native untracked-scope assessment still unavailable. T4 begins; no live database writes or commits.

L7. T4 worker observed PO0102 query RED404 then GREEN200 with exact complete body and received15/30. Health/error suite9 passed. Parent full backend suite72 passed in84.34s with no skips. Differential baseline72702dc vs current /health and unknown-route identical status/contenttype/body with absent/malformed/unreachable DB config. Backend image rebuilt; container Alembic offline SQL and importer --help passed, Compose config valid. README D1 stale implementation statement corrected; D2-D4 describe persistence/import/query decisions. No frontend changes or repeat frontend build needed.

L8. Final independent verifier PASS S1-S6, no blockers:48 bounded checks passed in isolated scratch, including original10 PO totals computed independently, exact decimals, zero receipts, migration cycles/FKs, rejection preservation and counters, inward dependencies. Broader parent72-test suite retained as primary full proof. Extra scratch-only probes were not copied into the repository: requested rules are already pinned. Native assess escalated item1; independentVerifier=true satisfied. Native risk assessment unavailable because untracked scope undeclared; no reviewDue continuation, review authority or approval invented.

L9. Final parent spot check Compose config and git diff --check exit0. Stopped only owned test container after matching full recorded container ID; --rm removed its tmpfs state. Existing Compose services remain healthy, no live migrations/imports/volume changes. Feature source and docs complete on feat/reference-data-domain; no commits/push. New API was exercised in isolated test servers, not deployed into the existing running backend. Use documented rebuild, explicit migration and import steps to activate it in development.

L10. Owner delivery request (verbatim): Bien, fijate de ahcer los commits, no hagas uno solo hacelo por tarea
Plan: four Conventional Commits in task order with tests and relevant README snapshot in each. Source reference JSON fixtures accompany T1 because its domain tests read them; invoice samples, Zone.Identifier, AGENTS.md and docs/PRD.md remain excluded. Final feature document records T1-T3 hashes and self-references T4 to avoid an unrelated bookkeeping commit. No source behavior changed; retain observed72-test and independent PASS proof. No merge/push.

L11. T1 commit efa7bc6 includes pure entities/tests, source reference fixtures required by those tests, and task-specific README. Assessment of committed T1 unavailable due untracked scope, no reviewDue continuation. Four local task commits are the owner-selected delivery boundaries; no PR scope or publishing authorized. Initial cumulative line warning includes older local commits and source JSON fixtures; preserve task cohesion rather than split tests from behavior.

L12. T2 commit af2e329 includes dependencies/lockfile, ORM models, explicit migrations, test isolation fixtures, constraints tests, container configuration and matching README/architecture documentation. Committed-range assessment remains unavailable due untracked scope; prior independent item1 verification stands. The cohesive migration unit is larger than400 lines with tests, rather than split schema/model/tests into unusable intermediate commits.

L13. T3 commit ad49575 includes transactional import use case/port/DTO, exact JSON reader, SQL adapter, public CLI, reader/import tests and corresponding README usage/decision. No query endpoint or query documentation is present in this commit. Committed-range assessment unavailable due untracked scope; existing independent item1 verification stands.

L14. T4 closes the four owner-requested commits with PO query port/use case/DTO, SQL aggregation adapter, HTTP router/composition, API and health regression tests, final README and this feature record. Previous functional proof remains72 passed with independent PASS; only staging/documentation changed for delivery. T1-T3 hashes above are exact; T4 self-reference avoids an extra housekeeping commit. No merge/push; all unrelated input invoice files/metadata and original AGENTS.md/docs/PRD.md excluded.
