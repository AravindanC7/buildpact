"""Run the whole BuildPact story in the terminal — no Discord, no API key needed.

    python sim.py            # full story with DMs
    python sim.py --quiet    # only the shared negotiation table

Act 1  form the crew      (private screening, staged idea reveal)
Act 2  resolve a conflict (offers in plain English → mediator → fairness proof)
Act 3  plan Milestone 1   (tasks around private busy days, everyone confirms)
Act 1  board + matching   (handshakes, double opt-in, screening, close search)
Act 2  kickoff meeting    (private room, agenda, stand-up, mediator notes)
Act 3  resolve a conflict (offers in plain English, mediator, fairness proof)
Act 4  plan Milestone 1   (tasks around private busy days, dependencies)
Act 5  scheduled sync     (check-ins, bottlenecks, meet/async vote, hand-offs, PR review, 1:1, rescue)
"""
import sys
from datetime import date

from buildpact.engine import Engine
from buildpact.llm import checkin_offline, meeting_offline, negotiation_offline, parse_offline
from buildpact.models import DM, Channel, Room
from buildpact.personas import DECLINES, OWNER_ARAV, OWNERS, SEEKERS, SIM_CHARACTERS

QUIET = "--quiet" in sys.argv
REPLAY = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--replay=")), None)
FRAMES: list = []
E = Engine(today=date(2026, 10, 25))


def capture(caption: str):
    """Record a dashboard frame for the web replay (python sim.py --replay=docs/replay.json)."""
    if not REPLAY:
        return
    snap = E.snapshot()
    snap.pop("public", None)
    for a in snap["agents"]:
        a["sim"] = a["id"] != "arav"          # in the replay only Arav is a real person
    FRAMES.append({"caption": caption, "pub": len(E.public_log), "act": ACT[0], "state": snap})


ACT = ["Setting up"]


def show(outs, caption=None):
    last = None
    for o in outs:
        if isinstance(o, Channel):
            last = o.text
        if isinstance(o, Channel):
            print(f"\n  #buildpact │ {o.text}")
        elif isinstance(o, Room):
            print(f"\n  🏠 team server: {o.name} ({len(o.members)} members)")
        elif not QUIET:
            btns = "  " + "  ".join(f"[{b.label}]" for b in o.buttons) if o.buttons else ""
            body = o.text.replace("\n", "\n            │ ")
            print(f"  DM→{E.name(o.user_id):<6}│ {body}{btns}")

    capture(caption or (last or "").replace("**", "").split("\n")[0][:140] or "…")

def act(title):
    ACT[0] = title
    print(f"\n{'═' * 8} {title} {'═' * max(4, 62 - len(title))}")


def click(uid, action, label=""):
    print(f"\n  ▶ {E.name(uid)} taps “{label or action}”")
    show(E.handle_action(uid, action), f"{E.name(uid)} taps “{label or action}”")


def say(uid, text, kind):
    print(f"\n  💬 {E.name(uid)} → their agent (private): “{text}”")
    data = negotiation_offline(text) if kind == "offer" else checkin_offline(text)
    show(E.negotiation_text(uid, data) if kind == "offer" else E.checkin(uid, data),
         f"{E.name(uid)} tells their agent privately: “{text}”")


act("ACT 1 · The board: two projects already listed, five students in the pool")
for uid, (name, text) in OWNERS.items():
    show(E.seed_owner(uid, name, parse_offline(text)))
for uid, (name, text) in SEEKERS.items():
    show(E.update_profile(uid, name, parse_offline(text)))
show(E.update_profile("arav", "Arav", parse_offline(OWNER_ARAV)))
click("arav", "find_crew", "Post to the board & find my crew")
for m in list(E.matches.values()):
    if m["state"] == "invited":
        uid = m["uid"]
        if uid in DECLINES:
            click(uid, f"notforme:{m['lid']}", "Not for me")
            click(uid, "browse", "Browse projects")
            app = next((k for k in E.listings if E.handshakes.get(f"{k}|{uid}") and f"{k}|{uid}" not in E.matches), None)
            if app:
                click(uid, f"apply:{app}", "Apply")
                click(E.listings[app]["owner"], f"match:{app}~{uid}", "Accept to team")
        else:
            click(uid, f"interested:{m['lid']}", "Interested")
            if E.awaiting(uid):
                ans = SIM_CHARACTERS.get(uid, {}).get("screen", {}).get("say", "github.com/me")
                print(f"\n  📝 {E.name(uid)} answers the screening question privately: “{ans}”")
                show(E.screening_answer(uid, ans))
click("sam", "browse", "Find me a better fit")
app = next((k for k in E.listings if E.handshakes.get(f"{k}|sam") and f"{k}|sam" not in E.matches), None)
if app:
    click("sam", f"apply:{app}", "Apply")
    click(E.listings[app]["owner"], f"match:{app}~sam", "Accept to team")
for m in list(E.matches.values()):
    if m["lid"] == "Larav" and m["state"] == "seeker_yes":
        click("arav", f"match:Larav~{m['uid']}", "Accept to team")

click("arav", "close", "Close search & start team")
print("\n  ▶ Arav creates a server and adds BuildPact — the bot builds #general #standup #meetings #prs")
show(E.home_ready("StudyCrew AI HQ"))

act("ACT 2 · Kickoff meeting in the team's own server")
click("arav", "standup", "Run a text stand-up now")
for uid in list(E.meeting["members"]):
    said = SIM_CHARACTERS.get(uid, {}).get("standup", {}).get(
        "say", "I'm Arav — product + Python. I'll own product and the pilot with 3 ASU courses.")
    show(E.meeting_input(uid, said))
show(E.apply_meeting_notes(meeting_offline(E.meeting_material(), [E.name(u) for u in E._team()],
                                           [r.name for r in E.idea.roles_needed])))

act("ACT 3 · Conflict: Priya and Arjun both want ML Lead")
k = next(iter(E.conflicts.values()))
for line in SIM_CHARACTERS["arjun"]["offer"][:2]:
    if k.stage == "trade" and E.awaiting("arjun"):
        say("arjun", line["say"], "offer")
for uid in ("priya", "arjun"):
    if k.stage == "mediator":
        click(uid, f"medyes:{k.id}", "Agree")

act("ACT 4 · Sign the charter and plan Milestone 1")
for uid in list(E.charter.roles):
    click(uid, "sign", "Sign")
click("arav", "kickoff", "Kick off Milestone 1")
while E.moves:
    tid, m = next(iter(E.moves.items()))
    click(m.to_user, f"moveyes:{tid}", "Take it")
for uid in list(E.charter.roles):
    click(uid, "confirm", "Confirm")

act("ACT 5 · Scheduled sync: check-ins, bottlenecks, vote, hand-offs, PR review, 1:1")
show(E.advance(3))
for uid in list(E.awaiting_checkin):
    say(uid, SIM_CHARACTERS.get(uid, {}).get("checkin", {}).get("say", "on track"), "checkin")
for _ in range(3):
    for tid, h in list(E.helps.items()):
        click(h["helper"], f"help:{tid}", "Take it")

for rid, r in list(getattr(E, "reviews", {}).items()):
    if r["state"] == "requested":
        click(r["reviewer"], f"changes:{rid}", "Request changes")
        click(r["author"], f"onetoone:{r['reviewer']}~{rid}", "Book a 1:1")
        mid = next(k for k, m in E.one_on_ones.items() if m["state"] == "asked")
        click(r["reviewer"], f"oneyes:{mid}", "Accept 1:1")
        break
if getattr(E, "sync_meeting", None):
    for uid in list(E.charter.roles):
        click(uid, "vote:meet", "Meet")

act("BONUS · Class mode: a TA forms teams for 40 students")
show(E.form_class(40, 4))
print(f"\n🛡️  Privacy check: {E.leak_scan()}")

if REPLAY:
    import json
    out = {"public": E.public_log, "frames": FRAMES}
    with open(REPLAY, "w") as f:
        json.dump(out, f, default=str, separators=(",", ":"))
    print(f"\nreplay: {len(FRAMES)} frames → {REPLAY}")
