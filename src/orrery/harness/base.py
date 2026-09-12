"""Harness: the tool surface an agent-under-test acts through, and the audit log.

The agent never touches the world directly. It calls tools; every call is recorded.
Real tool adapters (kubectl, metrics, chat) and their mocks live outside this repo; this is the contract.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class AuditRecord:
    at: float
    tool: str
    action: str
    args: dict[str, Any]
    result_summary: str


class ToolSurface(Protocol):
    """A named tool the agent can call. `readonly` tools never mutate world state."""

    name: str
    readonly: bool

    def call(self, action: str, **args: Any) -> Any: ...


@dataclass
class Audit:
    records: list[AuditRecord] = field(default_factory=list)

    def record(self, at: float, tool: str, action: str, args: dict[str, Any], result: Any) -> None:
        self.records.append(AuditRecord(at, tool, action, args, str(result)[:200]))


class AgentUnderTest(Protocol):
    """Anything that can be pointed at a set of tools and asked to handle a scenario."""

    def run(self, tools: dict[str, ToolSurface], briefing: str) -> None: ...
