"""Multi-issue negotiation between two agents over a contested role.

How a conflict is resolved, rung by rung:

  1. Direct trade   Agents exchange *packages* (who gets the role + which perks the
                    other side receives as compensation). Each agent scores packages
                    with its own student's PRIVATE valuation and only proposes or
                    accepts packages above its current aspiration, which it lowers a
                    little each round (concession).
  2. Mediator       If rounds run out (or a student asks), a neutral mediator picks the
                    Pareto-optimal package that maximises the WORSE-OFF side's satisfaction
                    (egalitarian rule; ties broken by the Nash product). It reads both
                    private valuations but publishes only percentages.
  3. Students       Nothing is final until the students say yes.

What crosses between agents: packages, plus priorities a student CHOSE to share
("my student cares most about presenting"). Raw scores never leave an agent.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field

from .matching import preference, skill_fit
from .models import Profile, RoleSlot

PERKS = {
    "present": "presents the final demo",
    "credit": "first name on the write-up / README credits",
    "pick": "first pick of Milestone 2 tasks",
}
DEFAULT_VALUE = {"present": 2.0, "credit": 2.0, "pick": 1.5}   # value of receiving it
DEFAULT_COST = {"present": 1.0, "credit": 1.5, "pick": 1.0}    # cost of the other side getting it
LEVEL = {"high": 5.0, "medium": 2.5, "low": 0.5, "none": 0.0}
ASPIRATION = [1.0, 0.75, 0.55]    # an agent's minimum satisfaction on its 1st, 2nd, 3rd proposal
MAX_ROUNDS = 4                   # total proposals before the mediator steps in


@dataclass(frozen=True)
class Package:
    holder: str                  # user_id who gets the contested role
    perks: frozenset             # perks given to the OTHER side as compensation

    def describe(self, names: dict[str, str], role: str, alt: str, other: str) -> str:
        p = ", ".join(PERKS[k] for k in sorted(self.perks)) or "no extras"
        return f"{names[self.holder]} → **{role}**, {names[other]} → **{alt}** + {p}"


@dataclass
class Valuation:
    """One student's private scores. Never shown to anyone else."""
    role_value: dict[str, float]
    perk_value: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_VALUE))
    perk_cost: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_COST))
    shared_priorities: dict[str, str] = field(default_factory=dict)  # what they CHOSE to share

    @classmethod
    def for_student(cls, p: Profile, role: RoleSlot, alt: RoleSlot) -> "Valuation":
        def v(r: RoleSlot) -> float:
            return 10 * preference(p, r) + 6 * skill_fit(p, r) + 1
        return cls({role.name: v(role), alt.name: v(alt)})

    def update(self, priorities: dict[str, str], cost_priorities: dict[str, str] | None = None) -> list[str]:
        """Apply what the student said. Returns the priorities they chose to share."""
        shared = []
        for k, lvl in (priorities or {}).items():
            if k in PERKS and lvl in LEVEL:
                self.perk_value[k] = LEVEL[lvl]
                self.shared_priorities[k] = lvl
                if lvl in ("high", "medium"):
                    shared.append(k)
        for k, lvl in (cost_priorities or {}).items():
            if k in PERKS and lvl in LEVEL:
                self.perk_cost[k] = LEVEL[lvl]
        return shared


@dataclass
class Conflict:
    id: str
    role: str                    # contested role
    alt: str                     # the other role in play
    a: str                       # current holder (higher fit) — side A
    b: str                       # challenger — side B
    vals: dict[str, Valuation]
    history: list[dict] = field(default_factory=list)   # public timeline entries
    proposals: dict[str, int] = field(default_factory=dict)  # proposals made per side
    pending: tuple[Package, str] | None = None          # (package, proposer)
    stage: str = "trade"         # trade | mediator | done | failed
    final: Package | None = None
    yes: set[str] = field(default_factory=set)
    sim_turns: dict[str, int] = field(default_factory=dict)
    proposed: dict[str, set] = field(default_factory=dict)   # packages each side already offered

    # ---------------------------------------------------------------- scoring
    def other(self, uid: str) -> str:
        return self.b if uid == self.a else self.a

    def packages(self) -> list[Package]:
        out = []
        for holder in (self.a, self.b):
            for n in range(len(PERKS) + 1):
                for combo in itertools.combinations(sorted(PERKS), n):
                    out.append(Package(holder, frozenset(combo)))
        return out

    def utility(self, uid: str, pkg: Package) -> float:
        v = self.vals[uid]
        role = self.role if pkg.holder == uid else self.alt
        u = v.role_value[role]
        if pkg.holder == uid:          # I keep the role, the other side gets the perks
            u -= sum(v.perk_cost[k] for k in pkg.perks)
        else:                          # I get the perks
            u += sum(v.perk_value[k] for k in pkg.perks)
        return u

    def satisfaction(self, uid: str, pkg: Package) -> float:
        us = [self.utility(uid, p) for p in self.packages()]
        lo, hi = min(us), max(us)
        return 1.0 if hi == lo else (self.utility(uid, pkg) - lo) / (hi - lo)

    def _estimated_other(self, uid: str, pkg: Package) -> float:
        """How good this package looks for the other side, using ONLY what they shared
        publicly (their stated priorities) plus defaults. No private numbers."""
        o = self.other(uid)
        shared = self.vals[o].shared_priorities
        value = {k: LEVEL.get(shared.get(k, ""), DEFAULT_VALUE[k]) for k in PERKS}
        base = 10.0 if pkg.holder == o else 4.0
        return base + (sum(value[k] for k in pkg.perks) if pkg.holder != o else 0)

    # --------------------------------------------------------------- protocol
    def aspiration(self, uid: str) -> float:
        n = self.proposals.get(uid, 0)
        return ASPIRATION[min(n, len(ASPIRATION) - 1)]

    def propose(self, uid: str) -> Package:
        """uid's agent picks, among packages it can live with, the one most likely
        to be accepted by the other side."""
        asp = self.aspiration(uid)
        ok = [p for p in self.packages() if self.satisfaction(uid, p) >= asp - 1e-9]
        fresh = [p for p in ok if p not in self.proposed.get(uid, set())]
        ok = fresh or ok
        best = max(ok, key=lambda p: (self._estimated_other(uid, p), self.satisfaction(uid, p)))
        self.proposals[uid] = self.proposals.get(uid, 0) + 1
        self.proposed.setdefault(uid, set()).add(best)
        self.pending = (best, uid)
        return best

    def acceptable(self, uid: str, pkg: Package) -> bool:
        """Would uid's agent recommend this? (at or above its next aspiration)"""
        return self.satisfaction(uid, pkg) >= self.aspiration(uid) - 1e-9

    def total_proposals(self) -> int:
        return sum(self.proposals.values())

    def mediate(self) -> Package:
        """Egalitarian among Pareto-optimal packages: lift the worse-off side; tie-break on product."""
        self.stage = "mediator"
        frontier = [p for p in self.packages() if self.pareto_optimal(p)]
        best = max(frontier, key=lambda p: (
            round(min(self.satisfaction(self.a, p), self.satisfaction(self.b, p)), 6),
            self.satisfaction(self.a, p) * self.satisfaction(self.b, p)))
        self.pending = (best, "mediator")
        return best

    def pareto_optimal(self, pkg: Package) -> bool:
        sa, sb = self.satisfaction(self.a, pkg), self.satisfaction(self.b, pkg)
        for p in self.packages():
            pa, pb = self.satisfaction(self.a, p), self.satisfaction(self.b, p)
            if pa >= sa - 1e-9 and pb >= sb - 1e-9 and (pa > sa + 1e-9 or pb > sb + 1e-9):
                return False
        return True

    def fairness(self, pkg: Package) -> dict:
        sa, sb = self.satisfaction(self.a, pkg), self.satisfaction(self.b, pkg)
        best_min = max(min(self.satisfaction(self.a, p), self.satisfaction(self.b, p))
                       for p in self.packages())
        return {"a": sa, "b": sb, "pareto": self.pareto_optimal(pkg),
                "worst": min(sa, sb), "worst_max": best_min}

    def log(self, kind: str, actor: str | None, text: str, pkg: Package | None = None):
        entry = {"kind": kind, "actor": actor, "text": text}
        if pkg is not None:
            entry["sat"] = {self.a: round(self.satisfaction(self.a, pkg), 2),
                            self.b: round(self.satisfaction(self.b, pkg), 2)}
        self.history.append(entry)
