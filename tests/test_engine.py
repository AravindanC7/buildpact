import asyncio
import json
from datetime import date

import httpx
import pytest

from buildpact import llm
from buildpact.engine import Engine
from buildpact.llm import parse_offline
from buildpact.models import DM, Channel, Phase, Room

IDEA = ("skills: product 4, python 4\nroles: Product Lead\ngoal: startup, portfolio\nhours: 10\n"
        "idea: StudyCrew | details here | SECRET full plan | roles: Product Lead=product; "
        "ML Lead=ml,python; Data Pipeline=data,python; Frontend=frontend | teaser: AI study buddy | goal: portfolio | min: 6")
PEOPLE = {
    "o": ("Owner", IDEA),
    "a": ("Ana", "skills: ml 5, python 5\nroles: ML Lead\ngoal: portfolio\nhours: 9\nbusy: wed (midterm)"),
    "b": ("Ben", "skills: ml 3, python 4, data 4\nroles: ML Lead, Data Pipeline\ngoal: portfolio\nhours: 8"),
    "c": ("Cy", "skills: frontend 4\nroles: Frontend\ngoal: portfolio\nhours: 7"),
    "x": ("Xi", "skills: stats 4\nroles: Research\ngoal: paper\nhours: 2"),
}


def channel_text(outs):
    return "\n".join(o.text for o in outs if isinstance(o, Channel))


def setup(**kw):
    e = Engine(today=date(2026, 10, 25))
    for uid, (n, t) in PEOPLE.items():
        e.update_profile(uid, n, parse_offline(t))
    return e


def form(e):
    """Owner posts; every compatible student opts in; owner matches everyone (double opt-in)."""
    outs = e.handle_action("o", "find_crew")
    for m in list(e.matches.values()):
        if m["state"] == "invited":
            outs += e.handle_action(m["uid"], f"interested:{m['lid']}")
    for m in list(e.matches.values()):
        if m["state"] == "seeker_yes":
            outs += e.handle_action("o", f"match:{m['lid']}~{m['uid']}")
    return outs


def kickoff_meeting(e):
    """Owner closes the search; everyone shares a stand-up; the mediator writes notes."""
    outs = e.handle_action("o", "close")
    outs += e.home_ready("Team HQ")
    outs += e.handle_action("o", "standup")
    said = {"o": "I'm the owner, product side.", "a": "ML is my thing, I want to lead the model.",
            "b": "Data engineering mostly, but I'd love to be ML lead.", "c": "Frontend and design for me."}
    for u in list(e.meeting["members"]):
        outs += e.meeting_input(u, said.get(u, "hi"))
    notes = llm.meeting_offline(e.meeting_material(), [e.name(u) for u in e._team()],
                                [r.name for r in e.idea.roles_needed])
    return outs + e.apply_meeting_notes(notes)


def to_charter(e):
    outs = form(e)
    outs += kickoff_meeting(e)
    k = next(iter(e.conflicts.values()))
    outs += e.negotiation_text(k.b, {"decision": "mediator"})
    outs += e.handle_action(k.a, f"medyes:{k.id}")
    outs += e.handle_action(k.b, f"medyes:{k.id}")
    return outs


def test_screening_keeps_reasons_private():
    e = setup()
    outs = e.handle_action("o", "find_crew")
    pub = channel_text(outs)
    assert "paper" not in pub and "hrs/week; you have" not in pub
    private = [o for o in outs if isinstance(o, DM) and o.user_id == "x"]
    assert "paper" in private[0].text
    assert "Lo|a" in e.matches and "Lo|x" not in e.matches


def test_full_plan_locked_until_everyone_signs():
    e = setup()
    outs = to_charter(e)
    assert all("SECRET" not in o.text for o in outs)
    assert e.phase == Phase.CHARTER
    members = list(e.charter.roles)
    seen = []
    for u in members[:-1]:
        seen += e.handle_action(u, "sign")
    assert all("SECRET" not in o.text for o in seen)
    last = e.handle_action(members[-1], "sign")
    assert e.phase == Phase.SIGNED
    assert any("SECRET" in o.text for o in last if isinstance(o, DM))
    assert not any("SECRET" in o.text for o in last if isinstance(o, Channel))


def test_negotiation_in_plain_words_then_mediator():
    e = setup()
    form(e)
    kickoff_meeting(e)
    k = next(iter(e.conflicts.values()))
    assert (k.a, k.b) == ("a", "b") and e.awaiting("b")[0] == "offer"
    outs = e.negotiation_text("b", llm.negotiation_offline(
        "ok I'd do data pipeline but I want to present the demo, and I can't do thursdays (work)"))
    pub = channel_text(outs)
    assert "presents the final demo" in pub and "work" not in pub
    assert "thu" in e.profiles["b"].busy_days
    assert k.vals["b"].perk_value["present"] == 5.0
    e.negotiation_text("b", {"decision": "mediator"})
    assert k.stage == "mediator"
    e.handle_action("a", f"medyes:{k.id}")
    outs = e.handle_action("b", f"medyes:{k.id}")
    assert k.stage == "done" and k.pareto_optimal(k.final)
    f = k.fairness(k.final)
    assert f["worst"] == pytest.approx(f["worst_max"])
    assert "Deal reached" in channel_text(outs) and e.phase == Phase.CHARTER


def test_accepting_an_offer_applies_roles_and_perks():
    e = setup()
    form(e)
    kickoff_meeting(e)
    k = next(iter(e.conflicts.values()))
    e.handle_action("b", f"accept:{k.id}")
    assert k.stage == "done" and e.roles["a"] == "ML Lead"


def test_busy_reason_never_public_and_plan_confirms():
    e = setup()
    to_charter(e)
    for u in list(e.charter.roles):
        e.handle_action(u, "sign")
    outs = e.handle_action("o", "kickoff")
    while e.moves:
        tid, m = next(iter(e.moves.items()))
        outs += e.handle_action(m.to_user, f"moveyes:{tid}")
    assert "midterm" not in channel_text(outs)
    for t in e.tasks:
        assert t.assignee in e.charter.roles and t.due <= e.charter.milestones[0].due
        if t.assignee == "a":
            assert t.start.weekday() != 2  # Ana is busy Wednesdays
    for u in list(e.charter.roles):
        e.handle_action(u, "confirm")
    assert e.phase == Phase.RUNNING


def _running(e):
    to_charter(e)
    for u in list(e.charter.roles):
        e.handle_action(u, "sign")
    e.handle_action("o", "kickoff")
    while e.moves:
        tid, m = next(iter(e.moves.items()))
        e.handle_action(m.to_user, f"moveyes:{tid}")
    for u in list(e.charter.roles):
        e.handle_action(u, "confirm")
    assert e.phase == Phase.RUNNING


def test_week3_rescue_keeps_reasons_private():
    e = setup()
    _running(e)
    outs = e.advance(3)
    behind = next(u for u in e.awaiting_checkin if u != "o")
    for u in list(e.awaiting_checkin):
        if u == behind:
            outs += e.checkin(u, {"status": "behind", "availability": 0.3, "progress_all": 5,
                                  "private_reason": "family emergency"})
        else:
            outs += e.handle_action(u, "ontrack")
    assert "at risk" in channel_text(outs)
    for _ in range(4):
        for tid, h in list(e.helps.items()):
            outs += e.handle_action(h["helper"], f"help:{tid}")
    assert any(t.title.startswith("Help:") for t in e.tasks)
    assert "family emergency" not in channel_text(outs)
    assert e.leak_scan()["leaks"] == []


def test_double_opt_in_and_browse():
    e = setup()
    e.handle_action("o", "find_crew")
    m = e.matches["Lo|c"]
    outs = e.handle_action("c", "notforme:Lo")
    assert m["state"] == "seeker_passed"
    owner_dm = [o.text for o in outs if isinstance(o, DM) and o.user_id == "o"]
    assert owner_dm and "Cy" not in owner_dm[0]          # owner never learns who passed
    e.handle_action("a", "interested:Lo")
    outs = e.handle_action("o", "pass:Lo~a")
    a_dm = [o.text for o in outs if isinstance(o, DM) and o.user_id == "a"]
    assert "not a match this time" in a_dm[0]
    assert "SECRET" not in channel_text(outs)
    # people -> project: a second owner's listing, found by browsing
    e.seed_owner("n", "Noor", parse_offline("skills: product 4\nroles: Product Lead\ngoal: portfolio\nhours: 8\n"
                                            "idea: Cart | d | p | roles: Product Lead=product; Frontend=frontend | min: 5"))
    outs = e.handle_action("c", "browse")
    assert any(b.action == "apply:Ln" for o in outs if isinstance(o, DM) for b in o.buttons)
    e.handle_action("c", "apply:Ln")
    e.handle_action("n", "match:Ln~c")
    assert e.matches["Ln|c"]["state"] == "matched"


def test_screening_close_search_and_kickoff():
    e = setup()
    e.profiles["o"].idea.question = "Share a GitHub link"
    e.profiles["o"].idea.must_have = ["python"]
    outs = e.handle_action("o", "find_crew")
    assert "Lo|c" not in e.matches                       # Cy has no python: fails the must-have privately
    assert "python" in [o.text for o in outs if isinstance(o, DM) and o.user_id == "c"][0]
    e.handle_action("a", "interested:Lo")
    assert e.matches["Lo|a"]["state"] == "screening" and e.awaiting("a")[0] == "screening"
    outs = e.screening_answer("a", "github.com/ana-ml")
    owner_dm = [o.text for o in outs if isinstance(o, DM) and o.user_id == "o"][0]
    assert "github.com/ana-ml" in owner_dm and "github.com/ana-ml" not in channel_text(outs)
    e.handle_action("o", "match:Lo~a")
    outs = e.handle_action("o", "close")
    assert e.phase == Phase.KICKOFF and any(isinstance(o, Room) for o in outs)
    assert e.matches["Lo|b"]["state"] == "closed"          # still-pending invite is closed politely
    assert "own server" in channel_text(outs)
    outs = e.home_ready("Team HQ")
    assert e.project_room and "moved to its own server" in channel_text(outs)
    assert "kickoff" in channel_text(outs).lower() and "agenda" in channel_text(outs).lower()
    assert e.home_ready("Team HQ") == []                    # idempotent
    e.handle_action("o", "standup")
    for u in list(e.meeting["members"]):
        e.meeting_input(u, "I'd like to work on ML" if u == "a" else "product for me")
    assert e.meeting["ready"]
    outs = e.apply_meeting_notes(llm.meeting_offline(e.meeting_material(), [e.name(u) for u in e._team()],
                                                     [r.name for r in e.idea.roles_needed]))
    assert "Meeting notes" in channel_text(outs) and e.phase in (Phase.ROLES, Phase.CHARTER)


def test_sync_vote_dependencies_and_one_on_one():
    e = setup()
    _running(e)
    up = next(t for t in e.tasks if e._dependents(t))
    down = e._dependents(up)[0]
    outs = e.advance(3)
    for u in list(e.awaiting_checkin):
        outs += e.handle_action(u, "behind" if u == down.assignee and u != up.assignee else "ontrack")
    e.progress[up.id] = 100
    outs += e._dependency_round()
    assert "Hand-off" in channel_text(outs) and "Scheduled sync #1" in channel_text(outs)
    rid = next(iter(e.reviews))
    outs = e.handle_action(down.assignee, f"changes:{rid}")
    assert e.reviews[rid]["state"] == "changes"
    a, b = up.assignee, down.assignee
    e.handle_action(a, f"onetoone:{b}~{rid}")
    outs = e.handle_action(b, f"onetoone:{a}~{rid}")    # both ask: still one meeting
    assert sum(1 for m in e.one_on_ones.values() if m["state"] == "booked") == 1
    assert "1:1 booked" in channel_text(outs)
    if getattr(e, "sync_meeting", None):
        for u in list(e.charter.roles):
            outs = e.handle_action(u, "vote:meet")
        assert "Sync confirmed" in channel_text(outs)
    assert e.leak_scan()["leaks"] == []


def test_meeting_followup_needs_each_persons_yes():
    e = setup()
    _running(e)
    a, b = list(e.charter.roles)[:2]
    notes = {"summary": "Sync.", "decisions": ["Ship v2 schema"],
             "action_items": [{"who": e.name(a), "what": "Fix the schema"}, {"who": e.name(b), "what": "Book rooms"},
                              {"who": "Nobody Here", "what": "ignored"}]}
    outs = e.meeting_followup(notes, "meeting")
    assert "Next steps" in channel_text(outs) and len(e.followups) == 2
    n = len(e.tasks)
    fa, fb = list(e.followups)
    e.handle_action(a, f"fuadd:{fa}")
    e.handle_action(a, f"fuadd:{fb}")                    # can't accept someone else's item
    e.handle_action(b, f"fuskip:{fb}")
    assert len(e.tasks) == n + 1 and e.tasks[-1].assignee == a
    assert [f["state"] for f in e.followups.values()] == ["added", "skipped"]


def test_recorder_writes_wav():
    from buildpact.recorder import to_wav
    w = to_wav(b"\x00\x00" * 1600)
    assert w[:4] == b"RIFF" and len(w) > 3200


def test_class_mode_beats_random():
    from buildpact.classmode import form_teams, synth_class
    r = form_teams(synth_class(40), 4)
    assert r["after"]["goal_clashes"] < r["before"]["goal_clashes"]
    assert r["after"]["hours_gap"] < r["before"]["hours_gap"]
    assert r["after"]["coverage"] >= r["before"]["coverage"]
    assert sum(len(t["members"]) for t in r["teams"]) == 40


def test_dropout_without_waitlist_replans():
    e = setup()
    to_charter(e)
    for u in list(e.charter.roles):
        e.handle_action(u, "sign")
    e.handle_action("o", "kickoff")
    dropper = next(u for u in e.charter.roles if u not in ("o",))
    e.waitlist = []
    e.moves.clear()
    e.handle_text(dropper, "I need to drop out")
    assert dropper not in e.charter.roles
    assert all(t.assignee in e.charter.roles for t in e.tasks)


def test_kickoff_with_llm_tasks():
    e = setup()
    to_charter(e)
    for u in list(e.charter.roles):
        e.handle_action(u, "sign")
    roles = sorted(set(e.charter.roles.values()))
    tasks = [{"id": "t1", "title": "Spec", "role": roles[0], "est_hours": 2, "depends_on": []},
             {"id": "t2", "title": "Build", "role": roles[1], "est_hours": 3, "depends_on": ["t1"]}]
    e.kickoff("o", tasks)
    assert [t.title for t in e.tasks] == ["Spec", "Build"]
    assert e.tasks[1].start > e.tasks[0].due or e.tasks[1].start >= e.tasks[0].due


def test_gemini_response_parsing(monkeypatch):
    payload = {"candidates": [{"content": {"parts": [{"text": json.dumps(
        {"skills": {"python": 5}, "hours_per_week": 8, "ambition": ["portfolio"]})}]}}]}

    def handler(request):
        assert request.headers["x-goog-api-key"] == "k"
        return httpx.Response(200, json=payload)

    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler)))
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    data = asyncio.run(llm.extract_profile("I know python well", {}))
    assert data["hours_per_week"] == 8


def test_llm_failure_falls_back(monkeypatch):
    def handler(request):
        return httpx.Response(429, json={"error": "quota"})

    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler)))
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    data = asyncio.run(llm.extract_profile("hours: 5\ngoal: portfolio", {}))
    assert data["hours_per_week"] == 5
