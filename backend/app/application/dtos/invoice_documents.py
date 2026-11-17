"""Framework-independent structured extraction and validated document contracts.

A draft retains missing fields as None. It is not a payment decision or a promise
that the document can be converted into the mandatory raw Invoice contract.
"""
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from hashlib import sha256
from typing import Literal


@dataclass(frozen=True)
class DocumentMetadata:
    file_name: str
    media_type: Literal["application/pdf", "image/png", "image/jpeg"]
    byte_size: int
    sha256: str
    page_count: int
    pixel_width: int | None = None
    pixel_height: int | None = None


@dataclass(frozen=True)
class ValidatedDocument:
    content: bytes
    metadata: DocumentMetadata

    def __post_init__(self):
        if (not isinstance(self.content, bytes) or len(self.content) != self.metadata.byte_size
                or sha256(self.content).hexdigest() != self.metadata.sha256):
            raise ValueError("Document content does not match metadata")


@dataclass(frozen=True)
class TokenUsage:
    input_tokens: int
    output_tokens: int

    def __post_init__(self):
        if any(type(value) is not int or value < 0 for value in (self.input_tokens, self.output_tokens)):
            raise ValueError("Token counters must be nonnegative integers")

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass(frozen=True)
class InvoiceLineDraft:
    description: str | None
    quantity: Decimal | None
    unit_price: Decimal | None
    line_total: Decimal | None
    po_line_reference: str | None
    po_number: str | None
    po_line_number: int | None
    unit_price_basis: Literal["net", "gross", "unknown"]


@dataclass(frozen=True)
class InvoiceDraft:
    supplier_name: str | None
    supplier_tax_id: str | None
    invoice_number: str | None
    invoice_date: date | None
    due_date: date | None
    po_references: tuple[str, ...] | None
    currency: str | None
    subtotal: Decimal | None
    tax_amount: Decimal | None
    total_amount: Decimal | None
    lines: tuple[InvoiceLineDraft, ...]


@dataclass(frozen=True)
class ExtractionResult:
    is_invoice: bool
    invoice: InvoiceDraft | None
    warnings: tuple[str, ...]
    usage: TokenUsage | None = None
    response_id: str | None = None
