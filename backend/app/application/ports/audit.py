"""Read-only coherent snapshot boundary; no audit mutation capability."""
from typing import Protocol
from uuid import UUID

from app.application.dtos.audit import AuditSnapshot


class AuditRepository(Protocol):
    def read(self, invoice_id: UUID) -> AuditSnapshot:
        """Return all supported facts from one snapshot, or raise AuditError."""
        ...
