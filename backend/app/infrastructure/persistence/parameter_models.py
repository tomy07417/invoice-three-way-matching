"""Append-only administration evidence linked to immutable configurations."""
from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.persistence.models import Base


class ParameterChange(Base):
    __tablename__ = 'parameter_changes'

    version_id: Mapped[int] = mapped_column(ForeignKey('rule_configuration_versions.id'), primary_key=True)
    previous_version_id: Mapped[int] = mapped_column(ForeignKey('rule_configuration_versions.id'))
    actor_id: Mapped[int] = mapped_column(ForeignKey('users.id'))
    # Snapshot public identity rather than inferring the historical role later.
    actor_username: Mapped[str]
    actor_role: Mapped[str]
    comment: Mapped[str | None]
