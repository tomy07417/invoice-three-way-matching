"""Coordinate structured invoice evaluation through one repository transaction."""
from datetime import datetime, timezone
from typing import Callable

from app.application.dtos.evaluation_snapshots import snapshot_context, snapshot_input
from app.application.dtos.invoice_evaluation import EvaluateInvoiceCommand, EvaluationRecord
from app.application.ports.invoice_evaluation import InvoiceEvaluationRepository
from app.domain.matching.engine import evaluate


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class EvaluateInvoiceUseCase:
    """Evaluate and append evidence; never approve an invoice or authorize payment.

    Public usage: ``EvaluateInvoiceUseCase(repository).execute(command)`` returns
    a persisted EvaluationRecord only after successful transaction exit. The
    repository supplies authoritative reference/configuration/history snapshots
    and approved-only consumption. Missing facts remain missing to the engine.

    actor_id and origin are trusted caller metadata for audit, not authentication
    or authorization. Entry points must establish that trust before invoking this
    use case. Errors propagate so the repository can roll back all staged writes.
    Existing invoice IDs must retain identical raw data; this is not a correction
    or human-review workflow.
    """

    def __init__(
        self,
        repository: InvoiceEvaluationRepository,
        *,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._repository = repository
        self._clock = clock

    def execute(self, command: EvaluateInvoiceCommand) -> EvaluationRecord:
        """Append one automatic evaluation atomically, preserving prior records."""
        with self._repository.transaction() as transaction:
            at = self._clock()
            if not isinstance(at, datetime) or at.utcoffset() is None:
                raise ValueError("Evaluation clock must return a timezone-aware datetime")
            reference = transaction.load_reference_data()
            supplier = next(
                (item for item in reference.suppliers if item.supplier_id == command.supplier_id),
                None,
            )
            invoice_id = transaction.register_invoice(
                command.invoice,
                supplier.supplier_id if supplier is not None else None,
                command.invoice_id,
            )
            configuration = transaction.get_configuration(at)
            po_numbers = command.invoice.po_references
            approved = transaction.get_approved_quantities(po_numbers)
            history = transaction.get_invoice_history(exclude_invoice_id=invoice_id)
            purchase_orders = tuple(
                po for po in reference.purchase_orders if po.po_number in po_numbers
            )
            grns = tuple(
                note for note in reference.goods_received_notes if note.po_number in po_numbers
            )
            result = evaluate(
                command.invoice,
                purchase_orders,
                grns,
                supplier=supplier,
                associations=command.associations,
                approved_quantities=approved,
                invoice_history=history,
                net_unit_prices=command.net_unit_prices,
                config=configuration.config,
            )
            draft = EvaluationRecord(
                invoice_id=invoice_id,
                configuration_id=configuration.configuration_id,
                evaluated_at=at,
                actor_id=command.actor_id,
                origin=command.origin,
                result=result,
                input_snapshot=snapshot_input(command),
                context_snapshot=snapshot_context(
                    supplier=supplier,
                    purchase_orders=purchase_orders,
                    grns=grns,
                    approved_quantities=approved,
                    invoice_history=history,
                    configuration=configuration,
                ),
            )
            saved = transaction.append_evaluation(draft)
        return saved
