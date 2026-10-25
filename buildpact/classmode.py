"""Class mode: a TA forms balanced project teams for a whole class in one command.

Each student's agent contributes only what the matcher needs (skills, goal, hours);
the public result shows names and roles, never anyone's hours or goals.

Objective (lower is better), per team:
  * role coverage  — every team should have someone good at each project role
  * goal clash     — "startup" and "pass the course" students in one team fight
  * hours spread   — a 15 h/week and a 4 h/week student resent each other
and across teams:
  * strength spread — no stacked "super team"
Solved with a snake draft followed by pairwise-swap local search (deterministic seed).
"""
from __future__ import annotations

import random
import statistics

from .matching import best_assignment, skill_fit
from .models import Profile, RoleSlot

ROLES = [RoleSlot("Product Lead", ["product"]), RoleSlot("ML Lead", ["ml", "python"]),
         RoleSlot("Data Pipeline", ["data", "python"]), RoleSlot("Frontend", ["frontend", "design"])]

FIRST = ["Aanya", "Ben", "Chloe", "Diego", "Emma", "Farhan", "Grace", "Hiro", "Isha", "Jamal", "Kavya", "Liam",
         "Mei", "Noah", "Olivia", "Pranav", "Quinn", "Riya", "Sofia", "Tariq", "Uma", "Vikram", "Wen", "Xavier",
         "Yara", "Zane", "Aisha", "Bruno", "Camila", "Dev", "Elena", "Felix", "Gia", "Hassan", "Ines", "Jonah",
         "Kai", "Lena", "Mateo", "Nora"]
SKILLS = ["product", "ml", "python", "data", "frontend", "design"]


def synth_class(n: int, seed: int = 7) -> list[Profile]:
    rnd = random.Random(seed)
    out = []
    for i in range(n):
        name = FIRST[i % len(FIRST)] + ("" if i < len(FIRST) else f" {i // len(FIRST) + 1}")
        main = rnd.choice(SKILLS)
        skills = {s: rnd.randint(1, 3) for s in rnd.sample(SKILLS, 3)}
        skills[main] = rnd.randint(4, 5)
        if main in ("ml", "data"):
            skills["python"] = max(skills.get("python", 0), rnd.randint(3, 5))
        goal = rnd.choices(["course", "portfolio", "startup"], weights=[5, 4, 2])[0]
        hours = {"course": rnd.randint(3, 7), "portfolio": rnd.randint(6, 10), "startup": rnd.randint(9, 15)}[goal]
        p = Profile(f"class:{i}", name, skills=skills, ambition={goal}, hours_per_week=float(hours))
        p.wanted_roles = [r.name for r in sorted(ROLES, key=lambda r: -skill_fit(p, r))][:2]
        out.append(p)
    return out


def _team_cost(team: list[Profile]) -> float:
    cover = sum(max(0.0, 0.7 - max(skill_fit(p, r) for p in team)) for r in ROLES)
    goals = [g for p in team for g in p.ambition]
    top = max(set(goals), key=goals.count)
    clash = sum(1 for p in team if top not in p.ambition)
    hours = [p.hours_per_week for p in team]
    return 8 * cover + 1.5 * clash + (max(hours) - min(hours)) / 4


def strength(team: list[Profile]) -> float:
    return sum(max(skill_fit(p, r) for p in team) for r in ROLES) / len(ROLES)


def total_cost(teams: list[list[Profile]]) -> float:
    st = [strength(t) for t in teams]
    spread = statistics.pstdev(st) if len(st) > 1 else 0.0
    return sum(_team_cost(t) for t in teams) + 20 * spread


def metrics(teams: list[list[Profile]]) -> dict:
    covered = sum(1 for t in teams for r in ROLES if max(skill_fit(p, r) for p in t) >= 0.6)
    st = [strength(t) for t in teams]
    clashes = 0
    for t in teams:
        goals = [g for p in t for g in p.ambition]
        top = max(set(goals), key=goals.count)
        clashes += sum(1 for p in t if top not in p.ambition)
    return {"coverage": covered / (len(teams) * len(ROLES)), "strength_spread": max(st) - min(st),
            "goal_clashes": clashes,
            "hours_gap": statistics.mean(max(p.hours_per_week for p in t) - min(p.hours_per_week for p in t)
                                         for t in teams)}


def form_teams(students: list[Profile], size: int = 4, seed: int = 7, iters: int = 6000) -> dict:
    rnd = random.Random(seed)
    k = max(1, len(students) // size)
    # baseline: random grouping (what a TA does today)
    shuffled = students[:]
    rnd.shuffle(shuffled)
    baseline = [shuffled[i::k] for i in range(k)]
    # snake draft by each student's best role fit
    order = sorted(students, key=lambda p: -max(skill_fit(p, r) for r in ROLES))
    teams: list[list[Profile]] = [[] for _ in range(k)]
    for i, p in enumerate(order):
        rnd_i, pos = divmod(i, k)
        teams[pos if rnd_i % 2 == 0 else k - 1 - pos].append(p)
    best = total_cost(teams)
    for _ in range(iters):
        a, b = rnd.sample(range(k), 2) if k > 1 else (0, 0)
        if a == b:
            break
        i, j = rnd.randrange(len(teams[a])), rnd.randrange(len(teams[b]))
        teams[a][i], teams[b][j] = teams[b][j], teams[a][i]
        c = total_cost(teams)
        if c < best - 1e-9:
            best = c
        else:
            teams[a][i], teams[b][j] = teams[b][j], teams[a][i]
    result = []
    for n, t in enumerate(teams, 1):
        roles = best_assignment(t, ROLES, t[0].user_id) if len(t) >= len(ROLES) else \
            best_assignment(t, ROLES[:len(t)], t[0].user_id)
        goals = [g for p in t for g in p.ambition]
        result.append({"team": n, "members": [{"id": p.user_id, "name": p.name,
                                                "role": roles.get(p.user_id, "Generalist")} for p in t],
                       "strength": round(strength(t), 2), "goal": max(set(goals), key=goals.count)})
    return {"teams": result, "after": metrics(teams), "before": metrics(baseline), "students": len(students)}
