"""One reference-data transaction boundary; no generic repository framework."""
from contextlib import AbstractContextManager
from typing import Protocol

from app.application.dtos.reference_data import ReferenceData


class ReferenceDataTransaction(Protocol):
    def load(self) -> ReferenceData: ...

    def add(self, data: ReferenceData) -> None: ...


class ReferenceDataRepository(Protocol):
    def transaction(self) -> AbstractContextManager[ReferenceDataTransaction]: ...
