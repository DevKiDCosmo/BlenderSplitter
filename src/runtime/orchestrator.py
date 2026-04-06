"""Internal runtime orchestration primitives.

This module intentionally keeps behavior minimal for the first migration step.
"""

from __future__ import annotations

from dataclasses import dataclass, field
@dataclass
class RuntimeOperation:
    """Represents a runtime operation tracked by the facade."""

    name: str
    payload: dict[str, object] = field(default_factory=dict)
    state: str = "queued"


class RuntimeOrchestrator:
    """Coordinates runtime operations behind the facade boundary."""

    def __init__(self) -> None:
        self._operations: list[RuntimeOperation] = []

    def enqueue(self, operation: RuntimeOperation) -> None:
        self._operations.append(operation)

    def latest(self) -> RuntimeOperation | None:
        if not self._operations:
            return None
        return self._operations[-1]
