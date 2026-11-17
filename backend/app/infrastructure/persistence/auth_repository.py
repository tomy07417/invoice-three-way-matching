"""Short synchronous transactions on a borrowed engine; no credential logging."""
from contextlib import contextmanager

from sqlalchemy import delete, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.application.dtos.auth import Actor, Role
from app.application.ports.auth import AuthError, SessionRecord, UserRecord
from app.infrastructure.persistence.auth_models import User, UserSession


class SqlAuthRepository:
    def __init__(self, engine: Engine):
        self.engine = engine

    @contextmanager
    def _transaction(self):
        try:
            with Session(self.engine) as session, session.begin():
                yield session
        except SQLAlchemyError:
            raise AuthError("Account service unavailable", 503) from None

    def create_user(self, username: str, password_hash: str, role: Role) -> Actor:
        with self._transaction() as session:
            user = User(username=username, password_hash=password_hash, role=role.value)
            session.add(user)
            try:
                session.flush()
            except IntegrityError:
                raise AuthError("Account already exists", 409) from None
            actor = Actor(user.id, user.username, Role(user.role))
        return actor

    def find_user(self, username: str) -> UserRecord | None:
        with self._transaction() as session:
            user = session.scalar(select(User).where(User.username == username))
            return None if user is None else UserRecord(user.id, user.username, user.password_hash, Role(user.role))

    def create_session(self, record: SessionRecord) -> None:
        with self._transaction() as session:
            session.add(UserSession(token_hash=record.token_hash, user_id=record.actor.actor_id,
                                    csrf_token=record.csrf_token, expires_at=record.expires_at))

    def find_session(self, token_hash: str) -> SessionRecord | None:
        with self._transaction() as session:
            result = session.execute(select(UserSession, User).join(User, User.id == UserSession.user_id)
                                     .where(UserSession.token_hash == token_hash)).one_or_none()
            if result is None:
                return None
            stored, user = result
            return SessionRecord(stored.token_hash, Actor(user.id, user.username, Role(user.role)),
                                 stored.csrf_token, stored.expires_at)

    def delete_session(self, token_hash: str) -> None:
        with self._transaction() as session:
            session.execute(delete(UserSession).where(UserSession.token_hash == token_hash))
