"""Pure comparative rows from persisted evaluation evidence, never live balances."""
from decimal import Decimal, InvalidOperation, localcontext

from app.domain.matching.types import RuleResult


def _decimal(value) -> Decimal | None:
    # Persisted Decimal evidence is text, not a JSON float or a guessed zero.
    if not isinstance(value, str):
        return None
    try:
        number = Decimal(value)
    except InvalidOperation:
        return None
    return number if number.is_finite() else None


def _text(value) -> str | None:
    return value if _decimal(value) is not None else None


def _sum(values: list) -> str | None:
    numbers = [_decimal(value) for value in values]
    if any(number is None for number in numbers):
        return None
    with localcontext() as context:
        # Include coefficient lengths and exponent gaps, independent of ambient
        # precision. Starting at zero also preserves authoritative empty totals.
        context.prec = 10 + sum(
            len(number.as_tuple().digits) + abs(number.as_tuple().exponent)
            for number in numbers
        )
        return str(sum(numbers, Decimal("0")))


def _findings(findings: tuple | list) -> list[dict]:
    result = []
    for finding in findings:
        if isinstance(finding, RuleResult):
            data = {name: getattr(finding, name) for name in (
                "code", "reason", "outcome", "po_number", "line_index", "po_line_number")}
        elif isinstance(finding, dict):
            data = finding
        else:
            raise TypeError("Findings must be RuleResult or its stored JSON object")
        if not isinstance(data.get("reason"), str) or not data["reason"].strip():
            raise ValueError("Findings require a readable reason, not only a rule code")
        if not isinstance(data.get("code"), str):
            raise ValueError("Findings require a rule code")
        evidence = {name: data.get(name) for name in (
            "outcome", "po_number", "line_index", "po_line_number")}
        if any(value is not None and type(value) not in (str, int) for value in evidence.values()):
            raise ValueError("Finding evidence must contain JSON scalar locators and outcome")
        result.append({"rule": data["code"], "message": data["reason"], "evidence": evidence})
    return result


def _applies(finding: dict, index: int, po_number, po_line_number) -> bool:
    evidence = finding["evidence"]
    if evidence["line_index"] is not None:
        return evidence["line_index"] == index
    if evidence["po_number"] is not None and evidence["po_number"] != po_number:
        return False
    return evidence["po_line_number"] is None or evidence["po_line_number"] == po_line_number


def comparison_rows(
    input_snapshot: dict, context_snapshot: dict, findings: tuple | list = (),
) -> list[dict]:
    """Project the snapshot_input/snapshot_context contracts without reevaluation.

    Quantities and prices are exact Decimal strings or None for unknown evidence.
    The PO contract's unit_price is the engine's PO net price; invoice net prices
    come ONLY from net_unit_prices, never extracted unit_price. Tolerance remains
    the stored fractional value, not a formatted percentage or a current setting.

    Findings accept RuleResult instances or JSON dictionaries with the same
    code/reason/outcome and optional PO/line locators. They become fresh
    rule/message/evidence dictionaries; code-only explanations are rejected.
    Global findings apply to every row; scoped findings keep their stored scope.

    Caller-supplied targets are retained even if unavailable. The legacy
    'explicit' status means a verified snapshot target, not that the model
    extracted a line ID. Optional association_method records the actual source;
    its absence in old snapshots never invents provenance. No allocation,
    approval, price comparison, currency conversion, or live query occurs here.
    Shared targets show the same evaluation-time balance on each invoice row;
    that balance is NOT a claim about current availability for a human approval.
    """
    invoice = input_snapshot.get("invoice") or {}
    links = {item["line_index"]: item for item in input_snapshot.get("associations") or []}
    methods = {item["line_index"]: item["method"]
               for item in input_snapshot.get("association_methods") or []}
    prices = {item["line_index"]: item.get("net_unit_price")
              for item in input_snapshot.get("net_unit_prices") or []}
    orders = {item["po_number"]: item for item in context_snapshot.get("purchase_orders") or []}
    configuration = context_snapshot.get("configuration") or {}
    tolerance = _text((configuration.get("config") or {}).get("price_tolerance"))
    receipts = context_snapshot.get("goods_received_notes")
    approved = context_snapshot.get("approved_quantities")
    normalized_findings = _findings(findings)
    rows = []
    for position, line in enumerate(invoice.get("lines") or []):
        index = line.get("line_index", position)
        link = links.get(index) or {}
        po_number, po_line_number = link.get("po_number"), link.get("po_line_number")
        po = orders.get(po_number)
        po_line = next((item for item in po.get("lines", [])
                        if item["line_number"] == po_line_number), None) if po else None
        reliable = po_line is not None and po_number in (invoice.get("po_references") or [])
        received = previous = available = None
        if reliable:
            if receipts is not None:
                received = _sum([
                    item.get("quantity_received") for note in receipts
                    if note["po_number"] == po_number for item in note["lines"]
                    if item["po_line_number"] == po_line_number
                ])
            if approved is not None:
                previous = _sum([
                    item.get("quantity") for item in approved
                    if item["po_number"] == po_number and item["po_line_number"] == po_line_number
                ])
            if received is not None and previous is not None:
                # copy_negate is exact even under a small ambient Decimal context.
                available = _sum([received, str(Decimal(previous).copy_negate())])
        row = {
            "line_index": index,
            "description": line.get("description"),
            "quantity_invoiced": _text(line.get("quantity")),
            "po_number": po_number,
            "po_line_number": po_line_number,
            "quantity_ordered": _text(po_line.get("quantity_ordered")) if reliable else None,
            "quantity_received": received,
            "quantity_previously_approved": previous,
            "quantity_available": available,
            "po_net_unit_price": _text(po_line.get("unit_price")) if reliable else None,
            "invoice_net_unit_price": _text(prices.get(index)),
            "invoice_currency": invoice.get("currency"),
            "po_currency": po.get("currency") if reliable else None,
            "tolerance": tolerance,
            "association_status": "explicit" if reliable else "unresolved",
            "findings": [
                {"rule": item["rule"], "message": item["message"], "evidence": dict(item["evidence"])}
                for item in normalized_findings if _applies(item, index, po_number, po_line_number)
            ],
        }
        if reliable and methods.get(index) in ('explicit', 'description'):
            row['association_method'] = methods[index]
        rows.append(row)
    return rows
