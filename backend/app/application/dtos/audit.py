"""Detached Manager audit contract. Unknown metadata is represented by null."""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal
from uuid import UUID

AuditKind = Literal['creation', 'extraction', 'evaluation', 'correction', 'reevaluation',
                    'decision', 'escalation', 'credit_note_request', 'state_change', 'approval_reversal']
AUDIT_KINDS = frozenset(AuditKind.__args__)


class AuditError(ValueError):
    def __init__(self, code: str, message: str, status: int):
        super().__init__(message)
        self.code, self.status = code, status


@dataclass(frozen=True)
class AuditFilters:
    offset: int = 0
    limit: int = 50
    kind: AuditKind | None = None
    actor_id: str | None = None
    time_from: datetime | None = None
    time_to: datetime | None = None

    def __post_init__(self):
        invalid = (type(self.offset) is not int or not 0 <= self.offset <= 2_147_483_647
                   or type(self.limit) is not int or not 1 <= self.limit <= 100
                   or self.kind is not None and self.kind not in AUDIT_KINDS
                   or self.actor_id is not None and (not isinstance(self.actor_id, str)
                                                      or not 1 <= len(self.actor_id) <= 128))
        for value in (self.time_from, self.time_to):
            if value is not None and (not isinstance(value, datetime) or value.tzinfo is None
                                      or value.utcoffset() is None):
                invalid = True
        if invalid or (self.time_from is not None and self.time_to is not None
                       and self.time_from > self.time_to):
            raise AuditError('invalid_request', 'Invalid audit request parameters', 422)


@dataclass(frozen=True)
class AuditActor:
    id: str | None = None
    name: str | None = None
    role: str | None = None
    type: Literal['recorded', 'system', 'unknown'] = 'unknown'


@dataclass(frozen=True)
class AuditProvenance:
    source: str
    source_id: str
    derived: bool = False
    note: str | None = None


@dataclass(frozen=True)
class AuditDiff:
    field: str
    before: object
    after: object
    before_known: bool
    derived: bool
    source: str


@dataclass(frozen=True)
class AuditEvent:
    id: str
    kind: AuditKind
    at: datetime | None
    actor: AuditActor
    provenance: AuditProvenance
    revision: int | None = None
    extraction_version: int | None = None
    evaluation_id: int | None = None
    current: bool = False
    comment: str | None = None
    diffs: tuple[AuditDiff, ...] = ()
    evidence: dict = field(default_factory=dict)


@dataclass(frozen=True)
class AuditSnapshot:
    invoice_id: UUID
    revision: int
    events: tuple[AuditEvent, ...]
