"""Parameter administration persistence boundary; writes commit atomically."""
from typing import Protocol

from app.application.dtos.auth import Actor
from app.application.dtos.parameters import ParameterPatch


class ParameterRepository(Protocol):
    def current(self) -> dict: ...
    def history(self, *, offset: int, limit: int) -> dict: ...
    def change(self, expected_version: int, patch: ParameterPatch, actor: Actor) -> dict:
        """Serialize, compare current version, then append config+audit or no-op."""
        ...
