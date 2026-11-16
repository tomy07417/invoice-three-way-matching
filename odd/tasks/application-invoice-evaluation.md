# Application Invoice Evaluation
Objective: integrate the existing matcher with structured invoices and PostgreSQL through EvaluateInvoiceUseCase. Branch: feat/deterministic-matching-engine; baseline f562a53. Local commits authorized after verification; no push/dependencies or human-review/AI/frontend workflow.
Checks: contract/unit RED-GREEN, isolated PostgreSQL migration/repository/use-case tests, existing CLI/API preservation, full regression and one independent verifier. Risk: item1 (new migration and persisted audit data), item4 (consistent transactional snapshot). Forecast ~1600 authored lines, incremental units; local commit delivery authorized in L10.

## Specs
S1. "Implementar un EvaluateInvoiceUseCase que permita evaluar facturas estructuradas utilizando los repositorios PostgreSQL y el motor de dominio existente."
S2. "Reutilizar las entidades, repositorios, puertos y reglas ya implementados." "Recuperar proveedores, PO, líneas de PO y GRN asociados."
S3. "Obtener cantidades previamente aprobadas para pago, sin descontar facturas simplemente recibidas, rechazadas o retenidas." "Recuperar la configuración de reglas aplicable."
S4. "Resolver los datos necesarios para construir el contexto del motor." "Invocar el motor sin duplicar ninguna regla de negocio en la capa de aplicación."
S5. "Persistir los resultados de evaluación, discrepancias y datos relevantes para auditoría." "Mantener diferenciados el resultado automático, el estado operativo y las decisiones humanas."
S6. "Conservar explícitamente las evaluaciones incompletas; nunca convertirlas automáticamente en Auto-Pay."
S7. "Agregar tests unitarios del caso de uso y tests de integración con PostgreSQL."
S8. "domain/: reglas determinísticas y entidades de negocio." "application/: coordinación del caso de uso y definición de puertos." "infrastructure/: implementación de repositorios y persistencia." "No introducir dependencias de SQLAlchemy, FastAPI ni PostgreSQL dentro del dominio."
S9. "No implementar todavía: Extracción mediante Azure OpenAI. Procesamiento de PDF o imágenes. Frontend. Workflow de revisión humana. Nuevas dependencias sin autorización."
S10. "Mantener los cambios pequeños y verificables. No realizar commits ni push sin autorización."
S11. Owner answer "Dale" approves the presented plan and confirms an initially empty AUTHORITATIVE approved-consumption ledger (no historical approvals to import). Evaluating never creates approvals or payments.

## Tasks
- T1 | S1-S11 | inline | done | Define common immutable command/result/config DTOs and evaluation-specific transactional port with contract tests. Commit8f0b6f6.
- T2 | S1-S8,S11 | two parallel scoped writers | done | Independent application orchestration/unit tests and PostgreSQL schema/adapter/repository tests after common contract freezes. Commitsa2bf89e (application),025f48a (schema),7caf80b (adapter).
- T3 | S1-S11 | inline | done | Integrate application+PostgreSQL tests, update docs/example, preserve existing CLI/API/migration behavior and run full regressions. Commit23ca0f6.
- T4 | S1-S11 | independent verify then inline close | done | Assess item1/4 risk, one independent functional verification, resolve any bounded findings, final scope/evidence. Regression probes in23ca0f6; evidence in this document.
- T5 | S10 | inline | done | Package contracts, application, schema, adapter and integration/docs into coherent local commits8f0b6f6,a2bf89e,025f48a,7caf80b,23ca0f6; owner files excluded. Closing document commit recorded externally in delivery memory. No push.

## Log
L1. Original owner request (verbatim):
Implementar la siguiente etapa del proyecto: integración del motor determinístico de Three-Way Matching mediante un caso de uso de aplicación.

Antes de comenzar, revisar `AGENTS.md`, `docs/PRD.md`, `docs/ARCHITECTURE.md`, el decision log y la implementación actual del dominio.

### Objetivo

Implementar un `EvaluateInvoiceUseCase` que permita evaluar facturas estructuradas utilizando los repositorios PostgreSQL y el motor de dominio existente.

### Requisitos

- Reutilizar las entidades, repositorios, puertos y reglas ya implementados.
- Recuperar proveedores, PO, líneas de PO y GRN asociados.
- Obtener cantidades previamente aprobadas para pago, sin descontar facturas simplemente recibidas, rechazadas o retenidas.
- Recuperar la configuración de reglas aplicable.
- Resolver los datos necesarios para construir el contexto del motor.
- Invocar el motor sin duplicar ninguna regla de negocio en la capa de aplicación.
- Persistir los resultados de evaluación, discrepancias y datos relevantes para auditoría.
- Mantener diferenciados el resultado automático, el estado operativo y las decisiones humanas.
- Conservar explícitamente las evaluaciones incompletas; nunca convertirlas automáticamente en Auto-Pay.
- Agregar tests unitarios del caso de uso y tests de integración con PostgreSQL.

### Arquitectura

- `domain/`: reglas determinísticas y entidades de negocio.
- `application/`: coordinación del caso de uso y definición de puertos.
- `infrastructure/`: implementación de repositorios y persistencia.

No introducir dependencias de SQLAlchemy, FastAPI ni PostgreSQL dentro del dominio.

### Alcance

No implementar todavía:
- Extracción mediante Azure OpenAI.
- Procesamiento de PDF o imágenes.
- Frontend.
- Workflow de revisión humana.
- Nuevas dependencias sin autorización.

Mantener los cambios pequeños y verificables. No realizar commits ni push sin autorización.

Antes de modificar código, presentar un plan que identifique los componentes existentes reutilizables, los puertos faltantes y los cambios necesarios en la base de datos.

L2. Parent read required docs/decision log and explored concrete ports/schema; presented plan before writes. Owner confirmed empty-authoritative ledger and implementation with (verbatim): Dale
L3. Accepted technical plan: reuse ReferenceDataTransaction(session).load and existing frozen entities/engine; one evaluation-specific repository transaction with PostgreSQL REPEATABLE READ. Command supplies structured Invoice, optional existing invoice_id, explicit supplier_id, reliable associations/net prices, actor_id/origin. No fuzzy identity/line matching or tax derivation. New invoices use existing invoice/line/reference tables; existing invoice_id must retain identical raw invoice data, not silently correct it. Prior evaluations append-only via repository, keep original evidence. Approval ledger is read-only to the use case and starts authoritatively empty; no approval/payment workflow. Seed versioned default config0.02/5000, select by evaluation time, snapshot actual version/config. Add nullable resolved supplier ID and technical operational state to invoices; preserve raw references and duplicate invoice numbers. Missing supplier/evidence stays pending; unresolved other-case supplier identity makes history incomplete, not an empty authoritative history. Operational states received/evaluated/evaluation_incomplete never approved/paid. Roll back invoice/result/findings together on failure. Existing-row-safe new0002 migration, never edit0001. No production endpoint/CLI required; public integration interface is the use case and repository port.
L4. Routing: freeze DTO/port first, then launch application and PostgreSQL writers together with disjoint edit surfaces. Parent owns cross-layer integration and documentation; each writer owns its code and focused tests/docstrings. Independent verifier required for schema/data and snapshot risks. PostgreSQL tests use only disposable tmpfs service, never existing development volumes. No commits/push authorized.
L5. Common command/config/record DTOs and transaction port frozen in application/{dtos,ports}/invoice_evaluation.py. Contract RED missing module exit2; GREEN136 contract+matching tests in0.31s. Command copies association/net-price mappings and requires actor/origin; EvaluationRecord carries full result and snapshots, nullable ID before append. Expected public class SqlAlchemyInvoiceEvaluationRepository(engine) with transaction(), and EvaluateInvoiceUseCase(repository, clock=...) with execute(command). Disposable PostgreSQL ready at loopback32777/test_evaluation, lifecycle guard job16 maximum2400s; no development database touched. Workers can use TEST_DATABASE_URL with test-only credentials from handoff. Parent owns teardown and full regression.
L6. T2 writers mwjf0tqv-e-v7cz (application) and mwjf0tqy-f-jh6u (PostgreSQL) completed disjoint units. Both observed missing-module RED before implementation. Application GREEN30 tests; PostgreSQL seven adapter tests plus existing migration/metadata/constraints and legacy-upgrade nodes passed. Parent combined contracts/application/repository GREEN44 in5.31s, exit0; cross-layer integration GREEN8 in6.52s, exit0. All actual PostgreSQL tests used isolated fixture databases. Domain and original0001 unchanged. Owner/external untracked docs/PRD/PRD_01..05 documents appeared during work and remain outside this feature, preserved alongside .gitignore and AGENTS.md.
L7. Native assess returned nativeRisk=unassessable (untracked files require declaration); explicit escalation item1 yields high risk and independentVerifier=true. No native reviewDue/start continuation or approval was obtained. One independent verifier remains required for item1 and item4; no native review claim.
L8. T3 evidence: README/architecture/PRD updated, application example passed with exact stdout/empty stderr and persisted evidence (1test in1.95s). Full regression job17 exit0: 256passed, no skips/failures, in101.55s; 55 new tests. Differential job18 exit0 in8s compares base f562a53 and changed code on new schema with persisted incomplete evaluation: four import CLI cases identical exit/stdout/stderr; health/PO/unknown PO/unknown route identical HTTP status/body. Raw invoice/evaluation/state/approved totals unchanged, scratch and unique database removed. Syntax11 changed/new modules valid; Markdown fences balanced; domain, original0001, interfaces/main, dependencies/frontend/Compose unchanged. HEAD remainsf562a53 and index empty. T4 independent verification now starts; disposable service retained until it finishes.
L9. T4 verifier mwjfwrgt-g-qjpr PASS S1-S11, no blocking findings: focused55 passed in14.93s, three independent probes passed in2.99s, literal in-memory README example exact stdout/empty stderr/exit0. Promoted three probes to test_evaluation_independent.py using public business-state observations and schema-specific constraint inspection; parent GREEN3 in3.10s. Production code unchanged after full256 run; three promoted tests were run separately, not a claimed259-test full-suite run. Verified rollback/conflicts, duplicate/raw-reference/constraint-preserving upgrade and concurrent GRN/approval/history snapshot isolation. Native RDD approval remains unavailable, not claimed. Parent final git checks passed; HEAD f562a53, index empty, no commits/push, original domain/0001/interfaces/dependencies untouched. All unique pytest/differential databases removed; temporary tmpfs PostgreSQL stopped/removed, guard job16 exit0. Verifier reported its own scratch /tmp/invoice-verifier-Fsq5gt; parent did not inspect/delete outside project. Owner files and external docs/PRD documents remain outside feature scope. All four tasks complete; no application implementation blocker remains.
L10. Owner authorized local commits (verbatim): Dale commits
L11. Delivery plan: contracts+contract tests; application coordinator/snapshots+unit tests; schema0002/metadata+schema tests; adapter+repository tests; integration/independent regression tests+README/architecture/PRD; final task-evidence document. Application code and its30 tests form an approximately578-line cohesive slice, above the400-line guidance; retain readable code and all coverage rather than arbitrarily separating tests from behavior. Schema and snapshot-adapter high-risk units remain separate. Preserve .gitignore, AGENTS.md and docs/PRD/ external documents; no push/merge. Each implementation snapshot gets isolated index-tree checks, using prior PostgreSQL proof for unchanged content.
L12. First four commits created on the existing feature branch:8f0b6f6 contracts152lines, a2bf89e application578lines,025f48a schema226diff-lines,7caf80b adapter364lines. Isolated index-tree checks respectively7passed/37passed/metadata1passed/repository7tests-collected (collection is not execution). Prior full PostgreSQL proof applies to byte-identical implementation/test files; original file hashes checked before every unit. An auxiliary setup attempt failed before commits because tracked data/ already existed in git archive; diagnosed staging/HEAD, removed unnecessary symlink, reran checks successfully. No product fix or test failure. Owner files hash-unchanged.
L13. Integration/docs committed as23ca0f6 (428diff-lines). Complete isolated index-tree collection finds259tests; collection is explicitly not a new259-test execution. No PostgreSQL/full-suite rerun in this commit-only delivery: all previously verified implementation/test files are byte-identical, confirmed against worktree and committed blobs. Five implementation commits and empty index audited before closing the task record. Final unit is this evidence document; its self-referential commit ID is stored outside the document in delivery memory. No push/merge, no owner file changes, no source behavior changes; temporary isolation helper/snapshots removed during final audit.
