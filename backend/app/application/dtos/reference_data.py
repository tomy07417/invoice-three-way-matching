"""Immutable import input and header-only result counts."""
from dataclasses import dataclass

from app.domain.entities import GoodsReceivedNote, PurchaseOrder, Supplier


class ReferenceDataError(ValueError):
    """Safe, deterministic import failure suitable for the CLI."""


@dataclass(frozen=True)
class ReferenceData:
    suppliers: tuple[Supplier, ...]
    purchase_orders: tuple[PurchaseOrder, ...]
    goods_received_notes: tuple[GoodsReceivedNote, ...]


@dataclass(frozen=True)
class ImportCount:
    inserted: int
    unchanged: int
