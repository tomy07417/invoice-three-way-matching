"""Server-resolved demo identity; a client-supplied role is never authority."""
from dataclasses import dataclass
from enum import StrEnum


class Role(StrEnum):
    INTAKE = "intake"
    AP_ANALYST = "ap_analyst"
    AP_MANAGER = "ap_manager"


@dataclass(frozen=True)
class Actor:
    actor_id: int
    username: str
    role: Role

    def __post_init__(self):
        if type(self.actor_id) is not int or self.actor_id <= 0:
            raise ValueError("Actor ID must be a positive integer")
        if not isinstance(self.username, str) or not self.username.strip():
            raise ValueError("Actor username must be nonblank")
        if not isinstance(self.role, Role):
            raise ValueError("Actor role must be server-resolved")

    def can_view(self, owner_id: int) -> bool:
        return self.actor_id == owner_id or self.role in (Role.AP_ANALYST, Role.AP_MANAGER)
