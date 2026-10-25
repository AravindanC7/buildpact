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

from .matching import Screen, best_assignment, find_conflicts, norm_skill, role_matches, screen, utility
from .negotiation import MAX_ROUNDS, PERKS as PERK_TEXT, Conflict, Package, Valuation
from .models import (AMBITIONS, DM, Button, Channel, Charter, Idea, Milestone, Out, Phase,
                     Profile, Room, RoleSlot, Task)
from .scheduling import Issue, default_tasks, is_busy, load, plan

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
    conflicts: dict[str, Conflict] = field(default_factory=dict)
    expecting: dict[str, tuple[str, str]] = field(default_factory=dict)   # uid -> (kind, id) awaiting free text
    public_log: list[dict] = field(default_factory=list)                  # everything posted on the table
    listings: dict[str, dict] = field(default_factory=dict)               # project board
    matches: dict[str, dict] = field(default_factory=dict)                # "lid|uid" -> double opt-in state
    project_room: bool = False
    handshakes: dict[str, bool] = field(default_factory=dict)             # "lid|uid" -> compatible?
    meeting: dict = field(default_factory=dict)                           # kickoff meeting state
    notes: dict = field(default_factory=dict)                             # mediator's meeting notes
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

    def agent(self, uid: str | None, text: str, room: str | None = "auto") -> Channel:
        who = f"🤖 **{self.name(uid)}'s agent**" if uid else "🧭 **BuildPact**"
        if room == "auto":
            room = "project" if self.project_room and self.phase in (
                Phase.KICKOFF, Phase.ROLES, Phase.CHARTER, Phase.SIGNED, Phase.TASKS, Phase.RUNNING) else None
        self.public_log.append({"agent": self.name(uid) if uid else "BuildPact", "text": text, "room": room})
        return Channel(uid, f"{who}: {text}", room)

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
        if data.get("idea") and (data["idea"].get("title") or not p.idea):
            p.idea = self._make_idea(uid, data["idea"], p)
        req = data.get("requirements") or {}
        if p.idea and req:
            if req.get("must_have"):
                p.idea.must_have = [str(x).lower() for x in req["must_have"]]
            if req.get("screening_question"):
                p.idea.question = str(req["screening_question"])

        out: list[Out] = [DM(uid, self._profile_card(p))]
        missing = p.missing_fields()
        if missing:
            out.append(DM(uid, "To find you a team I still need: " + "; ".join(missing) + "."))
            return out
        if p.is_owner:
            out.append(DM(uid, "Your idea is saved. I'll post only a one-line listing on the project board, "
                               "then scout for people. Nobody joins unless **both** of you opt in.",
                          [Button("📌 Post to the board & find my crew", "find_crew", "success")]))
        elif uid.startswith("sim:"):
            pass
        else:
            out.append(DM(uid, "You're in the pool. Want me to browse the project board for you? "
                               "Owners' agents may also reach out — nothing happens without your yes.",
                          [Button("🔎 Browse projects", "browse", "primary")]))
        if not was_ready:
            out.append(self.agent(uid, "joined the pool 🙋 (profile kept private)"))
        return out

    def _make_idea(self, uid: str, d: dict, p: Profile) -> Idea:
        def clean(n: str) -> str:
            n = str(n).strip()
            for suffix in (" person", " people", " developer", " dev", " engineer role", " role"):
                if n.lower().endswith(suffix) and len(n) > len(suffix) + 2:
                    n = n[: -len(suffix)].strip()
            return n
        roles = [RoleSlot(clean(r["name"]), [s.lower() for s in r.get("skills", [])])
                 for r in d.get("roles_needed", []) if r.get("name")]
        if len(roles) < 2:
            roles += [RoleSlot("Product Lead", ["product"]), RoleSlot("Engineer", ["python"])][len(roles):]
        return Idea(owner_id=uid, title=d.get("title", "Untitled"),
                    teaser=d.get("teaser") or d.get("title", ""),
                    details=d.get("details", ""), full_plan=d.get("full_plan", ""),
                    roles_needed=roles[:5],
                    ambition=({str(a).lower() for a in d.get("ambition") or []} & set(AMBITIONS))
                    or set(p.ambition) or {"portfolio"},
                    min_hours=float(d.get("min_hours") or 6), weeks=int(d.get("weeks") or 6),
                    must_have=[str(x).lower() for x in d.get("must_have") or []],
                    question=str(d.get("screening_question") or ""))

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
            if p.idea.must_have:
                lines.append(f"  must-haves: {', '.join(p.idea.must_have)}")
            lines.append(f"  screening question: {p.idea.question}" if p.idea.question else
                         "  (tip: tell me a must-have skill or a screening question, e.g. “ask for a GitHub link”)")
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
        if self.phase == Phase.RUNNING and ("pr" in t.split() or "pull request" in t or "ready for review" in t):
            return self.pr_ready(uid, text)
        return None

    def awaiting(self, uid: str) -> tuple[str, str] | None:
        """If this student's agent is waiting for a free-text answer: (kind, id)."""
        return self.expecting.get(uid)

    def reset(self) -> list[Out]:
        profiles = self.profiles
        keep = {lid: L for lid, L in self.listings.items() if L["owner"].startswith("sim:")}
        self.__init__(today=self.today)
        self.profiles = profiles
        self.listings = keep
        for attr in ("progress", "private_log"):
            if hasattr(self, attr):
                delattr(self, attr)
        return [self.agent(None, "🔄 Demo reset. Profiles kept; no team, roles or tasks.")]

    def status_for(self, uid: str) -> str:
        s = f"Phase: **{self.phase.value}**."
        if self.charter and uid in self.charter.roles:
            s += f" Your role: **{self.charter.roles[uid]}**."
        mine = [t for t in self.tasks if t.assignee == uid]
        if mine:
            s += "\n" + self._task_list(mine)
        return s

    # ------------------------------------------------- the project board
    def _owner_role(self, idea: Idea) -> str:
        p = self.profiles[idea.owner_id]
        for w in p.wanted_roles:
            for r in idea.roles_needed:
                if role_matches(w, r.name):
                    return r.name
        return idea.roles_needed[0].name

    def _open_roles(self, lid: str) -> list[str]:
        L = self.listings[lid]
        taken = {self._owner_role(L["idea"])} | {m["role"] for m in self.matches.values()
                                                  if m["lid"] == lid and m["state"] == "matched"}
        return [r.name for r in L["idea"].roles_needed if r.name not in taken]

    def _card(self, lid: str) -> Channel:
        L = self.listings[lid]
        idea = L["idea"]
        open_roles = self._open_roles(lid)
        n = sum(1 for m in self.matches.values() if m["lid"] == lid and m["state"] == "matched")
        status = "🟢 open" if open_roles else "✅ team full"
        text = (f"📌 **{idea.title}** — “{idea.teaser}”\n"
                f"Looking for: {' · '.join(open_roles) or '—'}\n"
                f"Vibe: {' / '.join(sorted(idea.ambition))} · ≥{idea.min_hours:g} h/week · {idea.weeks} weeks · "
                f"posted by {self.name(idea.owner_id)}'s agent · {status} ({n} matched)")
        self.public_log.append({"agent": "Board", "text": text, "room": "board"})
        return Channel(idea.owner_id, text, "board")

    def post_listing(self, uid: str) -> tuple[str, list[Out]]:
        p = self.profiles[uid]
        lid = f"L{uid}"
        if lid not in self.listings:
            self.listings[lid] = {"id": lid, "owner": uid, "idea": p.idea}
        return lid, [self._card(lid)]

    def seed_owner(self, uid: str, name: str, data: dict) -> list[Out]:
        """A practice idea owner whose project sits on the board (no team pipeline)."""
        self.update_profile(uid, name, data)
        lid, out = self.post_listing(uid)
        return out

    def _available(self, sid: str) -> bool:
        p = self.profiles[sid]
        return (not p.missing_fields() and not p.is_owner and
                not any(m["uid"] == sid and m["state"] == "matched" for m in self.matches.values()))

    def _handshake(self, lid: str, sid: str) -> tuple[Screen, str, Channel]:
        idea = self.listings[lid]["idea"]
        p = self.profiles[sid]
        sc = screen(p, idea)
        levels = {norm_skill(k): v for k, v in p.skills.items()}
        missing = [m for m in idea.must_have if levels.get(norm_skill(m), 0) < 3]
        if sc.ok and missing:
            sc = Screen(False, sc.fit, f"they require {', '.join(missing)}")
        self.handshakes[self._mkey(lid, sid)] = sc.ok
        open_roles = [r for r in idea.roles_needed if r.name in self._open_roles(lid)] or idea.roles_needed
        role = max(open_roles, key=lambda r: utility(p, r))
        if sc.ok:
            fit = utility(p, role)
            must = f" · must-haves ({', '.join(idea.must_have)})? ✅" if idea.must_have else ""
            text = (f"🤝 **Handshake** · {idea.title} ↔ {self.name(sid)}'s agent\n"
                    f"goals compatible? ✅ · hours band OK? ✅{must} · open role fits? ✅ {role.name}\n"
                    f"→ **compatible · {max(fit, sc.fit):.0%}** (each side answered from its own private profile)")
        else:
            text = (f"🤝 **Handshake** · {idea.title} ↔ {self.name(sid)}'s agent\n"
                    f"{4 if idea.must_have else 3} checks answered privately → ⏭️ **not compatible** "
                    "(which check failed stays private)")
        return sc, role.name, self.agent(idea.owner_id, text)

    def _mkey(self, lid: str, sid: str) -> str:
        return f"{lid}|{sid}"

    def _a_find_crew(self, uid: str, _: str) -> list[Out]:
        p = self.profiles[uid]
        if not p.is_owner:
            return [DM(uid, "Only a student with an idea can post a project.")]
        if self.owner_id and self.phase != Phase.POOL:
            if self.owner_id == uid:
                return [DM(uid, f"Your project is already past this step (phase: {self.phase.value}). "
                                "Type `!reset` in #buildpact to start over.")]
            return [DM(uid, "Another team is forming right now — try again after it's done.")]
        self.owner_id, self.phase = uid, Phase.SCREENING
        lid, out = self.post_listing(uid)
        idea = p.idea
        out.append(self.agent(uid, f"📌 posted “{idea.teaser}” to the project board. Scouting the pool — "
                                   "each check is a handshake between two agents; numbers never cross."))
        found = 0
        for sid in list(self.profiles):
            if sid == uid or not self._available(sid) or self._mkey(lid, sid) in self.matches:
                continue
            sc, role, msg = self._handshake(lid, sid)
            self.screens[sid] = sc
            out.append(msg)
            if sc.ok:
                found += 1
                self.matches[self._mkey(lid, sid)] = {"lid": lid, "uid": sid, "role": role, "fit": sc.fit,
                                                      "owner": None, "seeker": None, "state": "invited"}
                out.append(DM(sid, f"📬 {p.name}'s agent reached out: you'd fit **{role}** on “{idea.teaser}” "
                                   f"(fit {sc.fit:.0%}). Your goals and hours were checked privately.\n"
                                   "Interested? If you **both** say yes, you'll see the project details. "
                                   "If either side passes, the other only hears “not a match this time”.",
                              [Button("🙋 Interested", f"interested:{lid}", "success"),
                               Button("Not for me", f"notforme:{lid}", "secondary")],
                              meta={"kind": "invite", "lid": lid}))
            else:
                out.append(DM(sid, f"I checked “{idea.teaser}” for you and passed: {sc.private_reason}. "
                                   "Only you can see this. Want me to find a project that fits better?",
                              [Button("🔎 Find me a better fit", "browse", "primary")]))
        if not found:
            self.phase = Phase.POOL
            out.append(DM(uid, "No compatible students in the pool yet. Your listing stays on the board."))
            return out
        self.phase = Phase.SHORTLISTED
        out.append(DM(uid, f"🤝 {found} compatible student(s). I've asked each one privately whether they're "
                           "interested. I'll bring you everyone who opts in — you decide who to match with."))
        return out

    def _a_interested(self, sid: str, lid: str) -> list[Out]:
        m = self.matches.get(self._mkey(lid, sid))
        if not m or m["seeker"] is not None:
            return [DM(sid, "That invite is no longer open.")]
        m["seeker"] = True
        m["state"] = "seeker_yes"
        out: list[Out] = [self.agent(sid, f"🙋 opted in to “{self.listings[lid]['idea'].title}”.")]
        return out + self._screen_or_review(lid, sid)

    def _screen_or_review(self, lid: str, sid: str) -> list[Out]:
        idea = self.listings[lid]["idea"]
        if not idea.question:
            return self._review(lid, sid)
        m = self.matches[self._mkey(lid, sid)]
        m["state"] = "screening"
        self.expecting[sid] = ("screening", lid)
        return [self.agent(sid, "📝 answering the owner's screening question — privately, only the owner sees it."),
                DM(sid, f"📝 {self.name(idea.owner_id)}'s screening question for **{idea.title}**:\n> {idea.question}\n"
                        "Reply here in your own words. Only the project owner will see your answer.",
                   meta={"kind": "screening", "question": idea.question})]

    def screening_answer(self, sid: str, text: str) -> list[Out]:
        kind, lid = self.expecting.get(sid, ("", ""))
        m = self.matches.get(self._mkey(lid, sid))
        if kind != "screening" or not m:
            return [DM(sid, "Noted.")]
        self.expecting.pop(sid, None)
        m["answer"], m["state"] = text.strip()[:400], "seeker_yes"
        return [DM(sid, "Thanks — sent to the owner, only they can see it.")] + self._review(lid, sid)

    def _review(self, lid: str, sid: str) -> list[Out]:
        m = self.matches[self._mkey(lid, sid)]
        owner = self.listings[lid]["owner"]
        answer = f"\n📝 Their screening answer (only you see this): “{m['answer']}”" if m.get("answer") else ""
        return [DM(owner, f"🙋 **{self.name(sid)}** opted in — fit {m['fit']:.0%}, would take **{m['role']}**.{answer}\n"
                          "Accept them to the team? (If you pass, they'll only hear “not a match this time”.)",
                   [Button("✅ Accept to team", f"match:{lid}~{sid}", "success"),
                    Button("Pass", f"pass:{lid}~{sid}", "secondary")],
                   meta={"kind": "review", "lid": lid})]

    def _a_notforme(self, sid: str, lid: str) -> list[Out]:
        m = self.matches.get(self._mkey(lid, sid))
        if not m or m["seeker"] is not None:
            return [DM(sid, "That invite is no longer open.")]
        m["seeker"], m["state"] = False, "seeker_passed"
        self.expecting.pop(sid, None)
        owner = self.listings[lid]["owner"]
        return ([self.agent(sid, "🙅 passing on this one — no reason needed."),
                 DM(owner, "One invited student passed — not a match this time."),
                 DM(sid, "No problem, I let them know without a reason. Want me to look for other projects?",
                    [Button("🔎 Browse projects", "browse", "primary")])]
                + self._maybe_start(lid))

    def _a_match(self, uid: str, arg: str) -> list[Out]:
        lid, _, sid = arg.partition("~")
        m = self.matches.get(self._mkey(lid, sid))
        if not m or self.listings[lid]["owner"] != uid or m["owner"] is not None:
            return [DM(uid, "That request is no longer open.")]
        m["owner"] = True
        idea = self.listings[lid]["idea"]
        if not m["seeker"]:
            return [DM(uid, "Noted — waiting for them to opt in.")]
        m["state"] = "matched"
        out: list[Out] = [self.agent(None, f"🤝 **Mutual match** · {idea.title} ↔ {self.name(sid)} — both sides opted in."),
                          DM(sid, f"🤝 You're accepted to **{idea.title}** — both sides opted in. Stage 2, the problem + approach:\n"
                                  f"{idea.details}"),
                          self._card(lid)]
        if lid == f"L{self.owner_id}" and self.phase not in (Phase.SCREENING, Phase.SHORTLISTED, Phase.POOL):
            self.waitlist.append(sid)
            out.append(DM(sid, "The team is already formed — you're first on the waitlist."))
        elif lid == f"L{self.owner_id}":
            n = sum(1 for x in self.matches.values() if x["lid"] == lid and x["state"] == "matched")
            waiting = sum(1 for x in self.matches.values()
                          if x["lid"] == lid and x["state"] in ("invited", "seeker_yes", "screening"))
            if waiting:
                out.append(DM(uid, f"🤝 Accepted {self.name(sid)} ({n} so far). {waiting} more still deciding — "
                                   "I'll offer to close the search once everyone has answered."))
        return out + self._maybe_start(lid)

    def _a_pass(self, uid: str, arg: str) -> list[Out]:
        lid, _, sid = arg.partition("~")
        m = self.matches.get(self._mkey(lid, sid))
        if not m or self.listings[lid]["owner"] != uid or m["owner"] is not None:
            return [DM(uid, "That request is no longer open.")]
        m["owner"], m["state"] = False, "owner_passed"
        return ([self.agent(uid, "passed on one candidate (privately)."),
                 DM(sid, f"“{self.listings[lid]['idea'].teaser}” — not a match this time. No reason is shared, "
                         "either way. Want me to keep looking?",
                    [Button("🔎 Browse projects", "browse", "primary")])]
                + self._maybe_start(lid))

    def _maybe_start(self, lid: str) -> list[Out]:
        """When every invite to the active project is decided, nudge the owner to close the search."""
        if lid != f"L{self.owner_id}" or self.phase != Phase.SHORTLISTED:
            return []
        ms = [m for m in self.matches.values() if m["lid"] == lid]
        if any(m["state"] in ("invited", "seeker_yes", "screening") for m in ms):
            return []
        matched = [m for m in ms if m["state"] == "matched"]
        if not matched:
            self.phase = Phase.POOL
            return [DM(self.owner_id, "Nobody matched this round. Your listing stays on the board.")]
        return [DM(self.owner_id, f"Everyone has answered: **{len(matched)}** mutual match(es). "
                                  "Close the search and start the team?",
                   [Button(f"🔒 Close search & start team ({len(matched) + 1} people)", "close", "success"),
                    Button("Keep searching", "keepopen", "secondary")], meta={"kind": "close"})]

    def _a_keepopen(self, uid: str, _: str) -> list[Out]:
        return [DM(uid, "Okay — your listing stays open. Tap close whenever you're ready.",
                   [Button("🔒 Close search & start team", "close", "success")])]

    def _a_close(self, uid: str, _: str) -> list[Out]:
        lid = f"L{uid}"
        if uid != self.owner_id or self.phase != Phase.SHORTLISTED:
            return [DM(uid, "There's no open search to close.")]
        ms = [m for m in self.matches.values() if m["lid"] == lid]
        matched = sorted([m for m in ms if m["state"] == "matched"], key=lambda m: -m["fit"])
        if not matched:
            return [DM(uid, "Match with at least one person first.")]
        out: list[Out] = []
        for m in ms:
            if m["state"] in ("invited", "seeker_yes", "screening"):
                m["state"] = "closed"
                self.expecting.pop(m["uid"], None)
                out.append(DM(m["uid"], f"“{self.listings[lid]['idea'].teaser}” closed its search — not a match "
                                        "this time. Want me to keep looking?",
                              [Button("🔎 Browse projects", "browse", "primary")]))
        self.listings[lid]["status"] = "closed"
        self.shortlist = [m["uid"] for m in matched]
        out.append(self._card(lid))
        out.append(self.agent(uid, f"🔒 closed the search — team of {len(matched) + 1} formed.", room=None))
        return out + self._need_home()

    # ------------------------------------------------------------ team server
    def _need_home(self) -> list[Out]:
        """The team graduates from the hub to its own Discord server."""
        self.phase = Phase.KICKOFF
        idea = self.idea
        members = self._team()
        slug = "".join(ch if ch.isalnum() else "-" for ch in idea.title.lower()).strip("-")[:40]
        self.home = {"state": "pending", "slug": slug}
        names = ", ".join(self.name(u) for u in members)
        return [self.agent(None, f"🚀 {idea.title}'s team is getting its own server — the hub stays for "
                                 "finding people.", room=None),
                DM(self.owner_id, f"🚀 **Create {idea.title}'s team server** (Discord only lets a person create a "
                                  "server, so this takes you two taps):\n"
                                  "1. In Discord: **+** (left sidebar) → **Create My Own** → **For me and my friends** "
                                  f"→ name it **{idea.title}** → Create.\n"
                                  "2. Use the **Add BuildPact** link I'm sending next and pick that server.\n"
                                  "I'll set up the channels and DM every teammate an invite as soon as I'm in.",
                   [Button("Skip — stay in the hub", "skiphome", "secondary")], meta={"kind": "home"}),
                Room(slug, members, f"🏠 **{idea.title}** — your team's own server\nTeam: {names}\n"
                                    "Agents moved in with you: kickoff, meeting notes, syncs, PR reviews and 1:1s "
                                    "happen here. Only your team is in this server.")]

    def home_ready(self, server: str) -> list[Out]:
        """The bot joined and set up the team's server."""
        if self.phase != Phase.KICKOFF or getattr(self, "home", {}).get("state") == "ready":
            return []
        self.home.update({"state": "ready", "server": server})
        self.project_room = True
        out: list[Out] = [self.agent(None, f"🏠 **{self.idea.title}** moved to its own server (**{server}**). "
                                           "Members got invites; the hub only knows the team formed.", room=None)]
        return out + self._start_kickoff()

    def _a_skiphome(self, uid: str, _: str) -> list[Out]:
        if uid != self.owner_id or self.phase != Phase.KICKOFF or self.meeting:
            return [DM(uid, "Nothing to skip right now.")]
        self.home = {"state": "hub"}
        return [self.agent(None, "The team stays in the hub for now.", room=None)] + self._start_kickoff()

    # --------------------------------------------------------- kickoff meeting
    def _team(self) -> list[str]:
        return [self.owner_id] + self.shortlist

    def _common_slot(self, members: list[str], start_offset: int = 1) -> tuple[date, str] | None:
        for off in range(start_offset, start_offset + 5):
            d = self.today + timedelta(off)
            if all(not is_busy(self.profiles[u], d) for u in members):
                return d, "7:00 pm"
        return None

    def _start_kickoff(self) -> list[Out]:
        self.phase = Phase.KICKOFF
        idea = self.idea
        members = self._team()
        out: list[Out] = []
        slot = self._common_slot(members)
        when = f"{slot[0]:%a %b %d} · {slot[1]}" if slot else "this week (pick a time together)"
        self.meeting = {"open": True, "inputs": {}, "slot": when, "members": members, "transcript": "",
                        "ready": False}
        out.append(self.agent(None, f"📅 Agents compared calendars privately → **kickoff: {when}** works for all "
                                    f"{len(members)} (nobody's schedule was shared)."))
        out.append(self.agent(None, f"🗒️ **Kickoff agenda** (30 min)\n"
                                    f"1. Intros — name, background, what you're good at\n"
                                    f"2. What you want to work on and learn on {idea.title}\n"
                                    f"3. Milestone 1 scope — what “working prototype” means for us\n"
                                    f"4. Working norms — how we talk, how often we check in\n"
                                    "🎙️ The mediator takes notes, then proposes roles and tasks from what you said. "
                                    "Share the meeting by: uploading a recording in #meetings · `!notes` + pasted "
                                    "notes · or a quick text stand-up (`!standup`)."))
        out.append(DM(self.owner_id, f"Kickoff is set for **{when}**. After you meet, upload the recording or paste "
                                     "notes in the project room — or run a 2-minute text stand-up right now:",
                      [Button("▶️ Run a text stand-up now", "standup", "success")], meta={"kind": "kickoff"}))
        return out

    def _a_standup(self, uid: str, _: str) -> list[Out]:
        if self.phase != Phase.KICKOFF or not self.meeting.get("open"):
            return [DM(uid, "There's no kickoff meeting open right now.")]
        out: list[Out] = [self.agent(None, "🎙️ **Stand-up started.** Everyone: a quick intro, what you want to work "
                                           "on or learn, and anything the team should know. Reply here or to your "
                                           "agent.")]
        for u in self.meeting["members"]:
            if u in self.meeting["inputs"]:
                continue
            self.expecting[u] = ("standup", "kickoff")
            out.append(DM(u, f"🎙️ Kickoff stand-up for **{self.idea.title}**: tell the team who you are, what you "
                             "want to work on or learn, and anything they should know. Your reply is posted to "
                             "the private project room.", meta={"kind": "standup", "project": self.idea.title}))
        return out

    def meeting_input(self, uid: str, text: str) -> list[Out]:
        """A team member's stand-up answer (typed in the room or sent to their agent)."""
        if not self.meeting.get("open") or uid not in self.meeting["members"] or uid in self.meeting["inputs"]:
            return []
        self.expecting.pop(uid, None)
        self.meeting["inputs"][uid] = text.strip()
        line = f"🎙️ **{self.name(uid)}**: {text.strip()}"
        self.public_log.append({"agent": self.name(uid), "text": line, "room": "project", "student": True})
        out: list[Out] = [Channel(uid, line, "project")]
        if set(self.meeting["inputs"]) >= set(self.meeting["members"]):
            self.meeting["ready"] = True
            out.append(self.agent(None, "📝 Everyone shared. The mediator is writing up the notes…"))
        return out

    def meeting_transcript(self, text: str, source: str) -> list[Out]:
        """A recording transcript or pasted notes for the whole meeting."""
        if not self.meeting.get("open"):
            return [self.agent(None, "There's no kickoff meeting open right now.")]
        self.meeting["transcript"] = text.strip()
        self.meeting["ready"] = True
        return [self.agent(None, f"📝 Got the meeting {source} ({len(text.split())} words). "
                                 "The mediator is writing up the notes…")]

    def meeting_material(self) -> str:
        if self.meeting.get("transcript"):
            return self.meeting["transcript"]
        return "\n".join(f"{self.name(u)}: {t}" for u, t in self.meeting.get("inputs", {}).items())

    def apply_meeting_notes(self, data: dict) -> list[Out]:
        """Mediator notes → update what each member wants → roles proposal → negotiation as before."""
        if not self.meeting.get("ready"):
            return []
        self.meeting["open"], self.meeting["ready"] = False, False
        self.notes = data or {}
        roles = [r.name for r in self.idea.roles_needed]
        by_name = {self.name(u).lower(): u for u in self._team()}
        by_name.update({self.name(u).split(" (")[0].lower(): u for u in self._team()})
        lines = []
        for name, info in (self.notes.get("people") or {}).items():
            uid = by_name.get(str(name).lower()) or by_name.get(str(name).split(" (")[0].lower())
            if not uid:
                continue
            want = info.get("wants_role")
            match = next((r for r in roles if want and role_matches(str(want), r)), None)
            if match:
                p = self.profiles[uid]
                p.wanted_roles = [match] + [w for w in p.wanted_roles if not role_matches(w, match)]
            interests = ", ".join(info.get("interests") or [])[:120]
            lines.append(f"• **{self.name(uid)}** — wants {match or 'flexible'}"
                         + (f"; interested in {interests}" if interests else ""))
        actions = "\n".join(f"• {a.get('who', '?')}: {a.get('what', '')}" for a in (self.notes.get("action_items") or [])[:6])
        decisions = "\n".join(f"• {d}" for d in (self.notes.get("decisions") or [])[:5])
        text = (f"📝 **Meeting notes** (by the mediator)\n{self.notes.get('summary', '')}\n"
                + ("\n**Who wants what**\n" + "\n".join(lines) if lines else "")
                + (f"\n**Decisions**\n{decisions}" if decisions else "")
                + (f"\n**Action items**\n{actions}" if actions else "")
                + (f"\n**Milestone 1 scope**: {self.notes['scope']}" if self.notes.get("scope") else ""))
        out: list[Out] = [self.agent(None, text),
                          self.agent(None, "🧭 Proposing roles from your skills **and what you said in the "
                                           "meeting**. Each agent checks with its student; conflicts go through "
                                           "the resolution ladder.")]
        return out + self._negotiate_roles()

    def _a_browse(self, sid: str, _: str) -> list[Out]:
        """People → project: the student's agent reads the board and handshakes with fitting listings."""
        if not self._available(sid):
            return [DM(sid, "You're already on a team.")]
        ranked = []
        out: list[Out] = [self.agent(sid, "🔎 browsing the project board for my student…")]
        for lid, L in self.listings.items():
            key = self._mkey(lid, sid)
            if (L["owner"] == sid or key in self.matches or self.handshakes.get(key) is False
                    or not self._open_roles(lid) or L.get("status") == "formed"):
                continue
            sc, role, msg = self._handshake(lid, sid)
            out.append(msg)
            if sc.ok:
                ranked.append((sc.fit, lid, role))
        ranked.sort(reverse=True)
        if not ranked:
            return out + [DM(sid, "No open project fits you right now — I'll keep watching the board.")]
        lines = "\n".join(f"• **{self.listings[l]['idea'].title}** — {self.listings[l]['idea'].teaser} "
                          f"(fit {f:.0%}, as {r})" for f, l, r in ranked[:3])
        return out + [DM(sid, f"Projects that fit you:\n{lines}\nApply? The owner decides too — double opt-in.",
                         [Button(f"Apply: {self.listings[l]['idea'].title[:30]} ({f:.0%})", f"apply:{l}", "success")
                          for f, l, r in ranked[:3]],
                         meta={"kind": "browse"})]

    def _a_apply(self, sid: str, lid: str) -> list[Out]:
        if lid not in self.listings or self._mkey(lid, sid) in self.matches:
            return [DM(sid, "You've already applied there.")]
        sc, role, _ = self._handshake(lid, sid)
        self.public_log.pop()          # handshake already shown while browsing
        self.matches[self._mkey(lid, sid)] = {"lid": lid, "uid": sid, "role": role, "fit": sc.fit,
                                              "owner": None, "seeker": True, "state": "seeker_yes"}
        idea = self.listings[lid]["idea"]
        return ([self.agent(sid, f"📨 applies to “{idea.title}” as **{role}** (my student opted in)."),
                 DM(sid, f"Applied to **{idea.title}**. I'll tell you as soon as their side decides.")]
                + self._screen_or_review(lid, sid))

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
            role = next(r for r in idea.roles_needed if r.name == c.role)
            alt = next(r for r in idea.roles_needed if r.name == c.loser_role)
            cid = f"c{next(self._ids)}"
            k = Conflict(cid, c.role, c.loser_role, c.winner, c.loser,
                         {c.winner: Valuation.for_student(by_id[c.winner], role, alt),
                          c.loser: Valuation.for_student(by_id[c.loser], role, alt)})
            self.conflicts[cid] = k
            wf, lf = utility(by_id[c.winner], role), utility(by_id[c.loser], role)
            k.log("conflict", None, f"{self.name(c.winner)} and {self.name(c.loser)} both want {c.role}")
            out.append(self.agent(None, f"⚔️ **Conflict**: {self.name(c.winner)}'s and {self.name(c.loser)}'s "
                                        f"agents both want **{c.role}** (skill fit {wf:.0%} vs {lf:.0%}).\n"
                                        "Resolution ladder: 1️⃣ agents trade offers → 2️⃣ neutral mediator → "
                                        "3️⃣ students decide. Each agent scores offers privately."))
            out += self._propose(k, c.winner)
        if not self.open_conflicts():
            out += self._draft_charter()
        return out

    # ------------------------------------------------------ conflict resolution
    def open_conflicts(self) -> list[Conflict]:
        return [c for c in self.conflicts.values() if c.stage in ("trade", "mediator")]

    def _names(self) -> dict[str, str]:
        return {u: p.name for u, p in self.profiles.items()}

    def _desc(self, k: Conflict, pkg: Package) -> str:
        return pkg.describe(self._names(), k.role, k.alt, k.other(pkg.holder))

    def _propose(self, k: Conflict, uid: str) -> list[Out]:
        """uid's agent makes an offer; the other side's agent evaluates it."""
        if k.total_proposals() >= MAX_ROUNDS:
            return self._mediate(k, "no deal after %d offers" % MAX_ROUNDS)
        pkg = k.propose(uid)
        rnd = k.total_proposals()
        k.log("offer", uid, f"Round {rnd}: {self.name(uid)}'s agent offers {self._desc(k, pkg)}", pkg)
        out: list[Out] = [self.agent(uid, f"🤝 **Round {rnd} offer**: {self._desc(k, pkg)}")]
        other = k.other(uid)
        if other == k.a and not k.acceptable(other, pkg):
            # the holder's agent may reject and counter on its own; only *accepting* needs its student
            k.log("reject", other, f"{self.name(other)}'s agent: below my student's mandate", pkg)
            out.append(self.agent(other, "❌ that's below what my student authorised — countering."))
            return out + self._propose(k, other)
        return out + self._ask_responder(k, other, pkg)

    def _ask_responder(self, k: Conflict, uid: str, pkg: Package) -> list[Out]:
        sat = k.satisfaction(uid, pkg)
        rec = ("I'd **accept** — it's within what you told me you want."
               if k.acceptable(uid, pkg) else "I'd **counter** — we can do better for you.")
        self.expecting[uid] = ("offer", k.id)
        return [DM(uid, f"⚔️ **{k.role}** negotiation with {self.name(k.other(uid))}'s agent.\n"
                        f"Offer on the table: {self._desc(k, pkg)}\n"
                        f"By your priorities this gets you **{sat:.0%}** of your ideal outcome. {rec}\n"
                        "Tap a button — or just tell me in your own words what you want "
                        "(e.g. *“fine, but I want to present the demo and I can't do Thursdays”*).",
                   [Button("Accept", f"accept:{k.id}", "success"),
                    Button("Counter for me", f"counter:{k.id}", "primary"),
                    Button("⚖️ Ask the mediator", f"mediator:{k.id}", "secondary"),
                    Button("Leave this team", f"leave:{k.id}", "danger")],
                   meta={"kind": "offer", "conflict": k.id, "role": k.role, "alt": k.alt,
                         "offer": self._desc(k, pkg).replace("**", ""), "satisfaction": round(sat, 2),
                         "is_holder": uid == k.a})]

    def _mediate(self, k: Conflict, why: str) -> list[Out]:
        pkg = k.mediate()
        f = k.fairness(pkg)
        k.yes = set()
        k.log("mediator", None, f"Mediator ({why}) proposes {self._desc(k, pkg)}", pkg)
        out: list[Out] = [self.agent(None, f"⚖️ **Mediator steps in** ({why}).\n"
                                           f"Fairest package (lifts the worse-off side as high as possible, "
                                           f"with no wasted value): {self._desc(k, pkg)}\n"
                                           f"Satisfaction: {self.name(k.a)} **{f['a']:.0%}** · "
                                           f"{self.name(k.b)} **{f['b']:.0%}**. Both students must agree.")]
        for u in (k.a, k.b):
            self.expecting.pop(u, None)
            out.append(DM(u, f"⚖️ The mediator proposes: {self._desc(k, pkg)}\n"
                             f"That's **{k.satisfaction(u, pkg):.0%}** of your ideal outcome. Agree?",
                          [Button("Agree", f"medyes:{k.id}", "success"),
                           Button("Leave this team", f"leave:{k.id}", "danger")],
                          meta={"kind": "mediator", "conflict": k.id}))
        return out

    def _finalize(self, k: Conflict, pkg: Package) -> list[Out]:
        k.stage, k.final = "done", pkg
        other = k.other(pkg.holder)
        self.roles[pkg.holder], self.roles[other] = k.role, k.alt
        self.credit_rules += [f"{self.name(other)} {PERK_TEXT[x]}" for x in sorted(pkg.perks)]
        for u in (k.a, k.b):
            self.expecting.pop(u, None)
        f = k.fairness(pkg)
        k.log("deal", None, f"Deal: {self._desc(k, pkg)}", pkg)
        bal = f"{f['worst'] / f['worst_max']:.0%}" if f["worst_max"] else "—"
        out: list[Out] = [self.agent(None,
            f"✅ **Deal reached** — {self._desc(k, pkg)}\n"
            f"📊 **Why it's fair**: {self.name(k.a)} gets {f['a']:.0%} of their ideal, "
            f"{self.name(k.b)} {f['b']:.0%}. "
            f"{'Pareto-optimal ✔ (no option is better for one without hurting the other)' if f['pareto'] else 'Not Pareto-optimal'}"
            f" · balance: the worse-off side got {bal} of the best balance possible. "
            f"Private scores never left either agent.")]
        out.append(DM(pkg.holder, f"✅ You're **{k.role}**. You'll confirm everything when you sign the charter."))
        out.append(DM(other, f"✅ You're **{k.alt}** with: " + (", ".join(PERK_TEXT[x] for x in sorted(pkg.perks)) or "no extras")
                    + ". You'll confirm everything when you sign the charter."))
        if not self.open_conflicts():
            out += self._draft_charter()
        return out

    def _conflict_for(self, uid: str, cid: str) -> Conflict | None:
        k = self.conflicts.get(cid)
        return k if k and uid in (k.a, k.b) and k.stage in ("trade", "mediator") else None

    def _a_accept(self, uid: str, cid: str) -> list[Out]:
        k = self._conflict_for(uid, cid)
        if not k or not k.pending or k.pending[1] == uid or k.stage != "trade":
            return [DM(uid, "That offer is no longer open.")]
        k.log("accept", uid, f"{self.name(uid)} accepts")
        return [self.agent(uid, "👍 my student accepts.")] + self._finalize(k, k.pending[0])

    def _a_counter(self, uid: str, cid: str) -> list[Out]:
        k = self._conflict_for(uid, cid)
        if not k or k.stage != "trade":
            return [DM(uid, "Nothing to counter right now.")]
        self.expecting.pop(uid, None)
        return self._propose(k, uid)

    def _a_mediator(self, uid: str, cid: str) -> list[Out]:
        k = self._conflict_for(uid, cid)
        if not k or k.stage != "trade":
            return [DM(uid, "The mediator isn't available right now.")]
        return self._mediate(k, f"{self.name(uid)} asked for it")

    def _a_medyes(self, uid: str, cid: str) -> list[Out]:
        k = self._conflict_for(uid, cid)
        if not k or k.stage != "mediator":
            return [DM(uid, "That proposal is no longer open.")]
        k.yes.add(uid)
        out: list[Out] = [self.agent(uid, f"👍 agrees to the mediator's proposal ({len(k.yes)}/2).")]
        if k.yes >= {k.a, k.b}:
            out += self._finalize(k, k.pending[0])
        return out

    def _a_leave(self, uid: str, cid: str) -> list[Out]:
        k = self._conflict_for(uid, cid)
        if not k:
            return [DM(uid, "Nothing to leave right now.")]
        k.stage = "failed"
        k.log("leave", uid, f"{self.name(uid)} leaves")
        for u in (k.a, k.b):
            self.expecting.pop(u, None)
        stay = k.other(uid)
        self.roles[stay] = k.role
        freed = k.alt
        self.roles.pop(uid, None)
        out: list[Out] = [self.agent(uid, "🚪 my student is stepping out of this team. No hard feelings."),
                          self.agent(None, f"{self.name(stay)} keeps **{k.role}**.")]
        if uid == self.owner_id:
            self.phase = Phase.POOL
            return out + [self.agent(None, "The idea owner withdrew; the team is dissolved.")]
        if self.waitlist:
            nxt = self.waitlist.pop(0)
            self.roles[nxt] = freed
            out.append(self.agent(None, f"📨 Next-best match {self.name(nxt)} fills **{freed}**."))
            out.append(DM(nxt, f"A spot opened: **{freed}** on “{self.idea.teaser}”. Details:\n{self.idea.details}"))
        return out + (self._draft_charter() if not self.open_conflicts() else [])

    def negotiation_text(self, uid: str, data: dict) -> list[Out]:
        """A student answered an offer in their own words (already parsed by the LLM)."""
        kind, cid = self.expecting.get(uid, ("", ""))
        k = self._conflict_for(uid, cid)
        if kind != "offer" or not k:
            return [DM(uid, "Noted.")]
        out: list[Out] = []
        shared = k.vals[uid].update(data.get("priorities") or {}, data.get("protect") or {})
        if shared:
            k.log("priority", uid, f"{self.name(uid)}'s agent shares priorities: {', '.join(shared)}")
            out.append(self.agent(uid, "🗣️ my student shared what matters most: " +
                                  ", ".join(PERK_TEXT[x] for x in shared) + "."))
        days = [d for d in (data.get("busy_days") or []) if d]
        if days:
            p = self.profiles[uid]
            p.busy_days = sorted(set(p.busy_days) | {d.lower() for d in days})
            p.busy_reasons.update(data.get("busy_reasons") or {})
            out.append(self.agent(uid, "🔒 added a scheduling rule for my student (details private)."))
        decision = (data.get("decision") or "counter").lower()
        if decision == "accept" and k.pending and k.pending[1] != uid:
            return out + self._a_accept(uid, cid)
        if decision == "leave":
            return out + self._a_leave(uid, cid)
        if decision == "mediator":
            return out + self._mediate(k, f"{self.name(uid)} asked for it")
        self.expecting.pop(uid, None)
        return out + self._propose(k, uid)

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
                          "Sync: agents check in every 3 days at 7:00 pm; the team meets only if it votes to",
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
        self.listings.get(f"L{self.owner_id}", {})["status"] = "formed"
        out.append(DM(self.owner_id, "Charter signed by everyone. Ready to plan the first milestone?",
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
        self._link_dependencies()
        self.fixed, self.moves, self.confirmed = {}, {}, set()
        self.phase = Phase.TASKS
        out: list[Out] = [self.agent(uid, f"🚀 **Milestone 1 kickoff**: “{m1.name}” due **{m1.due:%a %b %d}**. "
                                          f"Splitting {len(self.tasks)} tasks by the charter's roles, around "
                                          "everyone's real calendars.")]
        return out + self._replan()

    def _a_kickoff(self, uid: str, _: str) -> list[Out]:
        return self.kickoff(uid)

    def _link_dependencies(self):
        """Make the hand-offs between roles explicit: model work waits on the data pipeline, UI wiring waits
        on the backend/model, so agents know whom to notify."""
        def first(word):
            return next((t for t in self.tasks if word in t.role.lower()), None)
        upstream = {"ml": first("data"), "frontend": first("ml") or first("backend")}
        ids = {t.id for t in self.tasks}
        for t in self.tasks:
            for key, up in upstream.items():
                if up and key in t.role.lower() and up.id != t.id and up.id not in t.depends_on:
                    # only the role's first task waits; later ones already chain inside the role
                    if not any(d in ids and next(x for x in self.tasks if x.id == d).role == t.role
                               for d in t.depends_on):
                        t.depends_on.append(up.id)

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
        done = getattr(self, "progress", {})
        return "\n".join(f"`{t.due:%a %b %d}` · {'~~' if done.get(t.id, 0) >= 100 else ''}{t.title}"
                         f"{'~~ ✔' if done.get(t.id, 0) >= 100 else ''} → **{self.name(t.assignee)}**" for t in rows)

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

    # ------------------------------------------------------ week-3 rescue
    def _ensure_rescue_state(self):
        if not hasattr(self, "progress"):
            self.progress: dict[str, float] = {}        # task_id -> percent done
            self.availability: dict[str, float] = {}    # user -> share of normal time left (private)
            self.checkin_reasons: dict[str, str] = {}   # user -> private reason (never posted)
            self.awaiting_checkin: set[str] = set()
            self.helps: dict[str, dict] = {}            # task_id -> pending help request
            self.rescues: list[dict] = []               # public rescue timeline
            self.status: dict[str, str] = {}            # user -> on_track | behind | blocked
            self.escalated: set[str] = set()            # task ids already sent to the product lead
            self.pending_esc: set[str] = set()

    def open_tasks(self, uid: str | None = None) -> list[Task]:
        self._ensure_rescue_state()
        return [t for t in self.tasks if self.progress.get(t.id, 0) < 100
                and (uid is None or t.assignee == uid)]

    def advance(self, days: int) -> list[Out]:
        """Demo time-skip. Agents then check in with their students on their own."""
        self._ensure_rescue_state()
        self.today += timedelta(days)
        self.sync_no = getattr(self, "sync_no", 0) + 1
        out: list[Out] = [self.agent(None, (f"⏩ **{days} days later** — it's {self.today:%a %b %d}. " if days else "")
                                     + f"⏰ **Scheduled sync #{self.sync_no}** (every 3 days, 7:00 pm)")]
        if self.phase != Phase.RUNNING or not self.charter:
            return out + [self.agent(None, "No running plan to check on yet.")]
        m1 = self.charter.milestones[0]
        self.votes, self.reviews = {}, getattr(self, "reviews", {})
        out.append(self.agent(None, f"🔔 Agents are checking in privately with their students "
                                    f"({(m1.due - self.today).days} days to “{m1.name}”)."))
        self.awaiting_checkin = set()
        self.status, self.escalated, self.pending_esc, self.rescue_rounds = {}, set(), set(), 0
        for u in self.charter.roles:
            mine = self.open_tasks(u)
            if not mine:
                continue
            self.awaiting_checkin.add(u)
            self.expecting[u] = ("checkin", "m1")
            lines = "\n".join(f"• {t.title} — due {t.due:%a %b %d}" for t in mine)
            out.append(DM(u, f"👋 Quick check-in on Milestone 1:\n{lines}\nHow's it going? Tap one, or tell me "
                             "in your own words (I keep the details private).",
                          [Button("✅ On track", "ontrack", "success"),
                           Button("⚠️ Behind", "behind", "secondary"),
                           Button("🛑 Blocked", "blocked", "danger")],
                          meta={"kind": "checkin",
                                "tasks": {t.id: t.title for t in mine}}))
        return out

    def _expected_pct(self, t: Task) -> float:
        span = max((t.due - t.start).days + 1, 1)
        done = (self.today - t.start).days + 1
        return max(0.0, min(100.0, 100 * done / span))

    def _a_ontrack(self, uid: str, _: str) -> list[Out]:
        return self.checkin(uid, {"status": "on_track", "availability": 1.0})

    def _a_behind(self, uid: str, _: str) -> list[Out]:
        return self.checkin(uid, {"status": "behind", "availability": 0.5})

    def _a_blocked(self, uid: str, _: str) -> list[Out]:
        return self.checkin(uid, {"status": "blocked", "availability": 0.4})

    def checkin(self, uid: str, data: dict) -> list[Out]:
        self._ensure_rescue_state()
        if uid not in self.awaiting_checkin:
            return [DM(uid, "No check-in open right now.")]
        self.awaiting_checkin.discard(uid)
        self.expecting.pop(uid, None)
        status = data.get("status", "on_track")
        if status not in ("on_track", "behind", "blocked"):
            status = "on_track"
        self.status[uid] = status
        # "on track" means the student vouches for their dates; only behind/blocked lowers capacity
        self.availability[uid] = 1.0 if status == "on_track" else min(0.8, float(data.get("availability") or 0.5))
        if data.get("private_reason"):
            self.checkin_reasons[uid] = str(data["private_reason"])
        per_task = {str(k): float(v) for k, v in (data.get("progress") or {}).items()}
        for t in self.open_tasks(uid):
            if t.id in per_task:
                pct = per_task[t.id]
            elif data.get("progress_all") is not None:
                pct = float(data["progress_all"])
            else:
                exp = 100.0 if t.due < self.today else self._expected_pct(t)
                pct = {"on_track": exp, "behind": exp * 0.3, "blocked": exp * 0.1}.get(status, exp)
            self.progress[t.id] = max(self.progress.get(t.id, 0), min(100.0, pct))
        icon = {"on_track": "✅ on track", "behind": "⚠️ behind", "blocked": "🛑 blocked"}.get(status, status)
        out: list[Out] = [self.agent(uid, f"📋 check-in: {icon} (details private)."),
                          DM(uid, "Thanks — got it. I'll only share what the team needs to know.")]
        if not self.awaiting_checkin:
            out += self._assess()
            out += self._dependency_round()
        return out

    # ------------------------------------------------------- dependency agents
    def _dependents(self, t: Task) -> list[Task]:
        return [x for x in self.tasks if t.id in x.depends_on and x.assignee and x.assignee != t.assignee]

    def _dependency_round(self) -> list[Out]:
        """Agents with linked tasks talk to each other: hand-offs, PR reviews, slip warnings."""
        self._ensure_rescue_state()
        out: list[Out] = []
        notified = getattr(self, "notified", set())
        self.notified = notified
        for t in self.tasks:
            deps = self._dependents(t)
            if not deps:
                continue
            done = self.progress.get(t.id, 0) >= 100
            for d in deps:
                key = (t.id, d.id)
                if done and ("pr", key) not in notified:
                    notified.add(("pr", key))
                    rid = f"r{next(self._ids)}"
                    self.reviews[rid] = {"id": rid, "task": t.id, "author": t.assignee, "reviewer": d.assignee,
                                         "next": d.id, "state": "requested"}
                    out.append(self.agent(t.assignee, f"🔗 **Hand-off** → {self.name(d.assignee)}'s agent: "
                                                      f"“{t.title}” is done; “{d.title}” is unblocked. "
                                                      "PR sent for review."))
                    out.append(DM(d.assignee, f"🔗 {self.name(t.assignee)} finished **{t.title}**, which your task "
                                              f"**{d.title}** depends on. Their agent sent a PR for you to review "
                                              "before you build on it.",
                                  [Button("✅ Approve PR", f"approve:{rid}", "success"),
                                   Button("✏️ Request changes", f"changes:{rid}", "secondary")],
                                  meta={"kind": "review"}))
                elif self.status.get(t.assignee) in ("behind", "blocked") and ("slip", key) not in notified:
                    notified.add(("slip", key))
                    out.append(self.agent(t.assignee, f"🔗 heads-up → {self.name(d.assignee)}'s agent: “{t.title}” "
                                                      f"may slip, so “{d.title}” may start later (reason private)."))
                    out.append(DM(d.assignee, f"Heads-up: **{t.title}** (which **{d.title}** depends on) may slip. "
                                              "I'll tell you the moment it's ready. Nothing for you to do yet."))
        return out

    def _a_approve(self, uid: str, rid: str) -> list[Out]:
        r = getattr(self, "reviews", {}).get(rid)
        if not r or r["reviewer"] != uid or r["state"] != "requested":
            return [DM(uid, "That review is no longer open.")]
        r["state"] = "approved"
        t = next(x for x in self.tasks if x.id == r["task"])
        return [self.agent(uid, f"✅ approved the PR for “{t.title}” — building on it now."),
                DM(r["author"], f"✅ {self.name(uid)} approved your PR for **{t.title}**.")]

    def _a_changes(self, uid: str, rid: str) -> list[Out]:
        r = getattr(self, "reviews", {}).get(rid)
        if not r or r["reviewer"] != uid or r["state"] != "requested":
            return [DM(uid, "That review is no longer open.")]
        r["state"] = "changes"
        t = next(x for x in self.tasks if x.id == r["task"])
        a = r["author"]
        return [self.agent(uid, f"✏️ requested changes on the PR for “{t.title}”. The two agents suggest a quick "
                                f"1:1 instead of a long thread."),
                DM(a, f"✏️ {self.name(uid)} requested changes on **{t.title}**. Want a 15-min 1:1 to sort it out? "
                      "(Only the two of you; the agents find a time.)",
                   [Button(f"🗣️ Book a 1:1 with {self.name(uid)}", f"onetoone:{uid}~{rid}", "primary"),
                    Button("I'll fix it async", f"async:{rid}", "secondary")], meta={"kind": "changes"}),
                DM(uid, f"Sent. I suggested a 1:1 with {self.name(a)} — you can also request one yourself.",
                   [Button(f"🗣️ Book a 1:1 with {self.name(a)}", f"onetoone:{a}~{rid}", "secondary")])]

    def _a_async(self, uid: str, rid: str) -> list[Out]:
        r = getattr(self, "reviews", {}).get(rid)
        if not r:
            return []
        t = next(x for x in self.tasks if x.id == r["task"])
        r["state"] = "fixing"
        return [self.agent(uid, f"🛠️ fixing “{t.title}” async — will re-request review.")]

    def _a_onetoone(self, uid: str, arg: str) -> list[Out]:
        other, _, ref = arg.partition("~")
        if not self.charter or other not in self.charter.roles or other == uid:
            return [DM(uid, "You can only book a 1:1 with a teammate.")]
        self.one_on_ones = getattr(self, "one_on_ones", {})
        for mid0, m0 in self.one_on_ones.items():
            if {m0["a"], m0["b"]} == {uid, other} and m0["state"] in ("asked", "booked"):
                if m0["state"] == "asked" and m0["b"] == uid:      # they asked me first: that's a yes
                    return self._a_oneyes(uid, mid0)
                return [DM(uid, f"A 1:1 with {self.name(other)} is already {m0['state']}.")]
        mid = f"m{next(self._ids)}"
        topic = ""
        r = getattr(self, "reviews", {}).get(ref)
        if r:
            topic = next(x.title for x in self.tasks if x.id == r["task"])
        self.one_on_ones[mid] = {"a": uid, "b": other, "topic": topic, "state": "asked"}
        return [self.agent(uid, f"🗣️ asks {self.name(other)}'s agent for a 1:1"
                                + (f" about “{topic}”." if topic else ".")),
                DM(other, f"🗣️ {self.name(uid)} would like a 15-min 1:1" + (f" about **{topic}**" if topic else "")
                          + ". Just the two of you — your agents will find a time privately.",
                   [Button("Accept 1:1", f"oneyes:{mid}", "success"), Button("Not now", f"oneno:{mid}", "secondary")],
                   meta={"kind": "onetoone"})]

    def _a_oneyes(self, uid: str, mid: str) -> list[Out]:
        m = getattr(self, "one_on_ones", {}).get(mid)
        if not m or m["b"] != uid or m["state"] != "asked":
            return [DM(uid, "That request is no longer open.")]
        m["state"] = "booked"
        slot = self._common_slot([m["a"], m["b"]], 1)
        when = f"{slot[0]:%a %b %d} · 6:00 pm" if slot else "this week"
        m["when"] = when
        topic = m["topic"] or "the open question"
        agenda = f"1. What's blocking on “{topic}” · 2. Options · 3. Decide + who does what (15 min)"
        out: list[Out] = [self.agent(None, f"🗣️ **1:1 booked** · {self.name(m['a'])} ↔ {self.name(m['b'])} · {when} "
                                           f"(agents found a time privately)\n{agenda}")]
        for u in (m["a"], m["b"]):
            out.append(DM(u, f"🗣️ 1:1 booked for **{when}** with {self.name(m['b'] if u == m['a'] else m['a'])}.\n"
                             f"Agenda: {agenda}"))
        return out

    def _a_oneno(self, uid: str, mid: str) -> list[Out]:
        m = getattr(self, "one_on_ones", {}).get(mid)
        if not m or m["b"] != uid:
            return [DM(uid, "That request is no longer open.")]
        m["state"] = "declined"
        return [DM(m["a"], f"{self.name(uid)} can't do a 1:1 right now — try async, or bring it to the next sync.")]

    def pr_ready(self, uid: str, text: str) -> list[Out]:
        """A student tells their agent a PR is ready; it goes to whoever depends on that work."""
        mine = [t for t in self.tasks if t.assignee == uid and self._dependents(t)]
        if not mine:
            return [DM(uid, "None of your tasks has a teammate waiting on it — no review needed.")]
        low = text.lower()
        t = next((x for x in mine if any(w in low for w in x.title.lower().split() if len(w) > 3)), mine[0])
        self.progress[t.id] = 100
        return self._dependency_round() or [DM(uid, "Already sent for review.")]

    def _capacity(self, uid: str, start: date, end: date) -> float:
        p = self.profiles[uid]
        daily = self._hours()[uid] / 7 * self.availability.get(uid, 1.0)
        d, total = start, 0.0
        while d <= end:
            if not is_busy(p, d):
                total += daily
            d += timedelta(1)
        return total

    def _window_end(self, t: Task) -> date:
        return max(t.due, self.today + timedelta(1))   # overdue work gets a 1-day grace window

    def _remaining(self, t: Task) -> float:
        return t.est_hours * (1 - self.progress.get(t.id, 0) / 100)

    def _assess(self) -> list[Out]:
        """Find tasks that will miss their date at the current pace and start a rescue."""
        self.rescue_rounds = 0
        at_risk = self._tight()
        ok = len(self.charter.roles) - len({u for u, st in self.status.items() if st != "on_track"})
        out: list[Out] = [self.agent(None, f"🩺 **Team health**: ✅ {ok} on track · "
                                           f"⚠️ {len(at_risk)} task(s) at risk.")]
        out += self._sync_proposal([t for t, _ in at_risk])
        if not at_risk:
            return out
        for t, short in at_risk:
            out += self._rescue(t, short)
        return out

    def _sync_proposal(self, at_risk: list[Task]) -> list[Out]:
        """The mediator decides whether the team needs to meet, and builds the agenda."""
        m1 = self.charter.milestones[0]
        if not at_risk:
            return [self.agent(None, "📅 **No sync meeting needed** — everyone's on track. Agents will check in "
                                     "again in 3 days.")]
        slot = self._common_slot(list(self.charter.roles), 1)
        when = f"{slot[0]:%a %b %d} · {slot[1]}" if slot else "this week"
        items = [f"Rescue “{t.title}” — {self.name(t.assignee)}'s agent raised it (reason private)" for t in at_risk]
        blocked = [x for t in at_risk for x in self._dependents(t)]
        bottlenecks = [f"Bottleneck: “{x.title}” ({self.name(x.assignee)}) waits on “{t.title}”"
                       for t in at_risk for x in self._dependents(t)]
        agenda = ["Quick wins since last sync"] + items + bottlenecks + [
            f"Re-check Milestone 1 scope still fits {m1.due:%b %d}", "Blockers + next check-in"]
        self.sync_meeting = {"when": when, "agenda": agenda, "votes": {}}
        out: list[Out] = [self.agent(None, f"📊 **Progress & bottlenecks** — {len(at_risk)} at risk"
                                           + (f", {len(blocked)} task(s) blocked downstream" if blocked else "")
                                           + f".\n📅 Proposed **15-min sync: {when}** (agents found a slot privately)\n"
                                           + "\n".join(f"{i}. {a}" for i, a in enumerate(agenda, 1))
                                           + "\nEveryone votes: meet, or handle it async? The rescue starts now "
                                             "either way.")]
        for u in self.charter.roles:
            out.append(DM(u, f"📅 Sync #{getattr(self, 'sync_no', 1)}: the mediator proposes a 15-min meeting "
                             f"**{when}**. Meet, or is async fine?",
                          [Button("👍 Meet", "vote:meet", "success"), Button("📝 Async is fine", "vote:async", "secondary")],
                          meta={"kind": "vote"}))
        return out

    def _a_vote(self, uid: str, choice: str) -> list[Out]:
        sm = getattr(self, "sync_meeting", None)
        if not sm or uid not in self.charter.roles or uid in sm["votes"]:
            return [DM(uid, "No open vote right now.")]
        sm["votes"][uid] = choice
        out: list[Out] = [self.agent(uid, f"🗳️ voted ({len(sm['votes'])}/{len(self.charter.roles)}).")]
        if len(sm["votes"]) < len(self.charter.roles):
            return out
        meet = sum(1 for v in sm["votes"].values() if v == "meet")
        if meet * 2 > len(self.charter.roles):
            out.append(self.agent(None, f"✅ **Sync confirmed: {sm['when']}** ({meet}/{len(self.charter.roles)} voted "
                                        "to meet). Agenda pinned above; the mediator will take notes."))
        else:
            out.append(self.agent(None, f"📝 **Handled async** ({meet}/{len(self.charter.roles)} wanted to meet). "
                                        "The agenda becomes a checklist; agents follow up."))
        self.sync_meeting = None
        return out

    def _rescue(self, t: Task, short: float) -> list[Out]:
        hours = max(1.0, round(short * 2 + 0.5) / 2)
        owner = t.assignee
        candidates = []
        asked = {r["helper"] for r in self.rescues if r["task"] == t.title and r.get("helper")}
        for u in self.charter.roles:
            if u == owner or self.status.get(u, "on_track") != "on_track" or self.name(u) in asked:
                continue
            pending = sum(h["hours"] for h in self.helps.values() if h["helper"] == u)
            slack = self._capacity(u, self.today, self._window_end(t)) - pending - sum(
                self._remaining(x) for x in self.open_tasks(u) if x.due <= t.due)
            candidates.append((slack, u))
        candidates.sort(reverse=True)
        when = (f"is overdue (was due {t.due:%a %b %d})" if t.due < self.today
                else f"won't make {t.due:%a %b %d} at the current pace")
        out: list[Out] = [self.agent(owner, f"🚨 **Early warning**: “{t.title}” {when} "
                                            f"(~{hours:g}h short). Reason stays private.")]
        if candidates and candidates[0][0] >= 1.0:
            slack, helper = candidates[0]
            hours = min(hours, max(1.0, int(slack * 2) / 2))
            self.helps[t.id] = {"task": t.id, "helper": helper, "hours": hours, "tried": {helper}}
            self.rescues.append({"task": t.title, "owner": self.name(owner), "helper": self.name(helper),
                                 "hours": hours, "status": "asked"})
            out.append(self.agent(None, f"🛟 Rescue plan: {self.name(helper)}'s agent has the most slack before "
                                        f"{t.due:%a} — asking them to take ~{hours:g}h of “{t.title}”."))
            out.append(DM(helper, f"🛟 A teammate's task is at risk: “{t.title}” (due {t.due:%a %b %d}). "
                                  f"You have room this week. Can you take ~{hours:g}h of it? "
                                  "It's outside your role, so it's your call.",
                          [Button(f"Take {hours:g}h", f"help:{t.id}", "success"),
                           Button("Can't this week", f"nohelp:{t.id}", "secondary")],
                          meta={"kind": "help"}))
            return out
        return out + self._escalate(t, hours)

    def _escalate(self, t: Task, hours: float) -> list[Out]:
        m1 = self.charter.milestones[0]
        new_due = min(t.due + timedelta(2), m1.due) if t.due < m1.due else t.due
        self.escalated.add(t.id)
        self.pending_esc.add(t.id)
        self.rescues.append({"task": t.title, "owner": self.name(t.assignee), "helper": None,
                             "hours": hours, "status": "escalated"})
        return [self.agent(None, f"📣 No teammate has room — escalating “{t.title}” to the product lead."),
                DM(self.owner_id, f"“{t.title}” is ~{hours:g}h short and nobody has slack. Your call:",
                   [Button(f"Move due to {new_due:%a %b %d}", f"shift:{t.id}", "primary"),
                    Button("Cut it from Milestone 1", f"cut:{t.id}", "danger")])]

    def _a_help(self, uid: str, tid: str) -> list[Out]:
        h = getattr(self, "helps", {}).get(tid)
        if not h or h["helper"] != uid:
            return [DM(uid, "That request is no longer open.")]
        self.helps.pop(tid)
        t = next(x for x in self.tasks if x.id == tid)
        part = Task(f"{tid}h", f"Help: {t.title}", t.role, h["hours"], [], uid, self.today, t.due,
                    note=f"split from {self.name(t.assignee)}'s task")
        t.est_hours = max(0.5, t.est_hours - h["hours"])
        self.tasks.append(part)
        self.rescues[-1]["status"] = "rescued"
        out: list[Out] = [self.agent(uid, f"🙌 takes ~{h['hours']:g}h of “{t.title}”."),
                          DM(t.assignee, f"Good news: {self.name(uid)} is taking ~{h['hours']:g}h of “{t.title}”. "
                                         "I didn't share why you were behind.")]
        return out + self._recheck()

    def _a_nohelp(self, uid: str, tid: str) -> list[Out]:
        h = getattr(self, "helps", {}).get(tid)
        if not h or h["helper"] != uid:
            return [DM(uid, "That request is no longer open.")]
        self.helps.pop(tid)
        t = next(x for x in self.tasks if x.id == tid)
        return [self.agent(uid, "can't take it this week.")] + self._escalate(t, h["hours"])

    def _a_shift(self, uid: str, tid: str) -> list[Out]:
        if uid != self.owner_id:
            return [DM(uid, "Only the product lead can decide that.")]
        t = next((x for x in self.tasks if x.id == tid), None)
        if not t:
            return []
        t.due = min(t.due + timedelta(2), self.charter.milestones[0].due)
        self.pending_esc.discard(tid)
        return [self.agent(uid, f"🗓️ moved “{t.title}” to {t.due:%a %b %d}.")] + self._recheck()

    def _a_cut(self, uid: str, tid: str) -> list[Out]:
        if uid != self.owner_id:
            return [DM(uid, "Only the product lead can decide that.")]
        t = next((x for x in self.tasks if x.id == tid), None)
        if not t:
            return []
        self.progress[t.id] = 100
        t.note = "cut from Milestone 1"
        self.pending_esc.discard(tid)
        return [self.agent(uid, f"✂️ cut “{t.title}” from Milestone 1 (moved to backlog).")] + self._recheck()

    def _tight(self) -> list[tuple[Task, float]]:
        out = []
        for t in self.open_tasks():
            u = t.assignee
            if self.status.get(u, "on_track") == "on_track" or t.id in self.escalated or t.title.startswith("Help:"):
                continue
            load_before = sum(self._remaining(x) for x in self.open_tasks(u) if x.due <= t.due and x.id != t.id)
            short = self._remaining(t) - (self._capacity(u, self.today, self._window_end(t)) - load_before)
            if short > 0.5:
                out.append((t, short))
        return out

    def _recheck(self) -> list[Out]:
        if self.helps or self.pending_esc:
            return []
        still = self._tight()
        self.rescue_rounds = getattr(self, "rescue_rounds", 0) + 1
        if still and self.rescue_rounds < 2:
            out: list[Out] = [self.agent(None, f"🔁 Still {len(still)} task(s) tight — agents look for more help.")]
            for t, short in still:
                out += self._rescue(t, short)
            return out
        if still:
            for t, short in still:
                self.escalated.add(t.id)
            return [self.agent(None, f"⚠️ {len(still)} task(s) still tight — flagged for the next team sync.")]
        return [self.agent(None, "🟢 **Rescue complete** — Milestone 1 is back on track. Nobody had to explain "
                                 "their week to the group.\n" + self.plan_table())]

    # -------------------------------------------------------------- privacy
    def private_terms(self) -> list[str]:
        """Strings that must NEVER appear on the public table (for the leak check)."""
        self._ensure_rescue_state()
        terms = []
        for p in self.profiles.values():
            terms += [r for r in p.busy_reasons.values() if r]
        terms += [s.private_reason for s in self.screens.values() if s.private_reason]
        terms += list(self.checkin_reasons.values())
        return [t for t in terms if len(t) >= 4]

    def log_private(self, uid: str, who: str, text: str):
        if not hasattr(self, "private_log"):
            self.private_log: dict[str, list[dict]] = {}
        self.private_log.setdefault(uid, []).append({"who": who, "text": text})

    def log_public(self, agent_of: str | None, text: str):
        self.public_log.append({"agent": agent_of, "text": text})

    def leak_scan(self) -> dict:
        terms = [t.lower() for t in self.private_terms()]
        leaks = []
        for m in self.public_log:
            if m.get("student"):          # students may share what they choose; agents may not
                continue
            low = m["text"].lower()
            leaks += [t for t in terms if t in low]
        return {"public_messages": len(self.public_log), "private_facts": len(set(terms)) + sum(
            len(p.skills) + 2 for p in self.profiles.values()), "leaks": sorted(set(leaks))}

    def snapshot(self) -> dict:
        """Everything the live dashboard shows. Private fields are marked so the page can blur them."""
        self._ensure_rescue_state()
        n = self._names()
        agents = []
        in_conflict = {u: k for k in self.conflicts.values() for u in (k.a, k.b)}
        for uid, p in self.profiles.items():
            if p.missing_fields():
                continue
            shared = []
            if uid in self.screens:
                sc = self.screens[uid]
                shared.append(f"fit {sc.fit:.0%}" if sc.ok else "not a fit")
            role = (self.charter.roles.get(uid) if self.charter else None) or self.roles.get(uid)
            if role:
                shared.append(f"role: {role}")
            if uid in self.waitlist:
                shared.append("waitlisted")
            priv = {"skills": ", ".join(f"{k} {v}/5" for k, v in p.skills.items()),
                    "hours/week": f"{p.hours_per_week:g}",
                    "goal": ", ".join(sorted(p.ambition))}
            if p.busy_days:
                priv["busy"] = ", ".join(f"{d}" + (f" ({p.busy_reasons[d]})" if d in p.busy_reasons else "")
                                         for d in p.busy_days)
            if uid in self.screens and not self.screens[uid].ok:
                priv["why not a fit"] = self.screens[uid].private_reason
            if uid in self.checkin_reasons:
                priv["check-in"] = self.checkin_reasons[uid]
            if uid in self.availability and self.availability[uid] < 1:
                priv["availability"] = f"{self.availability[uid]:.0%} of normal"
            k = in_conflict.get(uid)
            if k:
                v = k.vals[uid]
                priv["role scores"] = ", ".join(f"{r} {x:.1f}" for r, x in v.role_value.items())
                priv["perk values"] = ", ".join(f"{x} {y:g}" for x, y in v.perk_value.items())
                if v.shared_priorities:
                    shared.append("cares about: " + ", ".join(v.shared_priorities))
            if p.idea:
                priv["full plan"] = p.idea.full_plan
                shared.append(f"teaser: {p.idea.teaser}")
            agents.append({"id": uid, "name": p.name, "sim": uid.startswith("sim:"), "owner": p.is_owner,
                           "role": role, "private": priv, "shared": shared,
                           "chat": getattr(self, "private_log", {}).get(uid, [])[-6:]})
        conflicts = []
        for k in self.conflicts.values():
            hist = [{**h, "actor": n.get(h["actor"], h["actor"]) if h.get("actor") else None,
                     "sat": {n[u]: v for u, v in h.get("sat", {}).items()}} for h in k.history]
            entry = {"role": k.role, "alt": k.alt, "a": n[k.a], "b": n[k.b], "stage": k.stage, "history": hist}
            if k.final:
                f = k.fairness(k.final)
                entry["fairness"] = {"a": round(f["a"], 2), "b": round(f["b"], 2), "pareto": f["pareto"]}
            conflicts.append(entry)
        m1 = self.charter.milestones[0] if self.charter else None
        tasks = [{"title": t.title, "who": n.get(t.assignee, "?"), "status": self.task_status(t),
                  "start": t.start.isoformat() if t.start else None,
                  "due": t.due.isoformat() if t.due else None, "hours": t.est_hours,
                  "progress": round(self.progress.get(t.id, 0)), "help": t.title.startswith("Help:")}
                 for t in sorted(self.tasks, key=lambda t: (t.due or self.today, t.title))]
        return {"phase": self.phase.value, "today": self.today.isoformat(),
                "idea": {"title": self.idea.title, "teaser": self.idea.teaser} if self.idea else None,
                "milestone": {"name": m1.name, "due": m1.due.isoformat()} if m1 else None,
                "agents": agents, "public": self.public_log[-80:], "conflicts": conflicts,
                "tasks": tasks, "rescues": self.rescues, "privacy": self.leak_scan(),
                "class": getattr(self, "class_result", None),
                "tracker": self.tracker_text(),
                "board": [{"title": L["idea"].title, "owner": n.get(L["owner"], "?"), "teaser": L["idea"].teaser,
                           "open": self._open_roles(lid), "active": L["owner"] == self.owner_id,
                           "status": L.get("status", "open"),
                           "matches": [{"name": n.get(m["uid"], "?"), "role": m["role"], "fit": round(m["fit"], 2),
                                        "state": m["state"]} for m in self.matches.values() if m["lid"] == lid]}
                          for lid, L in self.listings.items()]}

    # ------------------------------------------------------------ class mode
    def form_class(self, n_synthetic: int, size: int = 4) -> list[Out]:
        """TA command: form balanced teams for a whole class (synthetic classmates + any real students)."""
        from .classmode import form_teams, synth_class
        real = [p for p in self.profiles.values() if not p.missing_fields() and not p.user_id.startswith("sim:")]
        students = synth_class(max(0, n_synthetic - len(real))) + real
        res = form_teams(students, size)
        self.class_result = res
        b, a = res["before"], res["after"]
        out: list[Out] = [self.agent(None,
            f"📚 **Class mode** — {res['students']} students → {len(res['teams'])} teams of ~{size}.\n"
            f"Each agent shared only skills, goal and hours with the matcher; the result shows names and roles only.\n"
            f"```\n               random   BuildPact\n"
            f"role coverage   {b['coverage']:>5.0%}     {a['coverage']:>5.0%}\n"
            f"strength gap    {b['strength_spread']:>5.2f}     {a['strength_spread']:>5.2f}\n"
            f"goal clashes    {b['goal_clashes']:>5}     {a['goal_clashes']:>5}\n"
            f"avg hours gap   {b['hours_gap']:>5.1f}h    {a['hours_gap']:>5.1f}h\n```")]
        lines = []
        for t in res["teams"][:12]:
            lines.append(f"**Team {t['team']}** · " + ", ".join(f"{m['name']} ({m['role']})" for m in t["members"]))
        out.append(self.agent(None, "\n".join(lines)))
        for t in res["teams"]:
            for m in t["members"]:
                if m["id"] in self.profiles:
                    mates = ", ".join(x["name"] for x in t["members"] if x["id"] != m["id"])
                    out.append(DM(m["id"], f"📚 You're on **Team {t['team']}** as **{m['role']}** with {mates}. "
                                           "Your hours and goals weren't shown to anyone."))
        return out

    def offer_text(self, cid: str) -> str:
        k = self.conflicts.get(cid)
        if not k or not k.pending:
            return ""
        return self._desc(k, k.pending[0]).replace("**", "")

    # ------------------------------------------------- meetings → next steps
    def _member_by_name(self, name: str) -> str | None:
        if not self.charter:
            return None
        name = str(name or "").strip().lower()
        for u in self.charter.roles:
            full = self.name(u).lower()
            if name and (name == full or name == full.split(" (")[0] or full.startswith(name.split()[0])):
                return u
        return None

    def meeting_followup(self, notes: dict, kind: str = "meeting") -> list[Out]:
        """Any meeting after kickoff (sync, 1:1, ad hoc): notes → decisions → action items, each confirmed
        by the person it's assigned to before it enters the plan."""
        self._ensure_rescue_state()
        self.followups = getattr(self, "followups", {})
        decisions = notes.get("decisions") or []
        items = notes.get("action_items") or []
        lines = [f"📝 **{kind.capitalize()} notes** (by the mediator)", str(notes.get("summary", "")).strip()]
        if decisions:
            lines.append("**Decisions**\n" + "\n".join(f"• {d}" for d in decisions[:6]))
        if items:
            lines.append("**Next steps**\n" + "\n".join(f"• {a.get('who', '?')}: {a.get('what', '')}" for a in items[:8]))
        else:
            lines.append("No action items detected.")
        out: list[Out] = [self.agent(None, "\n".join(l for l in lines if l), room="meetings")]
        m1 = self.charter.milestones[0] if self.charter else None
        for a in items[:8]:
            uid = self._member_by_name(a.get("who", ""))
            what = str(a.get("what", "")).strip()
            if not uid or not what:
                continue
            fid = f"f{next(self._ids)}"
            due = min(self.today + timedelta(3), m1.due) if m1 else self.today + timedelta(3)
            self.followups[fid] = {"uid": uid, "what": what, "due": due, "state": "asked"}
            out.append(DM(uid, f"📝 From the {kind}: **{what}** — due {due:%a %b %d}. Add it to your tasks?",
                          [Button("✅ Add to my tasks", f"fuadd:{fid}", "success"),
                           Button("Not mine", f"fuskip:{fid}", "secondary")], meta={"kind": "followup"}))
        return out

    def _a_fuadd(self, uid: str, fid: str) -> list[Out]:
        f = getattr(self, "followups", {}).get(fid)
        if not f or f["uid"] != uid or f["state"] != "asked":
            return [DM(uid, "That item is no longer open.")]
        f["state"] = "added"
        role = self.charter.roles.get(uid, "Team") if self.charter else "Team"
        self.tasks.append(Task(fid, f["what"][:60], role, 2.0, [], uid, self.today, f["due"], note="from meeting"))
        return [self.agent(uid, f"📌 added to the plan: “{f['what'][:60]}” — due {f['due']:%a %b %d}.")]

    def _a_fuskip(self, uid: str, fid: str) -> list[Out]:
        f = getattr(self, "followups", {}).get(fid)
        if not f or f["uid"] != uid or f["state"] != "asked":
            return [DM(uid, "That item is no longer open.")]
        f["state"] = "skipped"
        return [self.agent(uid, f"↩️ “{f['what'][:50]}” isn't mine — the mediator will raise it at the next sync.")]

    # ---------------------------------------------------------- project tracker
    def task_status(self, t: Task) -> str:
        """done · review · changes · at_risk · waiting · in_progress · todo"""
        self._ensure_rescue_state()
        pct = self.progress.get(t.id, 0)
        reviews = [r for r in getattr(self, "reviews", {}).values() if r["task"] == t.id]
        if any(r["state"] == "changes" for r in reviews):
            return "changes"
        if pct >= 100:
            return "review" if any(r["state"] == "requested" for r in reviews) else "done"
        tight = {x.id for x, _ in self._tight()} if self.status else set()
        if t.id in tight or t.id in getattr(self, "pending_esc", set()):
            return "at_risk"
        by_id = {x.id: x for x in self.tasks}
        if any(d in by_id and self.progress.get(d, 0) < 100 for d in t.depends_on) and pct == 0:
            return "waiting"
        return "in_progress" if pct > 0 else "todo"

    def tracker(self) -> dict | None:
        """The project tracker: created at Milestone 1 kickoff, kept current by the agents."""
        if not self.tasks or not self.charter:
            return None
        m1 = self.charter.milestones[0]
        rows = []
        for t in sorted(self.tasks, key=lambda x: (x.due or self.today, x.title)):
            st = self.task_status(t)
            deps = [x.title for x in self.tasks if x.id in t.depends_on]
            rows.append({"title": t.title, "who": self.name(t.assignee) if t.assignee else "?", "due": t.due,
                         "status": st, "progress": round(self.progress.get(t.id, 0)), "deps": deps,
                         "help": t.title.startswith("Help:")})
        total = sum(t.est_hours for t in self.tasks) or 1
        done_h = sum(t.est_hours * self.progress.get(t.id, 0) / 100 for t in self.tasks)
        counts = {}
        for r in rows:
            counts[r["status"]] = counts.get(r["status"], 0) + 1
        booked = [m for m in getattr(self, "one_on_ones", {}).values() if m["state"] == "booked"]
        sm = getattr(self, "sync_meeting", None)
        return {"project": self.idea.title, "today": self.today, "milestone": m1.name, "due": m1.due,
                "pct": round(100 * done_h / total), "rows": rows, "counts": counts,
                "health": "at risk" if counts.get("at_risk") else "on track",
                "meeting": sm["when"] if sm else None,
                "one_on_ones": [f"{self.name(m['a'])} ↔ {self.name(m['b'])} · {m.get('when', '')}" for m in booked],
                "sync_no": getattr(self, "sync_no", 0)}

    ICONS = {"done": "✅", "review": "👀", "changes": "✏️", "at_risk": "⚠️", "waiting": "⏳",
             "in_progress": "🔄", "todo": "⬜"}
    LABELS = {"done": "Done", "review": "In review", "changes": "Changes requested", "at_risk": "At risk",
              "waiting": "Waiting on a teammate", "in_progress": "In progress", "todo": "To do"}

    def tracker_text(self) -> str | None:
        tr = self.tracker()
        if not tr:
            return None
        bar = "█" * (tr["pct"] // 10) + "░" * (10 - tr["pct"] // 10)
        health = "🟢 on track" if tr["health"] == "on track" else "🟡 needs attention"
        counts = " · ".join(f"{self.ICONS[k]} {self.LABELS[k]} {v}" for k, v in tr["counts"].items())
        lines = [f"📋 **{tr['project']} — project tracker** · updated by the agents · {tr['today']:%a %b %d}",
                 f"**Milestone 1:** {tr['milestone']} — due **{tr['due']:%a %b %d}** · `{bar}` {tr['pct']}% · {health}",
                 counts, ""]
        for r in tr["rows"]:
            dep = f" ⟵ waits on “{r['deps'][0]}”" if r["status"] == "waiting" and r["deps"] else ""
            pct_note = f" ({r['progress']}%)" if r["status"] in ("in_progress", "at_risk") else ""
            lines.append(f"{self.ICONS[r['status']]} `{r['due']:%b %d}` **{r['title']}** · {r['who']} · "
                         f"{self.LABELS[r['status']]}{pct_note}{dep}")
        extra = []
        if tr["meeting"]:
            extra.append(f"📅 Sync meeting (vote open): {tr['meeting']}")
        else:
            extra.append("📅 Next scheduled sync: every 3 days, 7:00 pm")
        if tr["one_on_ones"]:
            extra.append("🗣️ 1:1s: " + "; ".join(tr["one_on_ones"]))
        return "\n".join(lines + [""] + extra)
