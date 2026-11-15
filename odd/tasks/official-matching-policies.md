# Official Matching Policies
Objective: apply confirmed business policies to documentation and pure domain evaluation, preserving explicit completeness and hexagonal boundaries. Branch: feat/deterministic-matching-engine; baseline25c1211. Task-specific commits authorized; no push/merge authorized. No dependencies or operational workflows.
Checks: RED/GREEN focused pytest, README example, full backend regression on disposable PostgreSQL. Forecast ~900 authored lines excluding this log; incremental units, no delivery requested. Route: inline contracts/integration; parallel scoped quantity and duplicate writers (independent substantial rules/tests, disjoint paths).

## Specs
S1. "Facturación parcial: la cantidad disponible es la cantidad total recibida en GRN menos las cantidades previamente aprobadas para pago. No descontar facturas simplemente recibidas o retenidas. Si la cantidad facturada supera lo disponible, retener la factura completa; no realizar pagos parciales."
S2. "Duplicados: mismo proveedor y mismo número de factura implica duplicado, independientemente del importe o del archivo. Un número diferente con idéntico importe y fecha debe generar Held for Review. La normalización del número debe ser una política explícita."
S3. "Moneda y precios: comparar precios unitarios netos de impuestos contra los precios de la PO, con tolerancia del 2 % en ambas direcciones. No comparar silenciosamente importes de monedas distintas."
S4. "Correcciones: cualquier modificación de datos relevantes debe provocar una nueva evaluación. Una decisión humana anterior no puede permanecer vigente si cambian los datos sobre los que se tomó. Conservar todas las versiones y decisiones anteriores en la auditoría."
S5. "Proveedor inactivo: nunca Auto-Pay. Si también existe una condición explícita de rechazo, prevalece Rejected."
S6. "PO cerrada: no rechazar únicamente por estar cerrada. Mantener los chequeos normales de duplicados y matching."
S7. "Múltiples PO: validar cada PO referenciada, incluyendo su pertenencia al proveedor. Una línea que no pueda asociarse confiablemente a una única línea de PO debe quedar Held for Review."
S8. "Notas de crédito: registrar la solicitud con comentario obligatorio y auditoría. Impedir el pago mientras la solicitud esté pendiente. No desarrollar un workflow completo de notas de crédito."
S9. "GRN posteriores: utilizar las cantidades efectivamente recibidas al momento de la evaluación, aunque la fecha del GRN sea posterior a la fecha de factura."
S10. Proposed designs: "Moneda distinta de la PO: Held for Review, sin conversión automática." "PO cerrada: Held for Review, salvo que corresponda Rejected." "Nuevos GRN: reevaluación manual auditada." "Cambios de parámetros: aplicarlos a futuras evaluaciones, preservando decisiones históricas." "Normalización de números de factura: ignorar diferencias de espacios, guiones y mayúsculas/minúsculas, conservando el valor original." "Reenvío de factura rechazada: permitir corrección o nueva versión vinculada al caso anterior, sin perder trazabilidad ni permitir pagos duplicados." "Asociación de líneas: exigir una correspondencia única y confiable; ante ambigüedad, Held for Review."
S11. "Mantener el motor dentro de domain/matching/, independiente de infraestructura." "La capa application/ deberá obtener mediante puertos las cantidades recibidas, las cantidades previamente aprobadas, los datos de proveedores y las PO." "Separar claramente: Resultado automático del matching. Estado operativo de la factura. Decisiones humanas. Historial de auditoría." "No implementar consultas SQL ni acceso a repositorios dentro del dominio."
S12. "No modificar todo el sistema en una sola tarea." "Respetar AGENTS.md. No incorporar dependencias, realizar commits ni hacer push sin autorización."
S13. Accepted focused answer: "dale" to same-supplier/same-currency scope of the equal-total/date alert and the incremental proposal. This stage implements documentation and pure matching only; persistence, transactions, human authorization, version/audit and credit-note workflows are later stages.

S14. Delivery/documentation request (verbatim): "Commits y actualiza la documentacion"

## Tasks
- T1 | S1-S13 | inline | done | Official requirements/design/deferred workflows documented. Commits:60d13f9 (D7), a1c48c8 (D8), 2093eef (PRD/architecture/public contract); verification evidence in this docs commit.
- T2 | S1-S3,S5-S7,S11 | inline | done | Frozen original-preserving InvoiceHistoryEntry and explicit snapshot inputs. RED missing type exit2, GREEN52 tests before integration. Commits:a1c48c8 and2093eef.
- T3 | S1-S2,S9-S10,S13 | two parallel scoped writers | done | Independent quantity/duplicate rules with RED/GREEN and parent acceptance. Commits:60d13f9 quantity, a1c48c8 duplicates. Isolated committed-snapshot checks74/113 passed.
- T4 | S2,S10,S13 | merged into T3 | dropped | Duplicate work is tracked in the single parallel-rules parent task T3, not omitted. No commit.
- T5 | S1-S3,S5-S7,S9-S11,S13 | inline | done | Integrated statuses, currency, reliable associations, exact grouped quantities and explicit snapshots. Commit:2093eef; isolated snapshot129 tests and exact README example passed.
- T6 | S1-S13 | inline | done | Full201-test proof and public README example apply to unchanged runtime committed in2093eef. Operational S4/S8 and persistence remain deferred. Evidence: this docs commit.

- T7 | S14 | inline | done | Delivery documented with three verified code slices and this final docs unit: docs(matching): record policy verification and commit evidence. Owner .gitignore/AGENTS.md excluded, approved PRD included. No push/merge.

## Log
L1. Owner request (verbatim):
Recibimos las respuestas oficiales del cliente a las nueve preguntas de negocio pendientes.

Quiero actualizar la especificación funcional y el motor determinístico de Three-Way Matching conforme a estas aclaraciones.

### Reglas confirmadas

1. **Facturación parcial:** la cantidad disponible es la cantidad total recibida en GRN menos las cantidades previamente aprobadas para pago. No descontar facturas simplemente recibidas o retenidas. Si la cantidad facturada supera lo disponible, retener la factura completa; no realizar pagos parciales.
2. **Duplicados:** mismo proveedor y mismo número de factura implica duplicado, independientemente del importe o del archivo. Un número diferente con idéntico importe y fecha debe generar Held for Review. La normalización del número debe ser una política explícita.
3. **Moneda y precios:** comparar precios unitarios netos de impuestos contra los precios de la PO, con tolerancia del 2 % en ambas direcciones. No comparar silenciosamente importes de monedas distintas.
4. **Correcciones:** cualquier modificación de datos relevantes debe provocar una nueva evaluación. Una decisión humana anterior no puede permanecer vigente si cambian los datos sobre los que se tomó. Conservar todas las versiones y decisiones anteriores en la auditoría.
5. **Proveedor inactivo:** nunca Auto-Pay. Si también existe una condición explícita de rechazo, prevalece Rejected.
6. **PO cerrada:** no rechazar únicamente por estar cerrada. Mantener los chequeos normales de duplicados y matching.
7. **Múltiples PO:** validar cada PO referenciada, incluyendo su pertenencia al proveedor. Una línea que no pueda asociarse confiablemente a una única línea de PO debe quedar Held for Review.
8. **Notas de crédito:** registrar la solicitud con comentario obligatorio y auditoría. Impedir el pago mientras la solicitud esté pendiente. No desarrollar un workflow completo de notas de crédito.
9. **GRN posteriores:** utilizar las cantidades efectivamente recibidas al momento de la evaluación, aunque la fecha del GRN sea posterior a la fecha de factura.

### Decisiones de diseño propuestas

Registrar en el decision log, diferenciándolas claramente de los requisitos del cliente:

- Moneda distinta de la PO: Held for Review, sin conversión automática.
- PO cerrada: Held for Review, salvo que corresponda Rejected.
- Nuevos GRN: reevaluación manual auditada.
- Cambios de parámetros: aplicarlos a futuras evaluaciones, preservando decisiones históricas.
- Normalización de números de factura: ignorar diferencias de espacios, guiones y mayúsculas/minúsculas, conservando el valor original.
- Reenvío de factura rechazada: permitir corrección o nueva versión vinculada al caso anterior, sin perder trazabilidad ni permitir pagos duplicados.
- Asociación de líneas: exigir una correspondencia única y confiable; ante ambigüedad, Held for Review.

### Arquitectura

Mantener el motor dentro de `domain/matching/`, independiente de infraestructura.

La capa `application/` deberá obtener mediante puertos las cantidades recibidas, las cantidades previamente aprobadas, los datos de proveedores y las PO.

Separar claramente:
- Resultado automático del matching.
- Estado operativo de la factura.
- Decisiones humanas.
- Historial de auditoría.

No implementar consultas SQL ni acceso a repositorios dentro del dominio.

### Plan de trabajo

Antes de implementar:

1. Actualizar `docs/PRD.md` y crear o actualizar el decision log con las aclaraciones oficiales.
2. Identificar qué reglas existentes necesitan modificaciones.
3. Identificar qué datos adicionales necesitan los contratos del dominio.
4. Proponer una implementación incremental, empezando por las reglas puras y sus tests.
5. Separar las funcionalidades que requieren persistencia, transacciones, autorización humana o auditoría para etapas posteriores.

No modificar todo el sistema en una sola tarea.

Respetar `AGENTS.md`. No incorporar dependencias, realizar commits ni hacer push sin autorización.

Primero presentar el plan y las decisiones de diseño para revisión.

L2. Parent presented an incremental read-only plan, including explicit facts, whole-invoice holds, grouped invoice-line quantities, and separate operational/audit workflows. Asked whether equal-total/date alerts should be scoped to same supplier and currency. Owner answer (verbatim): dale
L3. Scope accepted: documentation and pure domain rules/tests only. Historical evidence supplied to duplicate rules excludes the current case; filtering case versions and preventing repeat payment remain application/transaction responsibilities, not a new workflow here. Approved quantities are an authoritative PO-local snapshot; None means unavailable, empty mapping explicitly means no prior approved quantities. Net unit prices are explicit PO-comparison inputs; no tax computation. Supplied GRNs are the actually received evaluation snapshot, never filtered by invoice date. Named risk: no persistence/security/concurrency implementation; transactional prevention of double approval is deferred and must not be claimed.
L4. Documentation first: PRD now records all9 official answers, README decisions D7-D13 distinguish design policy, architecture marks operational deferral. Structural documentation check passed. Explicit history record RED import failure exit2; GREEN52 existing plus contract tests. Worker APIs: check_quantity adds keyword-only approved_quantity defaultDecimal0; check_duplicate_history(invoice, supplier_id, history) returns DUPLICATE/SIMILAR_INVOICE findings, with None history incomplete and empty tuple authoritative. Parent will replace blanket clearance inputs with supplier/approved_quantities/net_unit_prices/invoice_history snapshots in T5. T3 combines two independent writer units to keep a truthful single in-progress parent task; T4 merged/dropped.
L5. Both scoped workers returned within allowed paths. Quantity RED23 failures/24 passes, GREEN50 tests; duplicate RED missing-module exit2, GREEN39 tests. Parent read both modules and quantity-test diff, then independently ran their focused suite:89 passed in0.10s, exit0. Tests cover exact approved subtraction, PO-local isolation/later GRNs, normalization and same-supplier/currency similarity. T3 accepted; whole-invoice status/association integration remains T5, not yet verified at this point.
L6. T5 public-engine RED36 failures/2 passes (new supplier/snapshot inputs unsupported). Integrated explicit supplier/history/approved/net-price snapshots, grouped exact invoice quantity, inactive/closed/currency/ambiguous HOLD and reject precedence. Removed now-obsolete clearance class and pending stub; README runnable contract and architecture updated. GREEN127 tests. Added every-referenced-PO currency coverage: RED2 failures, then PO_CURRENCY rule GREEN129 focused tests in0.19s. README example exit0/exact stdout/empty stderr. No existing CLI invokes these changed/new helpers; no CLI differential check applies. PO/supplier/date checks, Decimal boundaries, validation and immutable historical config behavior remain covered. T6 full integration proof remains pending.
L7. Full regression job15 launched against a disposable tmpfs PostgreSQL, random loopback port, bounded startup/test timeout and guaranteed cleanup; result still pending. Parent scope checks passed: diff whitespace clean, no changes to main/application/infrastructure/entities/dependencies/Compose/frontend, HEAD25c1211 unchanged, index empty and owner's exact .gitignore diff preserved. Native assessment unavailable due undeclared untracked scope; returned self-verification=true, independentVerifier=false, no reviewDue continuation. No native review approval claimed.
L8. Final full backend suite:201 passed in82.01s, no skips/failures, command exit0 including automatic temporary-PostgreSQL cleanup. Parent verified container absent, no staged changes, HEAD25c1211 unchanged, no Application/Infrastructure/entities/dependency/frontend changes and git diff --check clean. Focused129 and README exact-output proof remain passing. No CLI differential applies: neither existing CLI command calls matching; existing checks and immutable parameter semantics are preserved by tests. T6 complete. S4/S8 and all operational persistence/authorization/audit/transactions remain explicitly deferred per approved plan; they are not partially implemented or claimed complete. No commits or push.
L9. Owner delivery request (verbatim): Commits y actualiza la documentacion
L10. Delivery plan: separate quantity rule/tests, duplicate contracts/rules/tests, integrated engine/public example, and official specification/evidence. Stage intermediate file versions in the index without rewriting working files or existing history; validate each index tree in an isolated ignored project-local scratch directory. README now records observed201/129-test evidence and deferred operational scope; PRD describes the implemented pure rules rather than future work. No new runtime behavior in this delivery task.
L11. Authorized code commits created:60d13f9 feat(matching): subtract quantities approved for payment (142 changed lines; isolated index tree74 tests plus exact README passed); a1c48c8 feat(matching): detect normalized invoice duplicates (283 lines; isolated113 tests plus exact README passed);2093eef feat(matching): apply confirmed supplier and PO policies (614 lines; isolated129 tests plus exact README passed). The larger integration unit keeps changed contracts, engine, tests, PRD and public example coherent. Each slice was scoped explicitly; intermediate index trees were tested in ignored project-local .cache directories, then removed, without rewriting working files or prior commits. All runtime changes are now committed and identical to the201-test verified implementation; only documentation/evidence remains for this docs commit. Native assessment remains unavailable due untracked scope; no native approval claimed. README verification record distinguishes observed test results from deferred operational capabilities. Final docs unit:docs(matching): record policy verification and commit evidence. No push/merge.
