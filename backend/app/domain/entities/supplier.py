"""Supplier reference data, without payment-policy decisions."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Supplier:
    supplier_id: str
    legal_name: str
    tax_id: str
    default_currency: str
    status: str
    address: str
