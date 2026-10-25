"""Core data types for BuildPact.

Everything the agents know and agree on lives in these dataclasses. The engine
(engine.py) only reads and writes these objects; it never talks to Discord or an
LLM directly, which keeps the negotiation logic testable on its own.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Union

# What a student wants out of a project. A team is compatible only if every
# member shares at least one goal with the idea.
AMBITIONS = ("startup", "portfolio", "paper", "hackathon", "course")


@dataclass
class RoleSlot:
    """A role the project needs, e.g. 'ML Lead' needing ['ml', 'python']."""
    name: str
    skills: list[str]


@dataclass
class Idea:
    """A project idea, held at three disclosure levels.

    The owner's agent only ever sends the level the owner has approved:
    stage 1 = teaser, stage 2 = details, stage 3 = full_plan.
    """
    owner_id: str
    title: str
    teaser: str          # one line, safe to post publicly
    details: str         # problem + approach, shared with the shortlist only
    full_plan: str       # the real plan, shared after the charter is signed
    roles_needed: list[RoleSlot]
    ambition: set[str]
    min_hours: float     # weekly hours each member must be able to give
    weeks: int = 6


@dataclass
class Profile:
    """What one student's agent knows about its student. Mostly private."""
    user_id: str
    name: str
    skills: dict[str, int] = field(default_factory=dict)   # skill -> level 1..5
    wanted_roles: list[str] = field(default_factory=list)  # most wanted first
    ambition: set[str] = field(default_factory=set)
    hours_per_week: float = 0.0
    busy_days: list[str] = field(default_factory=list)     # "wed" or "2026-10-28"
    busy_reasons: dict[str, str] = field(default_factory=dict)  # never shared
    idea: Idea | None = None

    def missing_fields(self) -> list[str]:
        missing = []
        if not self.skills:
            missing.append("skills (e.g. python 5, react 3)")
        if not self.wanted_roles:
            missing.append("the role(s) you want")
        if not self.ambition:
            missing.append("your goal: " + " / ".join(AMBITIONS))
        if self.hours_per_week <= 0:
            missing.append("hours per week you can give")
        return missing

    @property
    def is_owner(self) -> bool:
        return self.idea is not None


class Phase(str, Enum):
    """Where the team is in its life. The engine moves forward through these."""
    POOL = "pool"                 # collecting profiles
    SCREENING = "screening"       # stage 1: teaser out, fit checked privately
    SHORTLISTED = "shortlisted"   # waiting for the owner to approve stage 2
    ROLES = "roles"               # stage 2 revealed, negotiating roles
    CHARTER = "charter"           # charter drafted, collecting signatures
    SIGNED = "signed"             # stage 3 revealed, team formed
    TASKS = "tasks"               # milestone tasks proposed, collecting confirms
    RUNNING = "running"           # plan confirmed


@dataclass
class Milestone:
    name: str
    due: date


@dataclass
class Charter:
    title: str
    roles: dict[str, str]                 # user_id -> role name
    weekly_hours: float                   # agreed minimum per member
    milestones: list[Milestone]
    decision_rule: str
    credit_rules: list[str]
    dropout_rule: str
    signatures: set[str] = field(default_factory=set)


@dataclass
class Task:
    id: str
    title: str
    role: str                 # the role this task naturally belongs to
    est_hours: float
    depends_on: list[str] = field(default_factory=list)
    assignee: str | None = None
    start: date | None = None
    due: date | None = None
    note: str = ""            # public explanation, e.g. "moved off a busy day"


# ----- What the engine emits. The transport (Discord, simulator) renders these.

@dataclass
class Button:
    label: str
    action: str               # e.g. "sign_charter" or "accept_offer:abc"
    style: str = "primary"    # primary | success | danger | secondary


@dataclass
class DM:
    """A private message from a student's own agent to that student."""
    user_id: str
    text: str
    buttons: list[Button] = field(default_factory=list)


@dataclass
class Channel:
    """A message on the shared negotiation table, posted by an agent."""
    agent_of: str | None      # user_id whose agent is speaking; None = system
    text: str


Out = Union[DM, Channel]  # works on Python 3.8+
