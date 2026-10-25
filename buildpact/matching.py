"""Fit screening and role assignment.

Two ideas drive this file:
  1. Hard constraints (goal overlap, enough weekly hours) are pass/fail and their
     reasons stay private to the student's own agent.
  2. Role assignment is an optimisation: give each role to a person so total
     "utility" (skill fit + how much they wanted that role) is as high as possible.
     Teams are small (<= 6), so we can simply try every arrangement.
"""
from __future__ import annotations

import itertools
import re
from dataclasses import dataclass

from .models import Idea, Profile, RoleSlot

# Small synonym table so "machine learning" and "ml" count as the same skill.
SYNONYMS = {
    "machine learning": "ml", "deep learning": "ml", "pytorch": "ml", "ai": "ml",
    "javascript": "js", "typescript": "js", "react": "frontend", "ui": "frontend",
    "ux": "design", "figma": "design", "node": "backend", "api": "backend",
    "fastapi": "backend", "flask": "backend", "sql": "data", "pandas": "data",
    "statistics": "stats", "writing": "research", "nlp": "ml", "llm": "ml",
}


def norm_skill(s: str) -> str:
    s = s.strip().lower()
    return SYNONYMS.get(s, s)


def _tokens(s: str) -> set[str]:
    return {norm_skill(t) for t in re.split(r"[\s/,&+-]+", s.lower()) if t and t != "lead"}


def role_matches(wanted: str, role_name: str) -> bool:
    """'ML' matches 'ML Lead'; 'frontend dev' matches 'Frontend'."""
    return bool(_tokens(wanted) & _tokens(role_name))


def skill_fit(p: Profile, role: RoleSlot) -> float:
    """Average of the student's level (0..1) over the skills the role needs."""
    if not role.skills:
        return 0.5
    levels = {norm_skill(k): v for k, v in p.skills.items()}
    return sum(levels.get(norm_skill(s), 0) / 5 for s in role.skills) / len(role.skills)


def preference(p: Profile, role: RoleSlot) -> float:
    """1.0 for their first-choice role, then 0.6, 0.3, else 0."""
    for rank, wanted in enumerate(p.wanted_roles[:3]):
        if role_matches(wanted, role.name):
            return (1.0, 0.6, 0.3)[rank]
    return 0.0


def utility(p: Profile, role: RoleSlot) -> float:
    return 0.6 * skill_fit(p, role) + 0.4 * preference(p, role)


@dataclass
class Screen:
    ok: bool
    fit: float
    private_reason: str = ""   # shown only to this student, never to others


def screen(p: Profile, idea: Idea) -> Screen:
    """Stage-1 check. Hard constraints first, then a soft fit score."""
    if not (p.ambition & idea.ambition):
        return Screen(False, 0.0, f"they're aiming for {', '.join(sorted(idea.ambition))}; "
                                  f"you said {', '.join(sorted(p.ambition))}")
    if p.hours_per_week < idea.min_hours:
        return Screen(False, 0.0, f"they need {idea.min_hours:g}+ hrs/week; you have "
                                  f"{p.hours_per_week:g}")
    fit = max((utility(p, r) for r in idea.roles_needed), default=0.0)
    if fit < 0.25:
        return Screen(False, fit, "your skills don't line up with any open role")
    return Screen(True, fit)


def best_assignment(people: list[Profile], roles: list[RoleSlot], owner_id: str
                    ) -> dict[str, str]:
    """Return user_id -> role name maximising total utility.

    The owner is always on the team. If there are more people than roles, the
    best subset is chosen and the rest form the waitlist.
    """
    k = min(len(people), len(roles))
    best, best_score = {}, -1.0
    for chosen in itertools.permutations(people, k):
        if owner_id not in {p.user_id for p in chosen}:
            continue
        for role_order in itertools.permutations(roles, k):
            score = sum(utility(p, r) for p, r in zip(chosen, role_order))
            if score > best_score + 1e-9:
                best_score = score
                best = {p.user_id: r.name for p, r in zip(chosen, role_order)}
    return best


@dataclass
class Conflict:
    role: str
    winner: str          # user_id who got the contested role
    loser: str           # user_id who wanted it first but didn't get it
    loser_role: str      # what the loser was assigned instead


def find_conflicts(assign: dict[str, str], people: dict[str, Profile],
                   roles: list[RoleSlot]) -> list[Conflict]:
    """A conflict = two team members whose #1 choice is the same role."""
    out = []
    for role in roles:
        wanters = [uid for uid in assign
                   if people[uid].wanted_roles and role_matches(people[uid].wanted_roles[0], role.name)]
        holder = next((uid for uid, r in assign.items() if r == role.name), None)
        if holder is None or len(wanters) < 2:
            continue
        for uid in wanters:
            if uid != holder:
                out.append(Conflict(role.name, holder, uid, assign[uid]))
    return out
