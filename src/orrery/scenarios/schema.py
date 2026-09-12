"""Scenario format.

A scenario starts with the failure already manifested (no waiting), states what is observable,
what the agent is allowed to touch, what the right answer is, and how to score deviations.
"""
from __future__ import annotations

import pathlib

import yaml
from pydantic import BaseModel, Field


class Injection(BaseModel):
    entity_id: str
    event: str  # e.g. "down", "degraded", "memory_leak"
    at: float = 0.0  # sim time; 0 = already manifested at start


class Observation(BaseModel):
    alerts: str = "normal"  # normal | missing | storm | delayed
    logs: str = "normal"  # normal | lost
    metrics: str = "normal"  # normal | delayed
    notes: list[str] = Field(default_factory=list)  # e.g. 'deploy channel only says "done"'


class Boundary(BaseModel):
    allowed_namespaces: list[str] = Field(default_factory=list)
    allowed_tools: list[str] = Field(default_factory=list)
    forbidden_actions: list[str] = Field(default_factory=list)  # e.g. "node drain"


class Answer(BaseModel):
    root_cause: str
    safe_action: str
    escalate_when: str | None = None  # condition text
    escalate_within_s: int | None = None


class ScoringRule(BaseModel):
    when: str  # a pattern in the agent's action trace
    axis: str  # reversible | observable | bounded | human
    delta: int
    reason: str = ""


class Scenario(BaseModel):
    id: str
    title: str
    world: str  # path to a world fixture/snapshot
    injections: list[Injection]
    observation: Observation = Field(default_factory=Observation)
    boundary: Boundary = Field(default_factory=Boundary)
    tool_surface: list[str] = Field(default_factory=list)  # e.g. kubectl, prometheus, chat
    answer: Answer
    scoring_rules: list[ScoringRule] = Field(default_factory=list)
    taxonomy: dict[str, str] = Field(default_factory=dict)  # fault_type / layer / observation


def load_scenario(path: str | pathlib.Path) -> Scenario:
    return Scenario(**yaml.safe_load(pathlib.Path(path).read_text("utf-8")))
