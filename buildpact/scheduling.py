"""Phase 2: turn a milestone into tasks, assign them, and set internal deadlines.

The scheduler is deliberately simple and explainable:
  * each task goes to the member whose charter role owns it (the charter is the rulebook)
  * a member's weekly hours are spread evenly over the days of the week
  * busy days have zero capacity, so work flows around them (the reason stays private)
  * a task can't start until the tasks it depends on are due
If a task would miss the milestone date, the scheduler proposes moving it to the
member who can finish it soonest; that member's agent must ask before accepting.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from .matching import role_matches
from .models import Profile, Task

WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def is_busy(p: Profile, d: date) -> bool:
    for b in p.busy_days:
        b = b.strip().lower()
        if b[:3] in WEEKDAYS and WEEKDAYS.index(b[:3]) == d.weekday():
            return True
        if b == d.isoformat():
            return True
    return False


def default_tasks(roles: dict[str, str], owner_role: str) -> list[Task]:
    """Fallback milestone-1 breakdown when no LLM is available.

    Every role gets a setup task and a build task; the owner's role also owns a
    final integration task that depends on everyone's build.
    """
    tasks: list[Task] = []
    builds = []
    templates = {
        "ml": [("Collect & clean training data", 3), ("Train and evaluate baseline model", 5)],
        "frontend": [("Wireframes for the main screens", 2), ("Build the chat UI", 5)],
        "backend": [("API skeleton + database", 3), ("Connect the model to the API", 4)],
        "data": [("Set up the data pipeline", 3), ("Feature + quality checks", 3)],
        "design": [("User flows + style guide", 3), ("High-fidelity mockups", 4)],
        "research": [("Related-work survey", 3), ("Evaluation plan", 3)],
        "product": [("User interviews (5 students)", 3), ("Write the MVP spec", 3)],
    }
    for i, role in enumerate(sorted(set(roles.values()))):
        key = next((k for k in templates if role_matches(k, role)), None)
        (t1, h1), (t2, h2) = templates.get(key, [(f"Plan the {role} work", 2), (f"Build {role} v1", 4)])
        a, b = f"t{i}a", f"t{i}b"
        tasks.append(Task(a, t1, role, h1))
        tasks.append(Task(b, t2, role, h2, depends_on=[a]))
        builds.append(b)
    tasks.append(Task("tint", "Integrate everything + demo dry-run", owner_role, 3, depends_on=builds))
    return tasks


@dataclass
class Issue:
    """Something the agents must negotiate (needs a human yes)."""
    task_id: str
    from_user: str
    to_user: str | None
    reason: str          # public wording, never contains private reasons


def _topo(tasks: list[Task]) -> list[Task]:
    by_id = {t.id: t for t in tasks}
    seen, order = set(), []

    def visit(t: Task):
        if t.id in seen:
            return
        seen.add(t.id)
        for d in t.depends_on:
            if d in by_id:
                visit(by_id[d])
        order.append(t)

    for t in tasks:
        visit(t)
    return order


def plan(tasks: list[Task], roles: dict[str, str], profiles: dict[str, Profile],
         weekly_hours: dict[str, float], start: date, deadline: date,
         fixed: dict[str, str] | None = None) -> list[Issue]:
    """Assign + date every task in place. Returns the issues needing negotiation.

    fixed: task_id -> user_id assignments already agreed (they are respected).
    """
    fixed = fixed or {}
    role_to_user = {r: u for u, r in roles.items()}
    # remaining capacity per user per day, in hours
    days = [start + timedelta(n) for n in range((deadline - start).days + 1)]
    cap = {u: {d: (0.0 if is_busy(profiles[u], d) else weekly_hours[u] / 7) for d in days}
           for u in roles}
    due_of: dict[str, date] = {}
    issues: list[Issue] = []

    def simulate(u: str, t: Task, earliest: date, commit: bool):
        """Fill u's daily capacity from `earliest` until t is done.
        Returns (start, due, skipped_busy_day). due is None if it can't fit."""
        need, first = t.est_hours, None
        trial = dict(cap[u])
        for d in days:
            if d < earliest or trial[d] <= 0:
                continue
            take = min(trial[d], need)
            trial[d] -= take
            need -= take
            first = first or d
            if need <= 1e-9:
                if commit:
                    cap[u] = trial
                skipped = any(is_busy(profiles[u], x) for x in days if earliest <= x <= d)
                return first, d, skipped
        return first, None, False

    for t in _topo(tasks):
        earliest = max([start] + [due_of[d] + timedelta(1) for d in t.depends_on if d in due_of])
        earliest = min(earliest, deadline)
        owner = fixed.get(t.id) or t.assignee or role_to_user.get(t.role) or next(iter(roles))
        s, due, skipped = simulate(owner, t, earliest, commit=False)
        if due is None and t.id not in fixed:
            # try everyone else; pick whoever finishes first
            options = []
            for u in roles:
                if u == owner:
                    continue
                s2, d2, _ = simulate(u, t, earliest, commit=False)
                if d2 is not None:
                    options.append((d2, u))
            if options:
                _, alt = min(options)
                issues.append(Issue(t.id, owner, alt,
                                    f"'{t.title}' won't fit in {profiles[owner].name}'s week "
                                    f"before {deadline:%a %b %d}"))
        s, due, skipped = simulate(owner, t, earliest, commit=True)
        if due is None:
            due = deadline
            if not any(i.task_id == t.id for i in issues):
                issues.append(Issue(t.id, owner, None,
                                    f"'{t.title}' is tight for {profiles[owner].name} "
                                    f"— it needs extra hours this week"))
        t.assignee, t.start, t.due = owner, s or earliest, due
        if skipped:
            t.note = f"scheduled around a busy day for {profiles[owner].name} (reason private)"
        due_of[t.id] = due
    return issues


def load(tasks: list[Task]) -> dict[str, float]:
    out: dict[str, float] = {}
    for t in tasks:
        if t.assignee:
            out[t.assignee] = out.get(t.assignee, 0) + t.est_hours
    return out
