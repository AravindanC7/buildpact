"""Run the whole BuildPact demo in the terminal — no Discord, no API key needed.

    python sim.py            # scripted demo with 6 students
    python sim.py --quiet    # only the shared negotiation table

Shared-table messages are what everyone sees; DMs are private to one student.
"""
import sys
from datetime import date

from buildpact.engine import Engine
from buildpact.llm import parse_offline
from buildpact.models import DM, Channel
from buildpact.personas import OWNER_ARAV, SEEKERS

QUIET = "--quiet" in sys.argv
E = Engine(today=date(2026, 10, 25))

STUDENTS = {"arav": ("Arav", OWNER_ARAV), **SEEKERS}


def show(outs):
    for o in outs:
        if isinstance(o, Channel):
            print(f"\n  #buildpact │ {o.text}")
        elif not QUIET:
            btns = "  " + "  ".join(f"[{b.label}]" for b in o.buttons) if o.buttons else ""
            body = o.text.replace("\n", "\n            │ ")
            print(f"  DM→{E.name(o.user_id):<6}│ {body}{btns}")


def step(title):
    print(f"\n{'═' * 8} {title} {'═' * (60 - len(title))}")


def click(uid, action, label=""):
    print(f"\n  ▶ {E.name(uid)} taps “{label or action}”")
    show(E.handle_action(uid, action))


def offer_for(uid):
    return next(o.id for o in E.offers.values() if uid in (o.loser, o.winner))


step("1 · Each student DMs their own agent")
for uid, (name, text) in STUDENTS.items():
    show(E.update_profile(uid, name, parse_offline(text)))

step("2 · Arav starts the search (stage 1: teaser only)")
click("arav", "find_crew", "Find my crew")

step("3 · Arav approves stage 2 → role negotiation")
click("arav", "reveal", "Reveal stage 2")

step("4 · Conflict: Arjun asks for a swap, Priya keeps ML Lead, Arjun accepts the perks")
oid = offer_for("arjun")
click("arjun", f"counter:{oid}", "Ask for a swap")
click("priya", f"swapno:{oid}", "Keep ML Lead")
click("arjun", f"accept:{oid}", "Accept")

step("5 · Everyone signs the charter (stage 3 unlocks)")
for uid in list(E.charter.roles):
    click(uid, "sign", "Sign")

step("6 · Phase 2: Milestone 1 kickoff")
click("arav", "kickoff", "Kick off Milestone 1")
while E.moves:
    tid, m = next(iter(E.moves.items()))
    click(m.to_user, f"moveyes:{tid}", "Take it")

step("7 · Maya says it's too much; everyone else confirms")
click("maya", "toomuch", "Too much this week")
while E.moves:
    tid, m = next(iter(E.moves.items()))
    click(m.to_user, f"moveyes:{tid}", "Take it")
for uid in list(E.charter.roles):
    click(uid, "confirm", "Confirm")

step("8 · Bonus: Maya has to drop out → waitlisted Leo is invited")
print("\n  ▶ Maya texts: “I need to drop out, family stuff”")
show(E.handle_text("maya", "I need to drop out, family stuff"))
click("leo", "join", "Join + sign")
while E.moves:
    tid, m = next(iter(E.moves.items()))
    click(m.to_user, f"moveyes:{tid}", "Take it")
for uid in list(E.charter.roles):
    click(uid, "confirm", "Confirm")
print()
