"""Fresh payment authorization shared by manual and automatic approval."""
from collections import defaultdict
from decimal import Decimal

from app.application.dtos.automation import AUTOMATIC_APPROVER
from app.application.dtos.review import ReviewError, parse_review_draft
from app.application.use_cases.process_invoice import resolve_draft_associations, resolve_draft_prices
from app.domain.matching.duplicates import normalize_invoice_number
from app.domain.matching.types import LineAssociation


def approval_quantities(tx, state, record):
    """Fresh physical availability and payment identity; overrides cannot bypass them."""
    if state['credit_note_pending']:
        raise ReviewError('credit_note_pending', 'A pending credit-note request blocks payment authorization')
    if record is None or not record.result.complete or state['raw_invoice_id'] != record.invoice_id:
        raise ReviewError('incomplete_evidence', 'Complete, current evaluation evidence is required before approval')
    draft = parse_review_draft(state['extraction'])
    evaluation = tx.evaluation_transaction(state)
    reference = evaluation.load_reference_data()
    suppliers = [supplier for supplier in reference.suppliers if supplier.tax_id == draft.supplier_tax_id]
    number = normalize_invoice_number(draft.invoice_number or '')
    if len(suppliers) != 1 or not number:
        raise ReviewError('incomplete_evidence', 'Resolve supplier identity and invoice number before approval')
    if any(supplier == suppliers[0].supplier_id and normalize_invoice_number(previous) == number
           for supplier, previous in tx.active_approved_invoices()):
        raise ReviewError('already_authorized', 'This supplier and invoice number already have an active payment authorization')
    purchase_orders = {po.po_number: po for po in reference.purchase_orders}
    resolved = resolve_draft_associations(draft, reference.purchase_orders)
    basis = resolve_draft_prices(draft).resolution
    grouped = defaultdict(Decimal)
    for index, line in enumerate(draft.lines):
        association = resolved.get(index)
        if (association is None or line.quantity is None
                or not line.quantity.is_finite() or line.quantity <= 0
                or basis.lines[index].basis != 'net' or line.unit_price is None or line.unit_price <= 0):
            raise ReviewError('unresolved_line', 'Every line needs a reliable PO link and positive quantity/net price before approval')
        grouped[association.target] += line.quantity
    if not grouped:
        raise ReviewError('incomplete_evidence', 'At least one fully associated invoice line is required')
    approved = evaluation.get_approved_quantities(tuple(purchase_orders))
    received = defaultdict(Decimal)
    for note in reference.goods_received_notes:
        for line in note.lines:
            received[LineAssociation(note.po_number, line.po_line_number)] += line.quantity_received
    proof = []
    for link, quantity in grouped.items():
        consumed = approved.get(link, Decimal(0))
        available = received[link] - consumed
        if quantity > available:
            raise ReviewError('insufficient_available_quantity',
                              'Received quantity is no longer available; refresh evidence before approving')
        proof.append(dict(po_number=link.po_number, po_line_number=link.po_line_number,
                          quantity=quantity, received=received[link], previously_approved=consumed, available=available))
    return grouped, proof


def automatically_approve(tx, state, record, at):
    """Append one system decision in the caller's serialized transaction/next revision.

    A prior applicable decision or sticky credit-note block wins over Auto-Pay.
    This authorizes payment; it never executes a bank payment.
    """
    if (state['current_decision_id'] is not None or state['credit_note_pending']
            or state['requires_reevaluation'] or record is None
            or not record.result.complete or record.result.final_status != 'Auto-Pay'):
        return False
    tx.ensure_case(state)
    quantities, proof = approval_quantities(tx, state, record)
    comment = 'Automatically approved by complete Auto-Pay matching; no bank payment executed.'
    state['current_decision_id'] = tx.append_decision(state, 'approve', AUTOMATIC_APPROVER, comment, at)
    state.update(operational_state='APPROVED', payment_blocked=False)
    tx.append_consumption(state, quantities, AUTOMATIC_APPROVER, at)
    tx.audit({**state, 'revision': state['revision'] + 1}, AUTOMATIC_APPROVER, 'decision', comment,
             {'action': 'approve', 'decision_id': state['current_decision_id'],
              'extraction_version': state['extraction_version'], 'evaluation_id': state['evaluation_id'],
              'approval_time_quantities': proof}, at)
    return True
