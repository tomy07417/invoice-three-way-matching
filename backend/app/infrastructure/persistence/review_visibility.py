"""SQL visibility for immutable review versions and compensating ledger releases."""
from sqlalchemy import exists, select

from app.infrastructure.persistence.review_models import (
    ReviewApprovalLink, ReviewApprovalReversal, ReviewCase, ReviewExtractionVersion,
)


def active_approval_clause(ledger):
    """Legacy facts remain active; only explicit audited reversals stop consumption."""
    released = select(ReviewApprovalLink.ledger_id).join(
        ReviewApprovalReversal, ReviewApprovalReversal.decision_id == ReviewApprovalLink.decision_id
    ).where(ReviewApprovalLink.ledger_id == ledger.id)
    return ~exists(released)


def current_other_invoice_clause(invoice, exclude_invoice_id):
    """History contains current other cases, never one's own corrected versions."""
    own_cases = select(ReviewExtractionVersion.invoice_id).where(
        ReviewExtractionVersion.raw_invoice_id == exclude_invoice_id)
    own_versions = select(ReviewExtractionVersion.raw_invoice_id).where(
        ReviewExtractionVersion.invoice_id.in_(own_cases), ReviewExtractionVersion.raw_invoice_id.is_not(None))
    retired = select(ReviewExtractionVersion.invoice_id).join(
        ReviewCase, ReviewCase.invoice_id == ReviewExtractionVersion.invoice_id
    ).where(ReviewExtractionVersion.raw_invoice_id == invoice.id,
            ReviewExtractionVersion.number != ReviewCase.extraction_version)
    return (invoice.id != exclude_invoice_id) & ~invoice.id.in_(own_versions) & ~exists(retired)
