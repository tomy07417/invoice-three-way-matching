"""Conservative whole-line identity resolution, independent of commercial values."""
from dataclasses import dataclass
from typing import Literal, Sequence

from ..entities import PurchaseOrder
from .types import LineAssociation


@dataclass(frozen=True)
class InvoiceLineIdentity:
    description: str | None
    po_number: str | None = None
    po_line_number: int | None = None
    po_line_reference: str | None = None


@dataclass(frozen=True)
class ResolvedAssociation:
    target: LineAssociation
    method: Literal['explicit', 'description']


def _description(value: str | None) -> str:
    # Do not remove punctuation, item codes, dimensions or numeric specifications.
    return ' '.join(value.casefold().split()) if value else ''


def resolve_line_associations(
    lines: Sequence[InvoiceLineIdentity],
    po_references: Sequence[str],
    purchase_orders: Sequence[PurchaseOrder],
) -> dict[int, ResolvedAssociation]:
    """Resolve only unique targets within the invoice's referenced orders.

    Supported raw references are a PO-local integer or exact PO-number/integer.
    Unknown raw syntax cannot supply missing structured identity. Explicit
    evidence is intersected, never replaced with a convenient description.
    Multiple invoice lines may share a target; the existing engine aggregates
    their quantities before checking physical availability.
    """
    targets = [(LineAssociation(po.po_number, item.line_number), _description(item.description))
               for po in purchase_orders if po.po_number in po_references for item in po.lines]
    resolved = {}
    for index, line in enumerate(lines):
        description = _description(line.description)
        matches = [link for link, text in targets if description and description == text]
        raw = (line.po_line_reference or '').strip()
        explicit = line.po_number is not None or line.po_line_number is not None or bool(raw)
        if not explicit:
            if len(matches) == 1:
                resolved[index] = ResolvedAssociation(matches[0], 'description')
            continue
        # A PO number is valid scope, not an incomplete product identity. Use
        # its unique description when no line reference is printed. Recognized
        # raw references still pass through the conflict checks below.
        if line.po_number is not None and line.po_line_number is None and not raw:
            scoped = [link for link in matches if link.po_number == line.po_number]
            if len(scoped) == 1:
                resolved[index] = ResolvedAssociation(scoped[0], 'description')
            continue
        # A line number without an authoritative PO remains partial evidence.
        if line.po_number is None and line.po_line_number is not None:
            continue
        candidates = [link for link, _ in targets]
        if line.po_number is not None:
            candidates = [link for link in candidates if link.po_number == line.po_number]
        if line.po_line_number is not None:
            if type(line.po_line_number) is not int or line.po_line_number <= 0:
                continue
            candidates = [link for link in candidates if link.po_line_number == line.po_line_number]
        complete = line.po_number is not None and line.po_line_number is not None
        if raw.isascii() and raw.isdecimal():
            number = raw.lstrip('0') or '0'
            candidates = [link for link in candidates if str(link.po_line_number) == number]
            complete = True
        elif '/' in raw:
            po_number, number = raw.rsplit('/', 1)
            if number.isascii() and number.isdecimal():
                number = number.lstrip('0') or '0'
                candidates = [link for link in candidates if link.po_number == po_number
                              and str(link.po_line_number) == number]
                complete = True
        # A complete structured ID may coexist with an opaque original label;
        # only a recognizable contradictory identity is evidence of conflict.
        if not complete or len(candidates) != 1:
            continue
        target = candidates[0]
        if matches and target not in matches:
            continue
        resolved[index] = ResolvedAssociation(target, 'explicit')
    return resolved
