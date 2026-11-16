# PRD — Supplier Invoice Three-Way Matching with Human Review

## Source and scope
Derived from the Machines Like Me technical challenge (September 2026). This is a working implementation summary, **not** a replacement for the original 16-page challenge. The challenge and later written stakeholder clarifications are authoritative.

## Objective
Upload supplier invoices (PDF/image), extract header and line items, compare with supplier master, purchase orders (PO), and goods received notes (GRN), and produce exactly one automatic outcome: `Auto-Pay`, `Rejected`, or `Held for Review`. Allow human review with a complete audit trail.

## Roles
- Intake user: upload invoice, view status and understandable reasons; cannot modify reference data.
- AP Analyst: review held invoices and history, correct extraction, approve/reject/request credit note with mandatory comment, override within approval limit.
- AP Manager: analyst capabilities plus above-limit override approval, rule parameter management, complete audit visibility.

## Input and reference data
- Invoice PDF or image.
- `suppliers.json`: supplier ID, legal name, tax ID, default currency, active/inactive status.
- `purchase_orders.json`: PO number, supplier, date, currency, status, ordered lines/quantities/prices.
- `goods_received_notes.json`: GRN number, PO, receipt date, received quantities per PO line.
- Reference data must be loaded by a documented command, used as source of truth, and not hard-coded.

## Extraction requirements
Headers: `supplier_name`, `supplier_tax_id`, `invoice_number`, `invoice_date`, `due_date` if present, `po_references`, `currency`, `subtotal`, `tax_amount`, `total_amount`.

Per line: `description`, `quantity`, `unit_price`, `line_total`, `po_line_reference` if determinable.

## Deterministic rules (ordered precedence)
1. **Rejected:** no referenced PO; PO nonexistent; invoice supplier does not match PO supplier; duplicate invoice; invoice date earlier than PO date.
2. **Held for Review:** not rejected, and at least one line exceeds available received quantity, has a net unit price outside tolerance, or cannot be reliably associated with one PO line. Available quantity is total received minus quantities previously approved for payment; received or held invoices do not consume availability. Any excess holds the entire invoice, never a partial payment. An inactive supplier cannot receive Auto-Pay.
3. **Auto-Pay:** every mandatory validation is complete and neither rejection nor hold applies. This is an automatic evaluation outcome, not a payment instruction. Missing mandatory evidence leaves the evaluation incomplete, not approved.

Default configurable parameters: price tolerance **2%** per line, inclusive in both directions on net-of-tax unit prices; analyst approval limit **5,000**. The approval-limit currency/escalation details are not defined by these clarifications and remain outside the pure engine. Only a manager may change parameters; record actor and timestamp. New parameter snapshots apply to future evaluations, preserving historical results (design decision, not retroactive reevaluation).

## Human review and audit
Keep automatic matching results, operational invoice state, human decisions, and append-only audit history distinct. Preserve each automatic result, human decision, actor, timestamp, comment, and relevant data version. A relevant invoice-data correction triggers a new evaluation and invalidates the applicability of any prior human decision without deleting it. Escalate above-limit overrides to a manager. Record parameter changes.

A credit-note request requires a comment and audit entry and prevents payment while pending; no complete credit-note workflow is required. This is an operational payment block, not a replacement for the historical automatic matching result. Automatic evaluation evidence is now persisted separately. Corrected-invoice versioning, authorization, approval transactions, human-decision audit and credit-note requests remain later implementation stages.

## Technical constraints
- Technology choice flexible; Next.js and Python preferred.
- Azure OpenAI model provided; deployment name `TomasAmundarain-LIA3` is passed as `model`.
- Server-side calls only; keep key/endpoint secret. Rate limit: 10,000 tokens/minute, roughly 3–4 invoice extractions/minute. Handle HTTP 429 and model failures.
- Private GitHub repo with `RecruitmentMLM` access; comprehensive README including architecture, trade-offs, deployment and decision log.
- Cloud deployment optional but strongly weighted; Azure preferred. If deployed, authentication and role separation required.
- Demo: Friday 20 Nov 2026 15:00 UTC−3. Last eligible commit: Thursday 19 Nov 2026 15:00 UTC−3.

## Official client clarifications
These answers supersede the former nine open questions; implementation status must not be confused with specification completeness.

1. **Partial invoicing:** available quantity is all GRN receipts minus quantities previously approved for payment, not invoices merely received or held. Excess quantity holds the whole invoice; no partial payments.
2. **Duplicates:** the same supplier and invoice number is a duplicate regardless of amount or file. A different number with identical amount and invoice date requires review. Number normalization must be explicit.
3. **Currency and prices:** compare net-of-tax unit prices against PO prices with symmetric 2% tolerance. Never silently compare different currencies.
4. **Corrections:** any relevant data modification requires reevaluation, invalidates an earlier human decision's applicability, and preserves every previous version and decision in audit.
5. **Inactive supplier:** never Auto-Pay; an explicit rejection still takes precedence.
6. **Closed PO:** closure alone is not a rejection. Continue normal duplicate and matching checks.
7. **Multiple POs:** validate every referenced PO, including supplier ownership. A line without a unique reliable PO-line association is held.
8. **Credit-note request:** mandatory comment and audit, payment blocked while pending; no full credit-note workflow.
9. **Later GRNs:** use quantities actually received at evaluation, even if the GRN date is later than the invoice date. Do not filter receipts by invoice date.

## Accepted implementation policies (not client quotations)
See README decisions D7-D13 for rationale and scope.

- Currency mismatch: Held for Review without conversion.
- Closed PO: Held for Review unless Rejected takes precedence.
- Later GRNs: explicit manual audited reevaluation, not automatic import-triggered reevaluation.
- Parameter changes: future evaluations only; historical results are immutable.
- Invoice-number comparison ignores whitespace, hyphens and case; original text is retained.
- Equal-total/date alerts are scoped to the same supplier and currency; amount means invoice total. Different-number comparisons do not convert currencies.
- Reliable associations must identify one PO-local line. Multiple invoice lines targeting that same line are compared together so receipts cannot be reused.
- Rejected submissions may be corrected/versioned within a linked case; historical traceability and payment deduplication remain required. The pure engine receives history for other cases, not its own prior versions; same-case history exclusion is now implemented for unchanged invoice IDs, while corrected-version linking and transactional payment checks remain later work.

## Current implementation boundary
The clarified pure rules are implemented using supplied supplier/PO/GRN snapshots, prior approved quantities, invoice history and net unit prices. The public contract and observed verification record are documented in the README. EvaluateInvoiceUseCase now obtains these through a transactional port and invokes the unchanged Domain, which performs no SQL or repository access. One PostgreSQL REPEATABLE READ transaction registers structured input, selects effective persisted configuration, reads explicit approval consumption/other-case history and appends the result, all findings and original context/configuration snapshots with actor/time. Existing invoice IDs require identical raw data and are excluded from their own history. The owner confirmed the approved ledger starts authoritatively empty; evaluation never creates approval/payment facts. Technical state is received/evaluated/evaluation_incomplete, not approval. Missing mandatory evidence and unresolved other-case supplier history remain incomplete with no final status. Human decisions, approval-ledger writes, concurrent approval protection, corrections, a user-facing GRN reevaluation workflow, linked resubmissions, authorization and human-decision audit remain later stages. Unknown evidence is not a blanket policy bypass. Zero/nonpositive PO price percentage bases and credit/negative-line arithmetic remain undefined; do not invent those policies.

## Initial milestone
Set up project structure and local infrastructure, import supplier/PO/GRN data, expose a read-only PO lookup with aggregated GRN quantities, and test it. Do not implement ambiguous payment policies until clarified or explicitly approved and documented.

## Development process
All coding agents must follow `AGENTS.md`: consult before new libraries/stack choices, keep commits granular and coherent, request permission before committing/pushing, and report small verifiable increments.
