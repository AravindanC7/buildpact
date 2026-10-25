"""The negotiation engine: the brain shared by every agent.

Each student has a personal agent. Agents keep their student's facts private and
post only what the protocol allows on the shared table (the Channel). The engine
is a state machine over `Phase`; every public step is a Channel message and every
decision that commits a student goes through a DM with buttons (their yes/no).

It is transport-agnostic: methods return a list of DM / Channel objects, and the
Discord bot or the terminal simulator renders them.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from datetime import date, timedelta

from .matching import Screen, best_assignment, find_conflicts, screen, utility
from .models import (AMBITIONS, DM, Button, Channel, Charter, Idea, Milestone, Out, Phase,
                     Profile, RoleSlot, Task)
from .scheduling import Issue, default_tasks, load, plan

PERKS = ["leads {role} end-to-end", "presents the final demo",
         "first name on the write-up / README credits", "first pick of Milestone 2 tasks"]


@dataclass
class Offer:
    """A trade proposed to settle a role conflict."""
    id: str
    role: str            # the contested role
    winner: str
    loser: str
    loser_role: str
    perks: list[str]
    countered: bool = False


@dataclass
class Move:
    """A proposal to move a task to someone else; needs that person's yes."""
    task_id: str
    from_user: str
    to_user: str
    reason: str


@dataclass
class Engine:
    today: date = field(default_factory=date.today)
    profiles: dict[str, Profile] = field(default_factory=dict)
    phase: Phase = Phase.POOL
    owner_id: str | None = None
    screens: dict[str, Screen] = field(default_factory=dict)
    shortlist: list[str] = field(default_factory=list)
    waitlist: list[str] = field(default_factory=list)
    roles: dict[str, str] = field(default_factory=dict)          # user -> role
    offers: dict[str, Offer] = field(default_factory=dict)
    credit_rules: list[str] = field(default_factory=list)
    charter: Charter | None = None
    tasks: list[Task] = field(default_factory=list)
    fixed: dict[str, str] = field(default_factory=dict)          # task -> user (agreed)
    moves: dict[str, Move] = field(default_factory=dict)
    confirmed: set[str] = field(default_factory=set)
    invite: tuple[str, str] | None = None                          # (user, role) pending join
    announced: set[str] = field(default_factory=set)               # busy-day notes already posted
    _ids: itertools.count = field(default_factory=itertools.count)

    # ------------------------------------------------------------------ helpers
    @property
    def idea(self) -> Idea | None:
        return self.profiles[self.owner_id].idea if self.owner_id else None

    def name(self, uid: str) -> str:
        return self.profiles[uid].name

    def agent(self, uid: str | None, text: str) -> Channel:
        who = f"🤖 **{self.name(uid)}'s agent**" if uid else "🧭 **BuildPact**"
        return Channel(uid, f"{who}: {text}")

    @property
    def team(self) -> list[str]:
        return list(self.charter.roles) if self.charter else list(self.roles)

    # ------------------------------------------------------------ onboarding
    def update_profile(self, uid: str, display_name: str, data: dict) -> list[Out]:
        """Merge what the LLM extracted from a student's DM into their profile."""
        p = self.profiles.setdefault(uid, Profile(uid, display_name))
        was_ready = not p.missing_fields()
        if data.get("name"):
            p.name = data["name"]
        if data.get("skills"):
            p.skills.update({str(k).lower(): max(1, min(5, int(float(v or 3))))
                             for k, v in data["skills"].items()})
        if data.get("wanted_roles"):
            p.wanted_roles = list(data["wanted_roles"])
        amb = {str(a).lower() for a in (data.get("ambition") or [])} & set(AMBITIONS)
        if amb:
            p.ambition = amb
        try:
            if data.get("hours_per_week"):
                p.hours_per_week = float(data["hours_per_week"])
        except (TypeError, ValueError):
            pass
        if data.get("busy_days"):
            p.busy_days = list(data["busy_days"])
            p.busy_reasons.update(data.get("busy_reasons") or {})
        if data.get("idea"):
            p.idea = self._make_idea(uid, data["idea"], p)

        out: list[Out] = [DM(uid, self._profile_card(p))]
        missing = p.missing_fields()
        if missing:
            out.append(DM(uid, "To find you a team I still need: " + "; ".join(missing) + "."))
            return out
        if p.is_owner:
            out.append(DM(uid, "Your idea is saved. Only the one-line teaser is ever posted publicly "
                               "until you approve more.",
                          [Button("🔎 Find my crew", "find_crew", "success")]))
        else:
            out.append(DM(uid, "You're in the pool. I'll tell you when a project fits — "
                               "nothing is committed until you sign."))
        if not was_ready:
            out.append(self.agent(uid, "joined the pool 🙋 (profile kept private)"))
        return out

    def _make_idea(self, uid: str, d: dict, p: Profile) -> Idea:
        roles = [RoleSlot(r["name"], [s.lower() for s in r.get("skills", [])])
                 for r in d.get("roles_needed", []) if r.get("name")]
        if len(roles) < 2:
            roles += [RoleSlot("Product Lead", ["product"]), RoleSlot("Engineer", ["python"])][len(roles):]
        return Idea(owner_id=uid, title=d.get("title", "Untitled"),
                    teaser=d.get("teaser") or d.get("title", ""),
                    details=d.get("details", ""), full_plan=d.get("full_plan", ""),
                    roles_needed=roles[:5],
                    ambition=({str(a).lower() for a in d.get("ambition") or []} & set(AMBITIONS))
                    or set(p.ambition) or {"portfolio"},
                    min_hours=float(d.get("min_hours") or 6), weeks=int(d.get("weeks") or 6))

    def _profile_card(self, p: Profile) -> str:
        skills = ", ".join(f"{k} {v}/5" for k, v in p.skills.items()) or "—"
        lines = [f"📝 **What I know about you** (private to us):",
                 f"• Skills: {skills}",
                 f"• Roles you want: {', '.join(p.wanted_roles) or '—'}",
                 f"• Goal: {', '.join(sorted(p.ambition)) or '—'}",
                 f"• Hours/week: {p.hours_per_week:g}" if p.hours_per_week else "• Hours/week: —"]
        if p.busy_days:
            lines.append(f"• Busy: {', '.join(p.busy_days)} (reasons never shared)")
        if p.idea:
            lines.append(f"• Your idea: **{p.idea.title}** — teaser: “{p.idea.teaser}”")
            lines.append(f"  roles needed: {', '.join(r.name for r in p.idea.roles_needed)}; "
                         f"min {p.idea.min_hours:g} hrs/wk; goal {', '.join(sorted(p.idea.ambition))}")
        return "\n".join(lines)

    # --------------------------------------------------------------- dispatch
    def handle_action(self, uid: str, action: str) -> list[Out]:
        verb, _, arg = action.partition(":")
        fn = getattr(self, f"_a_{verb}", None)
        if fn is None or uid not in self.profiles:
            return [DM(uid, "That button has expired.")] if uid in self.profiles else []
        return fn(uid, arg)

    def handle_text(self, uid: str, text: str) -> list[Out] | None:
        """Keyword commands. Returns None if the text should go to the LLM instead."""
        t = text.strip().lower()
        if t in ("status", "/status"):
            return [DM(uid, self.status_for(uid))]
        if any(k in t for k in ("drop out", "dropping out", "need to drop", "i have to leave the team")):
            return self._a_drop(uid, "")
        if t in ("reset", "/reset"):
            return self.reset()
        return None

    def reset(self) -> list[Out]:
        profiles = self.profiles
        self.__init__(today=self.today)
        self.profiles = profiles
        return [self.agent(None, "🔄 Demo reset. Profiles kept; no team, roles or tasks.")]

    def status_for(self, uid: str) -> str:
        s = f"Phase: **{self.phase.value}**."
        if self.charter and uid in self.charter.roles:
            s += f" Your role: **{self.charter.roles[uid]}**."
        mine = [t for t in self.tasks if t.assignee == uid]
        if mine:
            s += "\n" + self._task_list(mine)
        return s

    # ---------------------------------------------------- stage 1: screening
    def _a_find_crew(self, uid: str, _: str) -> list[Out]:
        p = self.profiles[uid]
        if not p.is_owner:
            return [DM(uid, "Only a student with an idea can start a team.")]
        if self.owner_id and self.owner_id != uid and self.phase != Phase.POOL:
            return [DM(uid, "Another team is forming right now — try again after it's done.")]
        self.owner_id, self.phase = uid, Phase.SCREENING
        idea = p.idea
        out: list[Out] = [self.agent(uid, f"📣 **Stage 1 · teaser**: “{idea.teaser}”\n"
                                          f"Looking for: {', '.join(r.name for r in idea.roles_needed)}. "
                                          f"Goals and weekly hours are checked privately — no one sees anyone's numbers.")]
        passed = []
        for sid, sp in self.profiles.items():
            if sid == uid or sp.missing_fields() or sp.is_owner:
                continue
            sc = screen(sp, idea)
            self.screens[sid] = sc
            if sc.ok:
                passed.append(sid)
                out.append(self.agent(sid, f"✅ compatible · fit {sc.fit:.0%}"))
                out.append(DM(sid, f"A project matched you: “{idea.teaser}” (fit {sc.fit:.0%}). "
                                   "I'm negotiating for you — nothing is committed until you sign."))
            else:
                out.append(self.agent(sid, "⏭️ not a fit — stepping back (reason stays private)"))
                out.append(DM(sid, f"I passed on “{idea.teaser}” for you: {sc.private_reason}. "
                                   "Only you can see this."))
        self.shortlist = sorted(passed, key=lambda s: -self.screens[s].fit)
        if not self.shortlist:
            self.phase = Phase.POOL
            out.append(DM(uid, "No compatible students yet. I'll keep watching the pool."))
            return out
        self.phase = Phase.SHORTLISTED
        fits = ", ".join(f"{self.name(s)} {self.screens[s].fit:.0%}" for s in self.shortlist)
        out.append(DM(uid, f"{len(self.shortlist)} compatible: {fits}.\n"
                           "Reveal **stage 2** (the problem + approach) to them? "
                           "Your full plan stays locked until everyone signs the charter.",
                      [Button("🔓 Reveal stage 2", "reveal", "success"),
                       Button("Not yet", "hold", "secondary")]))
        return out

    def _a_hold(self, uid: str, _: str) -> list[Out]:
        return [DM(uid, "Okay — I'm holding. Tap reveal whenever you're ready.",
                   [Button("🔓 Reveal stage 2", "reveal", "success")])]

    # --------------------------------------------- stage 2: reveal + roles
    def _a_reveal(self, uid: str, _: str) -> list[Out]:
        if uid != self.owner_id or self.phase != Phase.SHORTLISTED:
            return [DM(uid, "Nothing to reveal right now.")]
        idea = self.idea
        names = ", ".join(self.name(s) for s in self.shortlist)
        out: list[Out] = [self.agent(uid, f"🔓 **Stage 2** shared privately with {names}'s agents. "
                                          "Full plan stays locked until the charter is signed.")]
        for s in self.shortlist:
            out.append(DM(s, f"🔓 **{idea.title}** — stage 2 details:\n{idea.details}"))
        return out + self._negotiate_roles()

    def _negotiate_roles(self) -> list[Out]:
        self.phase = Phase.ROLES
        idea = self.idea
        people = [self.profiles[u] for u in [self.owner_id] + self.shortlist]
        self.roles = best_assignment(people, idea.roles_needed, self.owner_id)
        self.waitlist = [s for s in self.shortlist if s not in self.roles]
        out: list[Out] = [self.agent(None, "🧩 Role proposal (max skill fit + preference):\n" +
                                     "\n".join(f"• {self.name(u)} → **{r}**" for u, r in self.roles.items()))]
        for w in self.waitlist:
            out.append(DM(w, "The team has fewer roles than strong matches, so you're on the "
                             "**waitlist** — first in line if a spot opens."))
        by_id = {p.user_id: p for p in people}
        for c in find_conflicts(self.roles, by_id, idea.roles_needed):
            oid = f"o{next(self._ids)}"
            role_slot = next(r for r in idea.roles_needed if r.name == c.role)
            perks = [PERKS[0].format(role=c.loser_role), PERKS[1], PERKS[2]]
            self.offers[oid] = Offer(oid, c.role, c.winner, c.loser, c.loser_role, perks)
            wf, lf = utility(by_id[c.winner], role_slot), utility(by_id[c.loser], role_slot)
            out.append(self.agent(None, f"⚔️ **Conflict**: {self.name(c.winner)}'s and {self.name(c.loser)}'s "
                                        f"agents both want **{c.role}**. Skill fit: "
                                        f"{self.name(c.winner)} {wf:.0%} vs {self.name(c.loser)} {lf:.0%}."))
            out.append(self.agent(c.winner, f"🤝 offer to {self.name(c.loser)}: take **{c.loser_role}** and "
                                             f"get: {'; '.join(perks)}."))
            out.append(DM(c.loser, f"You and {self.name(c.winner)} both wanted **{c.role}**; their skill fit is "
                                   f"higher. Their agent offers you **{c.loser_role}** plus:\n• " +
                          "\n• ".join(perks) + "\nWhat should I do?",
                          [Button("Accept", f"accept:{oid}", "success"),
                           Button("Ask for a swap", f"counter:{oid}", "primary"),
                           Button("Leave this team", f"leave:{oid}", "danger")]))
        if not self.offers:
            out += self._draft_charter()
        return out

    def _a_accept(self, uid: str, oid: str) -> list[Out]:
        o = self.offers.get(oid)
        if not o or uid != o.loser:
            return [DM(uid, "That offer is no longer open.")]
        self.offers.pop(oid)
        self.credit_rules += [f"{self.name(o.loser)} {p}" for p in o.perks[1:]]
        out = [self.agent(uid, f"✅ accepted — {self.name(uid)} takes **{o.loser_role}** with the perks.")]
        return out + (self._draft_charter() if not self.offers else [])

    def _a_counter(self, uid: str, oid: str) -> list[Out]:
        o = self.offers.get(oid)
        if not o or uid != o.loser or o.countered:
            return [DM(uid, "That offer can't be countered now.")]
        o.countered = True
        return [self.agent(uid, f"🔁 counter: my student asks for **{o.role}**. "
                                f"Checking with {self.name(o.winner)}'s agent…"),
                DM(o.winner, f"{self.name(o.loser)} asks to swap: they'd take **{o.role}**, you'd take "
                             f"**{o.loser_role}** plus: {'; '.join(o.perks[1:])}. Your fit for {o.role} "
                             "is higher, so it's your call.",
                   [Button("Swap", f"swapyes:{oid}", "success"),
                    Button(f"Keep {o.role}", f"swapno:{oid}", "secondary")]),
                DM(uid, f"Asked {self.name(o.winner)}'s agent. I'll let you know.")]

    def _a_swapyes(self, uid: str, oid: str) -> list[Out]:
        o = self.offers.get(oid)
        if not o or uid != o.winner:
            return [DM(uid, "That request is no longer open.")]
        self.offers.pop(oid)
        self.roles[o.winner], self.roles[o.loser] = o.loser_role, o.role
        self.credit_rules += [f"{self.name(o.winner)} {p}" for p in o.perks[1:]]
        out = [self.agent(uid, f"🔄 agreed to swap: {self.name(o.loser)} → **{o.role}**, "
                               f"{self.name(o.winner)} → **{o.loser_role}** (+ perks)."),
               DM(o.loser, f"🎉 {self.name(o.winner)} agreed — you're **{o.role}**.")]
        return out + (self._draft_charter() if not self.offers else [])

    def _a_swapno(self, uid: str, oid: str) -> list[Out]:
        o = self.offers.get(oid)
        if not o or uid != o.winner:
            return [DM(uid, "That request is no longer open.")]
        return [self.agent(uid, f"🙅 keeping **{o.role}**; the original offer stands."),
                DM(o.loser, f"{self.name(o.winner)} wants to keep {o.role}. The offer is still: "
                            f"**{o.loser_role}** + {'; '.join(o.perks[1:])}.",
                   [Button("Accept", f"accept:{oid}", "success"),
                    Button("Leave this team", f"leave:{oid}", "danger")])]

    def _a_leave(self, uid: str, oid: str) -> list[Out]:
        o = self.offers.pop(oid, None)
        if not o or uid != o.loser:
            return [DM(uid, "Nothing to leave right now.")]
        role = self.roles.pop(uid)
        out: list[Out] = [self.agent(uid, "🚪 my student is stepping out of this team. No hard feelings.")]
        if self.waitlist:
            nxt = self.waitlist.pop(0)
            self.roles[nxt] = role
            out.append(self.agent(None, f"📨 Next-best match {self.name(nxt)} fills **{role}**."))
            out.append(DM(nxt, f"A spot opened: **{role}** on “{self.idea.teaser}”. Details:\n{self.idea.details}"))
        return out + (self._draft_charter() if not self.offers else [])

    # ------------------------------------------------------------- charter
    def _draft_charter(self) -> list[Out]:
        self.phase = Phase.CHARTER
        idea = self.idea
        w = max(idea.weeks, 3)
        ms = [Milestone("Working prototype", self.today + timedelta(10)),
              Milestone("MVP tested with 10 students", self.today + timedelta(7 * (w // 2 + 1))),
              Milestone("Final demo + write-up", self.today + timedelta(7 * w))]
        owner = self.name(self.owner_id)
        self.charter = Charter(
            title=idea.title, roles=dict(self.roles), weekly_hours=idea.min_hours, milestones=ms,
            decision_rule=f"Each lead decides inside their area; team-wide calls by majority vote; "
                          f"{owner} breaks ties on product scope.",
            credit_rules=["Everyone is listed as a co-creator"] + self.credit_rules,
            dropout_rule="48 hours' notice; tasks get re-planned and the next-best match from the "
                         "waitlist is invited.")
        text = self._charter_text()
        out: list[Out] = [self.agent(None, "📜 **Draft charter** — every member must sign:\n" + text)]
        for u in self.charter.roles:
            out.append(DM(u, f"Here's the charter. Your role: **{self.charter.roles[u]}**, at least "
                             f"**{self.charter.weekly_hours:g} hrs/week** (your real availability stays "
                             f"private).\n\n{text}",
                          [Button("✍️ Sign", "sign", "success"),
                           Button("Decline", "decline", "danger")]))
        return out

    def _charter_text(self) -> str:
        c = self.charter
        return "\n".join([f"**{c.title}**",
                          "Roles: " + "; ".join(f"{self.name(u)} = {r}" for u, r in c.roles.items()),
                          f"Commitment: ≥{c.weekly_hours:g} hrs/week each",
                          "Milestones: " + "; ".join(f"{m.name} ({m.due:%b %d})" for m in c.milestones),
                          f"Decisions: {c.decision_rule}",
                          "Credit: " + "; ".join(c.credit_rules),
                          f"Drop-out: {c.dropout_rule}"])

    def _a_sign(self, uid: str, _: str) -> list[Out]:
        c = self.charter
        if not c or uid not in c.roles or self.phase != Phase.CHARTER:
            return [DM(uid, "There's nothing to sign right now.")]
        c.signatures.add(uid)
        out: list[Out] = [self.agent(uid, f"✍️ signed ({len(c.signatures)}/{len(c.roles)})")]
        if len(c.signatures) < len(c.roles):
            return out
        self.phase = Phase.SIGNED
        out.append(self.agent(None, "🎉 **Charter signed — team formed!** Stage 3 (full plan) "
                                    "is now shared with the team privately."))
        for u in c.roles:
            if u != self.owner_id:
                out.append(DM(u, f"🔐 **Stage 3 — the full plan**:\n{self.idea.full_plan or self.idea.details}"))
        out.append(DM(self.owner_id, "Your team is formed. Ready to plan the first milestone?",
                      [Button("🚀 Kick off Milestone 1", "kickoff", "success")]))
        return out

    def _a_decline(self, uid: str, _: str) -> list[Out]:
        if not self.charter or uid not in self.charter.roles or self.phase != Phase.CHARTER:
            return [DM(uid, "Nothing to decline right now.")]
        role = self.roles.pop(uid)
        out: list[Out] = [self.agent(uid, "✋ my student won't sign — stepping out.")]
        if uid == self.owner_id:
            self.phase = Phase.POOL
            return out + [self.agent(None, "The idea owner withdrew; the team is dissolved.")]
        if self.waitlist:
            nxt = self.waitlist.pop(0)
            self.roles[nxt] = role
            out.append(self.agent(None, f"📨 Next-best match {self.name(nxt)} fills **{role}**."))
            out.append(DM(nxt, f"A spot opened: **{role}** on “{self.idea.teaser}”. Details:\n{self.idea.details}"))
        return out + self._draft_charter()

    # ------------------------------------------------- phase 2: milestone tasks
    def kickoff(self, uid: str, task_dicts: list[dict] | None = None) -> list[Out]:
        if uid != self.owner_id or self.phase not in (Phase.SIGNED, Phase.RUNNING):
            return [DM(uid, "Only the idea owner can kick off, after the charter is signed.")]
        c = self.charter
        m1 = c.milestones[0]
        if task_dicts:
            self.tasks = [Task(str(d.get("id") or f"t{i}"), d["title"], d["role"],
                               float(d.get("est_hours", 3)), [str(x) for x in d.get("depends_on", [])])
                          for i, d in enumerate(task_dicts)]
            # LLMs over-estimate. Keep the milestone to ~70% of the team's real capacity.
            days = max((m1.due - self.today).days, 1)
            budget = 0.7 * sum(self._hours().values()) * days / 7
            total = sum(t.est_hours for t in self.tasks)
            if total > budget:
                for t in self.tasks:
                    t.est_hours = max(1.0, round(t.est_hours * budget / total * 2) / 2)
        else:
            self.tasks = default_tasks(c.roles, c.roles[self.owner_id])
        self.fixed, self.moves, self.confirmed = {}, {}, set()
        self.phase = Phase.TASKS
        out: list[Out] = [self.agent(uid, f"🚀 **Milestone 1 kickoff**: “{m1.name}” due **{m1.due:%a %b %d}**. "
                                          f"Splitting {len(self.tasks)} tasks by the charter's roles, around "
                                          "everyone's real calendars.")]
        return out + self._replan()

    def _a_kickoff(self, uid: str, _: str) -> list[Out]:
        return self.kickoff(uid)

    def _hours(self) -> dict[str, float]:
        # Agents plan with each student's real capacity but never publish it.
        return {u: max(self.profiles[u].hours_per_week, self.charter.weekly_hours) for u in self.charter.roles}

    def _replan(self) -> list[Out]:
        c = self.charter
        start = self.today + timedelta(1)
        for t in self.tasks:
            t.note = ""
            if t.assignee not in c.roles:
                t.assignee = None
        roles_now = dict(c.roles)
        # orphan tasks (their role owner left) go to whoever has the least load
        for t in self.tasks:
            if t.role not in roles_now.values() and t.id not in self.fixed:
                ld = load([x for x in self.tasks if x.assignee])
                self.fixed[t.id] = min(roles_now, key=lambda u: ld.get(u, 0) / self._hours()[u])
        issues = plan(self.tasks, roles_now, self.profiles, self._hours(), start, c.milestones[0].due, self.fixed)
        out: list[Out] = []
        for t in self.tasks:
            if t.note and (t.id, t.assignee) not in self.announced:
                self.announced.add((t.id, t.assignee))
                out.append(self.agent(t.assignee, f"📅 “{t.title}” — my student has a commitment that day "
                                                  "(details private), so I planned around it: "
                                                  f"due {t.due:%a %b %d}."))
        for i in issues:
            out += self._propose_move(i)
        if not self.moves:
            out += self._ask_confirmations()
        return out

    def _propose_move(self, i: Issue) -> list[Out]:
        t = next(x for x in self.tasks if x.id == i.task_id)
        if i.to_user is None:
            self.fixed[t.id] = i.from_user
            if ("tight", t.id, i.from_user) in self.announced:
                return []
            self.announced.add(("tight", t.id, i.from_user))
            return [self.agent(i.from_user, f"⚠️ {i.reason}; flagging it as tight.")]
        self.moves[t.id] = Move(t.id, i.from_user, i.to_user, i.reason)
        return [self.agent(i.from_user, f"⚠️ {i.reason}. Asking {self.name(i.to_user)}'s agent to take it."),
                DM(i.to_user, f"Can you take “{t.title}” (~{t.est_hours:g}h) this milestone? It's outside "
                              f"your role, so I won't accept without your OK.",
                   [Button("Take it", f"moveyes:{t.id}", "success"),
                    Button("Can't", f"moveno:{t.id}", "secondary")])]

    def _a_moveyes(self, uid: str, tid: str) -> list[Out]:
        m = self.moves.get(tid)
        if not m or uid != m.to_user:
            return [DM(uid, "That request is no longer open.")]
        self.moves.pop(tid)
        self.fixed[tid] = uid
        title = next(t.title for t in self.tasks if t.id == tid)
        return [self.agent(uid, f"🙌 takes “{title}”.")] + (self._replan() if not self.moves else [])

    def _a_moveno(self, uid: str, tid: str) -> list[Out]:
        m = self.moves.get(tid)
        if not m or uid != m.to_user:
            return [DM(uid, "That request is no longer open.")]
        self.moves.pop(tid)
        self.fixed[tid] = m.from_user
        title = next(t.title for t in self.tasks if t.id == tid)
        return ([self.agent(uid, f"can't take “{title}” — it stays with {self.name(m.from_user)}, "
                                 "flagged as tight.")] + (self._replan() if not self.moves else []))

    def _task_list(self, tasks: list[Task]) -> str:
        return "\n".join(f"• {t.title} (~{t.est_hours:g}h) — due **{t.due:%a %b %d}**"
                         + (" ⏳ starts after an earlier task" if t.depends_on else "") for t in tasks)

    def _ask_confirmations(self) -> list[Out]:
        self.confirmed = set()
        out: list[Out] = [self.agent(None, "🗓️ Task split proposed. Each agent is checking with its student.")]
        for u in self.charter.roles:
            mine = sorted([t for t in self.tasks if t.assignee == u], key=lambda t: t.due)
            text = (f"Your Milestone 1 tasks as **{self.charter.roles[u]}**:\n" + self._task_list(mine)
                    if mine else "You have no Milestone 1 tasks — you're on review duty.")
            out.append(DM(u, text, [Button("✅ Confirm", "confirm", "success"),
                                     Button("Too much this week", "toomuch", "secondary")]))
        return out

    def _a_confirm(self, uid: str, _: str) -> list[Out]:
        if self.phase != Phase.TASKS or uid not in self.charter.roles:
            return [DM(uid, "Nothing to confirm right now.")]
        self.confirmed.add(uid)
        n = len(self.charter.roles)
        out: list[Out] = [self.agent(uid, f"✅ confirmed ({len(self.confirmed)}/{n})")]
        if len(self.confirmed) == n:
            self.phase = Phase.RUNNING
            out.append(self.agent(None, "🏁 **Milestone 1 plan locked:**\n" + self.plan_table()))
        return out

    def plan_table(self) -> str:
        rows = sorted(self.tasks, key=lambda t: (t.due, t.title))
        return "\n".join(f"`{t.due:%a %b %d}` · {t.title} → **{self.name(t.assignee)}**" for t in rows)

    def _a_toomuch(self, uid: str, _: str) -> list[Out]:
        if self.phase != Phase.TASKS or uid not in self.charter.roles:
            return [DM(uid, "Nothing to change right now.")]
        mine = [t for t in self.tasks if t.assignee == uid and not t.depends_on] or \
               [t for t in self.tasks if t.assignee == uid]
        if not mine:
            return [DM(uid, "You don't have any tasks to hand off.")]
        t = max(mine, key=lambda t: t.est_hours)
        ld, hrs = load(self.tasks), self._hours()
        others = [u for u in self.charter.roles if u != uid]
        if not others:
            return [DM(uid, "No teammate to hand it to.")]
        to = min(others, key=lambda u: ld.get(u, 0) / hrs[u])
        self.confirmed.discard(uid)
        return self._propose_move(Issue(t.id, uid, to, f"my student is overloaded this week (details private) — "
                                                       f"looking to hand off “{t.title}”"))

    # ------------------------------------------------------------- drop-out
    def _a_drop(self, uid: str, _: str) -> list[Out]:
        c = self.charter
        if not c or uid not in c.roles or self.phase in (Phase.CHARTER,):
            return [DM(uid, "You're not on a signed team right now.")]
        if uid == self.owner_id:
            return [DM(uid, "You own the idea — hand it off before dropping (not supported in the demo).")]
        role = c.roles.pop(uid)
        self.roles.pop(uid, None)
        for t in self.tasks:
            if t.assignee == uid:
                t.assignee = None
            if self.fixed.get(t.id) == uid:
                self.fixed.pop(t.id)
        out: list[Out] = [self.agent(uid, "🚨 my student has to drop out (details private). "
                                          "Applying the charter's drop-out rule."),
                          DM(uid, "Done — I told the team without sharing why. Take care of yourself.")]
        if self.waitlist:
            nxt = self.waitlist.pop(0)
            self.invite = (nxt, role)
            out.append(self.agent(None, f"📨 Inviting next-best match **{self.name(nxt)}** for **{role}**."))
            out.append(DM(nxt, f"A spot opened on “{self.idea.title}”: **{role}**, ≥{c.weekly_hours:g} hrs/week.\n"
                               f"{self.idea.details}\nJoin and sign the charter?",
                          [Button("Join + sign", "join", "success"), Button("No thanks", "nojoin", "secondary")]))
            return out
        return out + self._after_roster_change(role)

    def _after_roster_change(self, role: str) -> list[Out]:
        out: list[Out] = []
        if self.tasks:
            out.append(self.agent(None, f"No one on the waitlist — re-planning **{role}** tasks across the team."))
            self.phase = Phase.TASKS
            out += self._replan()
        else:
            out.append(self.agent(None, f"No one on the waitlist — **{role}** is open; the team covers it for now."))
        return out

    def _a_join(self, uid: str, _: str) -> list[Out]:
        if not self.invite or self.invite[0] != uid:
            return [DM(uid, "That invite has expired.")]
        _, role = self.invite
        self.invite = None
        self.charter.roles[uid] = role
        self.roles[uid] = role
        self.charter.signatures.add(uid)
        out: list[Out] = [self.agent(uid, f"✍️ joins as **{role}** and signs the charter."),
                          DM(uid, f"🔐 Welcome! Full plan:\n{self.idea.full_plan or self.idea.details}")]
        if self.tasks:
            for t in self.tasks:
                if t.assignee is None and t.role == role:
                    self.fixed[t.id] = uid
            self.phase = Phase.TASKS
            out += self._replan()
        return out

    def _a_nojoin(self, uid: str, _: str) -> list[Out]:
        if not self.invite or self.invite[0] != uid:
            return [DM(uid, "That invite has expired.")]
        _, role = self.invite
        self.invite = None
        return [self.agent(uid, "passes on joining.")] + self._after_roster_change(role)
