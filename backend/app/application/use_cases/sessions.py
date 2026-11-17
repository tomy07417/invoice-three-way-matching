"""Local password and opaque-session lifecycle, independent of HTTP and SQL."""
from collections.abc import Callable, Collection
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import re
import secrets

from app.application.dtos.auth import Actor, Role
from app.application.ports.auth import AuthError, AuthRepository, SessionRecord

_ITERATIONS = 600_000


def _password_digest(password: str, salt: bytes) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _ITERATIONS).hex()


def _hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    return f"pbkdf2_sha256${_ITERATIONS}${salt.hex()}${_password_digest(password, salt)}"


def _verify_password(password: str, encoded: str) -> bool:
    algorithm, iterations, salt, digest = encoded.split("$")
    if algorithm != "pbkdf2_sha256" or iterations != str(_ITERATIONS):
        return False
    return hmac.compare_digest(_password_digest(password, bytes.fromhex(salt)), digest)


@dataclass(frozen=True)
class SessionIdentity:
    actor: Actor
    csrf_token: str = field(repr=False)
    expires_at: datetime


class SessionService:
    def __init__(self, repository: AuthRepository, *,
                 clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
                 lifetime: timedelta = timedelta(hours=8)):
        if lifetime <= timedelta(0):
            raise ValueError("Session lifetime must be positive")
        self.repository = repository
        self.clock = clock
        self.lifetime = lifetime
        # Unknown accounts perform the same expensive password check.
        self._dummy_password = _hash_password(secrets.token_urlsafe(32))

    def provision(self, username: str, password: str, role: Role) -> Actor:
        if not isinstance(username, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", username):
            raise AuthError("Username must contain 1-64 letters, digits, dots, underscores or hyphens")
        if not isinstance(password, str) or not 12 <= len(password) <= 1024:
            raise AuthError("Password must contain 12-1024 characters")
        if not isinstance(role, Role):
            raise AuthError("Invalid role")
        return self.repository.create_user(username, _hash_password(password), role)

    def login(self, username: str, password: str) -> tuple[str, SessionIdentity]:
        if not isinstance(username, str) or not isinstance(password, str) or len(username) > 64 or len(password) > 1024:
            raise AuthError("Invalid username or password", 401)
        user = self.repository.find_user(username)
        try:
            valid = _verify_password(password, user.password_hash if user else self._dummy_password)
        except (ValueError, TypeError):
            valid = False
        if not valid or user is None:
            raise AuthError("Invalid username or password", 401)
        token = secrets.token_urlsafe(32)
        record = SessionRecord(hashlib.sha256(token.encode()).hexdigest(), user.actor,
                               secrets.token_urlsafe(32), self.clock() + self.lifetime)
        self.repository.create_session(record)
        return token, SessionIdentity(record.actor, record.csrf_token, record.expires_at)

    def resolve(self, session_token: str | None) -> SessionIdentity:
        if not isinstance(session_token, str) or not 1 <= len(session_token) <= 128:
            raise AuthError("Authentication required", 401)
        record = self.repository.find_session(hashlib.sha256(session_token.encode()).hexdigest())
        if record is None or record.expires_at <= self.clock():
            raise AuthError("Authentication required", 401)
        return SessionIdentity(record.actor, record.csrf_token, record.expires_at)

    def require(self, session_token: str | None, csrf_token: str | None, *,
                roles: Collection[Role] | None = None, owner_id: int | None = None) -> Actor:
        """Authorize mutating use cases from server state, never a submitted Actor/role.

        Future document use cases must invoke this themselves, not just their UI/router.
        Read-only routes use resolve(token).actor and enforce can_view(owner_id).
        """
        identity = self.resolve(session_token)
        if not isinstance(csrf_token, str) or len(csrf_token) > 128 or not hmac.compare_digest(identity.csrf_token.encode(), csrf_token.encode()):
            raise AuthError("CSRF validation failed", 403)
        actor = identity.actor
        if (roles is not None and actor.role not in roles) or (owner_id is not None and not actor.can_view(owner_id)):
            raise AuthError("Forbidden", 403)
        return actor

    def logout(self, session_token: str | None, csrf_token: str | None) -> None:
        self.require(session_token, csrf_token)
        self.repository.delete_session(hashlib.sha256(session_token.encode()).hexdigest())
