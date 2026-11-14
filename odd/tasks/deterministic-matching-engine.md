# Deterministic Three-Way Matching
Objective: pure, testable domain rules and engine with explicit pending-policy reporting. Branch: `feat/deterministic-matching-engine`. Feature commit authorized by owner; no push authorized. Baseline: `5d06cc7`. Forecast ~600 authored lines including tests, one cohesive domain behavior; no PR/delivery requested.
Checks: in-memory rule/engine pytest, domain independence, existing backend regressions; isolated PostgreSQL only if full integration is run. Risk: none — new unused pure domain modules; existing runtime code, entity contracts, dependencies and infrastructure are unchanged. Preserve owner's existing .gitignore modification.

## Specs
S1. Design (verbatim):
> Mantener el motor y sus reglas en la capa `domain/`, independientes de FastAPI, PostgreSQL y Azure OpenAI.
> Separar las reglas en componentes pequeños, con responsabilidades claras.
> Centralizar la evaluación y precedencia en un motor: `Rejected > Held for Review > Auto-Pay`.
> Devolver resultados estructurados con estado, códigos de regla y motivos explicativos.
> Mantener configurables los parámetros definidos en el challenge, especialmente la tolerancia de precio (2 % por defecto).
> Evitar abstracciones innecesarias y utilizar las entidades y componentes ya implementados.

S2. Initial rejection rules (verbatim):
> Ausencia de PO referenciada.
> PO referenciada inexistente.
> Proveedor de factura diferente al proveedor de la PO.
> Fecha de factura anterior a la fecha de la PO.

S3. Initial hold rules (verbatim):
> Cantidad facturada superior a la cantidad recibida, considerando la suma de GRN, únicamente en casos donde la comparación sea inequívoca.
> Diferencia de precio unitario superior a la tolerancia configurada, cuando exista una asociación inequívoca con la línea de PO.
> Línea sin correspondencia con una línea de PO, cuando la imposibilidad de asociación sea determinable.

S4. Completeness and deferred policies (verbatim):
> Asignarlo únicamente cuando todas las validaciones obligatorias hayan sido evaluadas y ninguna haya detectado discrepancias.
> No inventar políticas para:
> - Definición exacta de factura duplicada.
> - Facturación parcial y cantidades previamente facturadas.
> - Comparaciones con monedas distintas y tratamiento de impuestos.
> - Asociación de líneas entre múltiples PO.
> - Proveedores inactivos y PO cerradas.
> - Reevaluación posterior a correcciones o nuevas recepciones.
> Diseñar puntos de extensión para estas políticas sin implementar comportamientos arbitrarios.
> Si una validación obligatoria no puede completarse por una ambigüedad pendiente, representar esa situación como un resultado interno de evaluación incompleta. No tratarla como `Auto-Pay` ni como una decisión automática definitiva.

S5. Tests (verbatim):
> Crear pruebas unitarias independientes para cada regla, incluyendo:
> - Casos válidos e inválidos.
> - Límites exactos de tolerancia.
> - Agregación de cantidades de múltiples GRN.
> - Precedencia entre reglas.
> - Evaluaciones incompletas.
> - Independencia del motor respecto de infraestructura.

S6. Owner pending-check correction (verbatim):
> Lo que queda pendiente reservalo en un comentario en la funcion que le corresponde o si necesita tener una funcion para ese chequeo solo deja la funcion vacia, Por ahora dejemos eso como pendiente pero que no sea algo que impida probar el sistema y ver si pasa con los chequeos que si tenemos

S7. Scope/parameters from source supplied by owner (verbatim):
> El motor debe implementarse mediante código determinístico y testeable.
> Price tolerance | 2 % | Diferencia máxima permitida entre el precio unitario facturado y el precio unitario de la PO, evaluada por línea.
> Analyst approval limit | 5.000 | Importe máximo de factura que un AP Analyst puede aprobar o modificar por su cuenta. Por encima de este límite, la decisión debe escalarse al AP Manager.
> El AP Manager debe poder modificar estos parámetros sin cambios de código ni redeployment.

S8. Organizational refactor request (verbatim excerpts):
> Es un refactor organizativo, no una reimplementación.
> Conservar exactamente las reglas, los contratos, los parámetros configurables, la precedencia y el tratamiento de políticas pendientes.
> Esta estructura es orientativa. No quiero crear archivos o abstracciones innecesarias ni implementar todavía `evaluate_invoice.py`.

S9. Documentation and commit request (verbatim):
> Genial, commit y ACTUALIZA el README PARA poner explicacion del sistema

Implementation boundaries: accept the owner's pasted sections7/7.1 as supplied source, do not claim the PDF itself was inspected. Reuse immutable domain records without adding ORM/HTTP imports or changing their contracts. New modules under domain/matching (engine.py, rules.py, types.py, pending_policies.py), in-memory fixtures, standard library plus existing pytest only. Expose a provisional outcome of implemented checks separately from completeness/final outcome; pending checks never count as passed, but do not prevent executing/tests of available rules. Empty hooks/comments must identify pending policy, not silently approve it. Price comparison uses exact Decimal, 0.02 fractional tolerance, strict greater-than boundary; undefined percentage/ambiguous currency or tax basis must not become an invented rejection/hold. Engine accepts already-resolved supplier identity and explicit PO-local line association facts; no fuzzy matching, description equality, raw-reference parsing, currency conversion, tax adjustment or prior-invoice subtraction. Confirmed unmatchable lines differ from unresolved association. Configuration is immutable input, not hardcoded globals; analyst limit is carried for future review flow, not enforced here. Do not add manager endpoints, configuration storage, audit persistence, invoice decisions, IA/frontend integration, matching API, dependencies or migrations.

## Tasks
- T1 | S1-S7 | delegated (context backstop) | done | Pure contracts/rules/pending hooks/engine and docs implemented. Missing-module RED observed; focused51 tests GREEN, existing non-PostgreSQL36 tests passed. Parent read all new domain modules and example contract. Commit: this feature commit, feat(matching): add deterministic invoice evaluation.
- T2 | S1-S7 | parent | done | README example exact stdout/empty stderr verified; full suite123 passed without skips on temporary PostgreSQL; diff/scope/cleanup checks passed. Assessment unavailable, no review approval claimed. Commit: this feature commit, feat(matching): add deterministic invoice evaluation.

- T3 | S8-S9 | parent (small refactor/docs) | done | Four modules moved to matching/; structural RED/GREEN, four-module AST equivalence, README output and full123-test regressions passed. README now explains the system, intended workflow, actual capabilities, architecture, and limitations. No business behavior/dependencies changed. Commit: this feature commit, feat(matching): add deterministic invoice evaluation.

## Log
L1. Original owner request (verbatim):
Implementar el motor determinístico de Three-Way Matching siguiendo `AGENTS.md`, `docs/PRD.md`, `docs/ARCHITECTURE.md` y las secciones 7 y 7.1 del challenge original.

**Objetivo:** desarrollar las reglas cuyo comportamiento está claramente definido, manteniendo una arquitectura que permita incorporar fácilmente las aclaraciones pendientes del cliente.

### Diseño

- Mantener el motor y sus reglas en la capa `domain/`, independientes de FastAPI, PostgreSQL y Azure OpenAI.
- Separar las reglas en componentes pequeños, con responsabilidades claras.
- Centralizar la evaluación y precedencia en un motor: `Rejected > Held for Review > Auto-Pay`.
- Devolver resultados estructurados con estado, códigos de regla y motivos explicativos.
- Mantener configurables los parámetros definidos en el challenge, especialmente la tolerancia de precio (2 % por defecto).
- Evitar abstracciones innecesarias y utilizar las entidades y componentes ya implementados.

### Implementación inicial

Implementar las validaciones claramente especificadas:

**Rejected:**
- Ausencia de PO referenciada.
- PO referenciada inexistente.
- Proveedor de factura diferente al proveedor de la PO.
- Fecha de factura anterior a la fecha de la PO.

**Held for Review:**
- Cantidad facturada superior a la cantidad recibida, considerando la suma de GRN, únicamente en casos donde la comparación sea inequívoca.
- Diferencia de precio unitario superior a la tolerancia configurada, cuando exista una asociación inequívoca con la línea de PO.
- Línea sin correspondencia con una línea de PO, cuando la imposibilidad de asociación sea determinable.

**Auto-Pay:**
- Asignarlo únicamente cuando todas las validaciones obligatorias hayan sido evaluadas y ninguna haya detectado discrepancias.

### Requisitos pendientes de aclaración

No inventar políticas para:
- Definición exacta de factura duplicada.
- Facturación parcial y cantidades previamente facturadas.
- Comparaciones con monedas distintas y tratamiento de impuestos.
- Asociación de líneas entre múltiples PO.
- Proveedores inactivos y PO cerradas.
- Reevaluación posterior a correcciones o nuevas recepciones.

Diseñar puntos de extensión para estas políticas sin implementar comportamientos arbitrarios.

Si una validación obligatoria no puede completarse por una ambigüedad pendiente, representar esa situación como un resultado interno de evaluación incompleta. No tratarla como `Auto-Pay` ni como una decisión automática definitiva.

### Tests

Crear pruebas unitarias independientes para cada regla, incluyendo:
- Casos válidos e inválidos.
- Límites exactos de tolerancia.
- Agregación de cantidades de múltiples GRN.
- Precedencia entre reglas.
- Evaluaciones incompletas.
- Independencia del motor respecto de infraestructura.

### Restricciones

- No integrar todavía Azure OpenAI ni desarrollar funcionalidades del frontend.
- No agregar nuevas dependencias sin autorización.
- No modificar el comportamiento de reglas ya definidas.
- No implementar soluciones especulativas para los puntos pendientes.
- Mantener cambios pequeños, enfocados y fáciles de revisar.
- No realizar commits ni push sin autorización.

Antes de escribir código, revisar los componentes existentes y presentar un plan breve indicando qué se reutilizará, qué se agregará y qué quedará pendiente de las respuestas del cliente.

L2. Owner correction (verbatim):
Lo que queda pendiente reservalo en un comentario en la funcion que le corresponde o si necesita tener una funcion para ese chequeo solo deja la funcion vacia, Por ahora dejemos eso como pendiente pero que no sea algo que impida probar el sistema y ver si pasa con los chequeos que si tenemos

L3. Owner supplied source (verbatim):
# Technical Challenge — Rule Engine Specification

**Documento fuente:** `technical-challenge.pdf`
**Empresa:** Machines Like Me GmbH
**Secciones:** 7 (página 8) y 7.1 (página 9)

## 7. Rule Engine Specification

El sistema debe evaluar cada factura utilizando reglas determinísticas, respetando el siguiente orden de precedencia. Cada factura debe recibir exactamente un resultado automático definitivo.

### Rejected — Primera prioridad

La factura debe rechazarse si se cumple cualquiera de estas condiciones:

- No referencia ninguna Purchase Order (PO).
- La PO referenciada no existe.
- El proveedor de la factura no coincide con el proveedor de la PO.
- La factura es un duplicado de otra ya procesada.
- La fecha de la factura es anterior a la fecha de la PO.

Si se cumple cualquier condición de rechazo, el resultado es `Rejected`, independientemente de los importes o de otras discrepancias.

### Held for Review — Segunda prioridad

Si la factura no fue rechazada, debe quedar retenida para revisión cuando se cumpla al menos una de estas condiciones:

- La cantidad facturada de una línea supera la cantidad recibida según los Goods Received Notes (GRN).
- El precio unitario de una línea difiere del precio de la PO más allá de la tolerancia configurada.
- Una línea de la factura no puede asociarse a una línea de la PO.

### Auto-Pay — Resultado por defecto

Si no se cumple ninguna condición de rechazo ni de revisión, el resultado debe ser `Auto-Pay`.

Esto requiere que todas las líneas estén asociadas a líneas de PO, las cantidades facturadas estén cubiertas por las recepciones y los precios estén dentro de la tolerancia.

### Requisitos adicionales

- El motor debe implementarse mediante código determinístico y testeable.
- Durante la demo se evaluarán facturas no incluidas en los datos de prueba.
- Un AP Analyst puede modificar el estado final de una factura.
- Debe conservarse el resultado automático original, la decisión humana, la identidad del usuario, la fecha y hora, y el comentario.
- El diseño debe contemplar correcciones de datos extraídos después de que una factura haya recibido una decisión.

## 7.1. Rule Parameters

Los siguientes parámetros deben utilizarse como valores iniciales:

| Parámetro | Valor por defecto | Descripción |
|---|---|---|
| Price tolerance | 2 % | Diferencia máxima permitida entre el precio unitario facturado y el precio unitario de la PO, evaluada por línea. |
| Analyst approval limit | 5.000 | Importe máximo de factura que un AP Analyst puede aprobar o modificar por su cuenta. Por encima de este límite, la decisión debe escalarse al AP Manager. |

El AP Manager debe poder modificar estos parámetros sin cambios de código ni redeployment.

Cada modificación debe registrar la identidad de quien la realizó y la fecha y hora.

El efecto de modificar parámetros sobre facturas que ya recibieron un resultado automático es una decisión de diseño que debe documentarse.

---

## Indicaciones de implementación

Esta sección contiene recomendaciones de implementación, no requisitos adicionales del documento original.

Implementar primero las reglas inequívocas y mantener aisladas las políticas pendientes de aclaración, especialmente duplicados, facturación parcial, asociación de múltiples PO y tratamiento de monedas.

No inventar criterios para los puntos pendientes de respuesta del cliente.

Mantener el motor independiente de infraestructura y respetar la arquitectura hexagonal existente.

L4. Parent read docs, current branch/source status, and delegated a read-only map. Spot-checked invoice.py: invoice supplier fields are name/tax ID, not supplier_id; PO line reference is raw optional text. Actual source PDF was not found, owner supplied sections verbatim above. Owner .gitignore modification and untracked AGENTS.md/docs/PRD.md must remain untouched. No domain engine exists yet. Plan presented before creating this feature document/source writes.

L5. Worker implemented four focused domain modules and two in-memory test files, plus README example/decision D5 and minimal architecture update. RED missing matching_types module (exit2), final focused51 tests passed in0.13s; existing domain/health/reader36 tests passed in2.45s. Parent read types, rules, pending hooks and engine: no framework/ORM imports, explicit supplier/line facts, duplicate stub returnsNone, policy facts represent external verified evidence rather than local detection. All pending checks force final_status=None while provisional precedence remains observable. README example not yet executed at this point; parent T2 will verify it and full regressions.

L6. Parent executed the README in-memory example: exit0, exact stdout "Held for Review None False" and pending ['DUPLICATE', 'SUPPLIER_ACTIVITY', 'PO_STATUS'], empty stderr. Full backend suite against a newly created dedicated tmpfs PostgreSQL container:123 passed in81.52s, no skips/failures; command exit0 including automatic container cleanup. No live-volume changes or new dependencies. Final scope check confirms no changes to existing main.py, entity contracts, pyproject.toml, uv.lock or Compose. No differential check applies because existing runtime code is untouched. Frontend/build/browser checks not applicable: none changed.

L7. Native assess unavailable due undeclared untracked scope; returned self-verification=true, independentVerifier=false, no reviewDue continuation. No separate verifier or native review/approval invented. Parent read every new domain module and public example, checked git diff --check, confirmed temporary PostgreSQL removed, and preserved owner .gitignore modification. All requested initial engine tasks complete; pending customer policies remain explicit by design, not unfinished implementation tasks. No commits/push. Baseline HEAD remains5d06cc7 on feat/deterministic-matching-engine.

L8. Owner organizational refactor request (verbatim):
Quiero revisar y mejorar la organización del motor de Three-Way Matching dentro de nuestra arquitectura hexagonal, sin modificar su comportamiento.

Actualmente, los archivos `matching_rules.py`, `three_way_matching.py`, `matching_types.py` y `pending_policies.py` están ubicados en `backend/app/domain/services/`.

**La implementación no es arquitectónicamente incorrecta**, porque los Domain Services pertenecen a la capa de dominio. Sin embargo, considero que sería más apropiado organizar estos componentes dentro de un módulo específico `domain/matching/`, ya que representan una capacidad central del negocio.

### Separación de responsabilidades

Quiero mantener claramente diferenciadas las tres capas:

- **Domain:** contiene las entidades, reglas de negocio y motor determinístico que decide si una factura corresponde a `Rejected`, `Held for Review` o `Auto-Pay`. No debe depender de bases de datos, frameworks, APIs ni servicios externos.
- **Application:** contiene los casos de uso que coordinan el proceso. Por ejemplo, recuperar una factura, obtener la PO y los GRN mediante puertos, preparar el contexto, invocar el motor y persistir el resultado.
- **Infrastructure:** implementa los adaptadores concretos, como repositorios PostgreSQL, almacenamiento e integración con Azure OpenAI.

El motor de matching debe recibir todos los datos necesarios ya resueltos y limitarse a evaluar las reglas de negocio. No debe realizar consultas SQL, acceder a repositorios ni encargarse de la orquestación del proceso.

### Organización propuesta

```text
backend/app/
├── domain/
│   ├── entities/
│   └── matching/
│       ├── engine.py
│       ├── rules.py
│       ├── types.py
│       └── pending_policies.py
│
├── application/
│   ├── use_cases/
│   │   └── evaluate_invoice.py
│   └── ports/
│
└── infrastructure/
    └── persistence/
```

Esta estructura es orientativa. No quiero crear archivos o abstracciones innecesarias ni implementar todavía `evaluate_invoice.py`.

### Qué necesito ahora

1. Revisar la estructura existente y determinar si el cambio propuesto mejora realmente la cohesión del dominio.
2. Si corresponde, mover los módulos desde `domain/services/` hacia `domain/matching/`.
3. Actualizar los imports y tests afectados.
4. Conservar exactamente las reglas, los contratos, los parámetros configurables, la precedencia y el tratamiento de políticas pendientes.
5. Mantener las políticas sin definir aisladas para poder incorporarlas cuando recibamos las aclaraciones del cliente.
6. Ejecutar nuevamente los tests y verificar que no existan regresiones.
7. Actualizar la documentación de arquitectura si corresponde.

### Restricciones

- Es un refactor organizativo, no una reimplementación.
- No cambiar ninguna regla de negocio.
- No agregar dependencias ni nuevas capas.
- No implementar endpoints, integración con Azure OpenAI ni funcionalidades del frontend.
- No hacer commits ni push sin autorización.
- Respetar `AGENTS.md`, `docs/PRD.md` y `docs/ARCHITECTURE.md`.

**Antes de realizar modificaciones, explicame si considerás adecuada esta organización, qué ventajas tendría frente a la estructura actual y qué archivos sería necesario modificar.**

La prioridad es mantener una arquitectura hexagonal clara, modular y fácil de extender, sin introducir complejidad innecesaria.

que te parece esto?

L9. Parent explained recommendation, exact four-file move, affected tests/docs and empty-path architecture-test risk. Owner approval (verbatim): dale

L10. Refactor T3: architecture test first changed to require matching/ and expected four modules; RED1 failed because directory absent. Parent moved modules, updated imports/docs and removed unused services/.gitkeep. AST comparison of old/new modules excluding only top-level imports: identical for all4, preserving definitions/contracts/parameters/reasons and rule order. Focused51 tests GREEN in0.13s. No wrappers/use case/dependencies introduced; full regression and updated README example pending at this point.

L11. Refactor closure: updated README example exited0 with identical exact stdout and empty stderr. Full suite against isolated tmpfs PostgreSQL:123 passed in81.84s, no skips/failures; command exit0 included temporary-container cleanup. Final parent checks confirmed that container is absent, git diff --check passes, and existing application/infrastructure/entities/main.py/dependencies/Compose are unchanged. No stale old-module references in code/README/architecture; old paths in historical Log entries intentionally preserved. Source definitions/contracts/configuration/reasons unchanged by four-module non-import AST comparison. Architectural test cannot pass by scanning an empty path. No extra application use case, API, wrappers, frontend, IA, database writes or new dependencies. Native assessment remains unavailable due untracked scope; no independent/native review approval claimed. Owner .gitignore modification preserved. HEAD remains5d06cc7, no commits/push. T3 complete; only organization/import paths and architectural test coverage changed.

L12. Owner authorization and documentation request (verbatim):
Genial, commit y ACTUALIZA el README PARA poner explicacion del sistema

L13. README expanded with the business problem, Invoice/PO/GRN roles, intended extraction-to-evaluation workflow, deterministic decision precedence, implemented capabilities, pending policies, and hexagonal boundaries. Clearly distinguishes intended workflow from the current partial in-memory engine. No new source behavior. Public README example re-executed with exact stdout, exit0 and empty stderr; focused51 tests passed in0.13s. Existing full123-test proof remains applicable because no runtime code changed afterward. One cohesive feature commit planned with the new engine, its tests/docs, and this evidence; larger than the usual review target because splitting the single matching behavior from necessary contracts/tests would obscure the deliverable. Owner .gitignore, AGENTS.md and docs/PRD.md excluded. No push or merge authorized.
