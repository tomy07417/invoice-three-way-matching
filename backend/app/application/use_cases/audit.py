"""Manager authorization and filter/pagination over one detached read snapshot."""
from datetime import datetime, timezone
from uuid import UUID

from app.application.dtos.audit import AuditFilters
from app.application.dtos.auth import Role
from app.application.dtos.review import review_value
from app.application.ports.audit import AuditRepository
from app.application.ports.auth import AuthError
from app.application.use_cases.sessions import SessionService


class AuditService:
    def __init__(self, sessions: SessionService, repository: AuditRepository):
        self.sessions, self.repository = sessions, repository

    def authorize(self, token: str | None):
        actor = self.sessions.resolve(token).actor
        if actor.role != Role.AP_MANAGER:
            raise AuthError('AP Manager access is required', 403)
        return actor

    def history(self, token: str | None, invoice_id: UUID, filters: AuditFilters = AuditFilters()) -> dict:
        self.authorize(token)
        snapshot = self.repository.read(invoice_id)
        events = [event for event in snapshot.events
                  if (filters.kind is None or event.kind == filters.kind)
                  and (filters.actor_id is None or event.actor.id == filters.actor_id)
                  and (filters.time_from is None or event.at is not None and event.at >= filters.time_from)
                  and (filters.time_to is None or event.at is not None and event.at <= filters.time_to)]
        # Unknown times sort last, never placed at upload time. Stable source IDs
        # break ties; revision orders review actions sharing a recorded timestamp.
        events.sort(key=lambda event: (event.at is None, event.at or datetime.max.replace(tzinfo=timezone.utc),
                                      event.revision if event.revision is not None else -1, event.id))
        return review_value(dict(invoice_id=snapshot.invoice_id, revision=snapshot.revision,
            events=events[filters.offset:filters.offset + filters.limit], total=len(events),
            offset=filters.offset, limit=filters.limit))
