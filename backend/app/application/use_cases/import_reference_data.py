"""Validate prospective references and immutable identities before writing."""
from dataclasses import replace

from app.application.dtos.reference_data import ImportCount, ReferenceData, ReferenceDataError
from app.application.ports.reference_data import ReferenceDataRepository


def indexed(records, identity, label):
    result = {}
    for record in records:
        key = getattr(record, identity)
        if key in result:
            raise ReferenceDataError(f"Duplicate {label} identity")
        result[key] = record
    return result


def canonical(data: ReferenceData) -> ReferenceData:
    """Line identity is local to its parent, not list position or item code."""
    for records, identity, label in (
        (data.purchase_orders, "line_number", "PO line"),
        (data.goods_received_notes, "po_line_number", "GRN line"),
    ):
        for record in records:
            indexed(record.lines, identity, label)
    return ReferenceData(
        data.suppliers,
        tuple(replace(po, lines=tuple(sorted(po.lines, key=lambda line: line.line_number)))
              for po in data.purchase_orders),
        tuple(replace(grn, lines=tuple(sorted(grn.lines, key=lambda line: line.po_line_number)))
              for grn in data.goods_received_notes),
    )


def import_reference_data(data: ReferenceData, repository: ReferenceDataRepository) -> dict[str, ImportCount]:
    data = canonical(data)
    # All reads, comparisons, and writes share the adapter's transaction.
    with repository.transaction() as transaction:
        stored = canonical(transaction.load())
        prospective = []
        additions = []
        counts = {}
        for name, identity, label in (
            ("suppliers", "supplier_id", "supplier"),
            ("purchase_orders", "po_number", "PO"),
            ("goods_received_notes", "grn_number", "GRN"),
        ):
            incoming = indexed(getattr(data, name), identity, label)
            existing = indexed(getattr(stored, name), identity, label)
            new = []
            for key, record in incoming.items():
                if key in existing:
                    if record != existing[key]:
                        raise ReferenceDataError(f"Conflicting {label} content for existing identity")
                else:
                    new.append(record)
            counts[name] = ImportCount(len(new), len(incoming) - len(new))
            additions.append(tuple(new))
            prospective.append(existing | incoming)
        suppliers, orders, receipts = prospective
        for po in orders.values():
            if po.supplier_id not in suppliers:
                raise ReferenceDataError("PO references unknown supplier")
        for grn in receipts.values():
            po = orders.get(grn.po_number)
            if po is None:
                raise ReferenceDataError("GRN references unknown PO")
            line_numbers = {line.line_number for line in po.lines}
            if any(line.po_line_number not in line_numbers for line in grn.lines):
                raise ReferenceDataError("GRN references unknown PO-local line")
        transaction.add(ReferenceData(*additions))
    # Counts become visible only after commit succeeds.
    return counts
