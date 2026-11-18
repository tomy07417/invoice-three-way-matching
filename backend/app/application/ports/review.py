"""Transactional review persistence boundary; authorization stays in the use case."""
from contextlib import AbstractContextManager
from typing import Protocol


class ReviewRepository(Protocol):
    def transaction(self, *, write: bool = False) -> AbstractContextManager: ...
