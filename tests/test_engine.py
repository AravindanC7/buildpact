import asyncio
import json
from datetime import date

import httpx
import pytest

from buildpact import llm
from buildpact.engine import Engine
from buildpact.llm import parse_offline
from buildpact.models import DM, Channel, Phase

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


def to_charter(e):
    e.handle_action("o", "find_crew")
    outs = e.handle_action("o", "reveal")
    oid = next(iter(e.offers))
    e.handle_action(e.offers[oid].loser, f"accept:{oid}")
    return outs


def test_screening_keeps_reasons_private():
    e = setup()
    outs = e.handle_action("o", "find_crew")
    pub = channel_text(outs)
    assert "paper" not in pub and "hrs/week; you have" not in pub
    private = [o for o in outs if isinstance(o, DM) and o.user_id == "x"]
    assert "paper" in private[0].text
    assert e.shortlist[0] == "a" and "x" not in e.shortlist


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


def test_role_conflict_and_swap():
    e = setup()
    e.handle_action("o", "find_crew")
    e.handle_action("o", "reveal")
    assert e.roles["a"] == "ML Lead"
    oid = next(iter(e.offers))
    o = e.offers[oid]
    assert (o.winner, o.loser) == ("a", "b")
    e.handle_action("b", f"counter:{oid}")
    e.handle_action("a", f"swapyes:{oid}")
    assert e.roles["b"] == "ML Lead" and e.phase == Phase.CHARTER


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
