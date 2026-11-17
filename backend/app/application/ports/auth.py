"""Account/session persistence boundary for the approved local demo."""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from app.application.dtos.auth import Actor, Role


class AuthError(Exception):
    """Safe public failure; status is meaningful to HTTP adapters only."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class UserRecord:
    user_id: int
    username: str
    password_hash: str = field(repr=False)
    role: Role

    @property
    def actor(self) -> Actor:
        return Actor(self.user_id, self.username, self.role)


@dataclass(frozen=True)
class SessionRecord:
    token_hash: str = field(repr=False)
    actor: Actor
    csrf_token: str = field(repr=False)
    expires_at: datetime


class AuthRepository(Protocol):
    def create_user(self, username: str, password_hash: str, role: Role) -> Actor: ...
    def find_user(self, username: str) -> UserRecord | None: ...
    def create_session(self, record: SessionRecord) -> None: ...
    def find_session(self, token_hash: str) -> SessionRecord | None: ...
    def delete_session(self, token_hash: str) -> None: ...
