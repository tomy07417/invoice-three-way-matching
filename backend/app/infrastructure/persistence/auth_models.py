"""Local demo accounts; shared Base, explicitly imported by the auth adapter."""
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Identity
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.persistence.models import Base


class User(Base):
    __tablename__ = "users"
    __table_args__ = (CheckConstraint("role IN ('intake', 'ap_analyst', 'ap_manager')", name="ck_user_role"),)

    id: Mapped[int] = mapped_column(Identity(), primary_key=True)
    username: Mapped[str] = mapped_column(unique=True)
    password_hash: Mapped[str]
    role: Mapped[str]


class UserSession(Base):
    __tablename__ = "user_sessions"

    token_hash: Mapped[str] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    csrf_token: Mapped[str]
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
