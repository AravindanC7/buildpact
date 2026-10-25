# BuildPact 🤝

**🌐 Website + live replay:** https://aravindanc7.github.io/buildpact/

**Personal AI agents that form your project team, negotiate the deal, and rescue it in week 3, without ever exposing your private life.**

Cubbie Hackathon · Track 2 (Agent-to-Agent Coordination) · Python · Discord · OpenAI/Gemini

---

## The campus problem

Student project teams fail in predictable ways:

1. **They form on vibes.** "Anyone want to build something?" in a Discord channel. Nobody aligns on goals, hours, roles or credit.
2. **Conflicts get avoided, not resolved.** Two people want ML lead; one quietly resents the other for six weeks.
3. **They die in week 3.** Someone falls behind (midterms, a job, family) and doesn't want to explain why, so they go silent. The deadline slips and everyone blames each other.
4. **Privacy makes it worse.** Nobody wants to tell four near-strangers "I have therapy Tuesdays" or "I can only give 4 hours."

BuildPact gives every student a **private agent**. The agents negotiate with each other at a shared table, sharing only what the protocol allows, and every commitment needs the student's own yes.

## Where students meet: the project board

BuildPact is a **campus-wide marketplace for projects**, not just a class tool. It runs as one shared Discord server:

| Channel | What lives there |
|---|---|
| **DM with the bot** | Your personal agent. You talk in plain English; it keeps your profile private. |
| **#project-board** | One card per project: teaser, open roles, vibe (startup / portfolio / paper / hackathon / course), hours band. Nothing private. |
| **#buildpact** | The table where agents meet: handshakes, opt-ins, offers, mediator. |
| **The team's own server** | When the owner closes the search, they create a server (two taps; Discord only lets people create servers) and add BuildPact. The bot builds `#general #standup #meetings #prs`, DMs every teammate an invite, and the agents move in with the team. |

**Matching works in both directions, always with double opt-in:**

- **Project → people.** An owner's agent posts a listing, then **handshakes** with every student's agent: *goals compatible? hours band OK? an open role fits?* Each side answers from its own private profile, so only ✅ / ❌ and a fit % cross. Compatible students are asked privately: *Interested?*
- **People → project.** A student's agent **browses the board**, handshakes with each listing, and offers *"Apply: CampusCart (70%)"*.
- **Both must say yes.** If either side passes, the other only hears *"not a match this time."* No reason is given, and the owner never learns who passed. Only after a mutual match does the student see the project details.

The demo board has three projects: your idea, plus **CampusCart** (Noor) and **ThesisLens** (Omar). Leo passes on StudyCrew and his agent finds CampusCart instead. Sam isn't compatible with StudyCrew, so his agent finds ThesisLens.

## The full journey

```
Board ─► handshakes ─► opt-in (+ private screening answer) ─► owner matches ─► 🔒 close search
  ─► the team's own server (bot sets up channels + invites) ─► agents find a kickoff slot privately ─► agenda
  ─► kickoff meeting (recording · pasted notes · text stand-up) ─► mediator notes
  ─► roles proposed from skills + what people said ─► conflicts: offers → mediator → students
  ─► charter signed ─► Milestone 1 tasks (from the plan + meeting notes) ─► everyone confirms
  ─► every 3 days: scheduled sync ─► progress + bottlenecks ─► team votes meet / async
  ─► dependency agents: hand-offs, PR reviews, slip heads-ups ─► 1:1 for two-person conflicts ─► rescue ─► 🟢
```

| Step | What happens | What judges see |
|---|---|---|
| **1 · Match** | Owner posts to **#project-board**. Their agent handshakes with every student's agent (goals · hours · **must-have skills** · role fit). Students opt in and answer the owner's **screening question privately**; only the owner sees the answer. Others browse the board and apply elsewhere. | Handshakes, private screening, "🤝 Mutual match", "not a match this time" |
| **2 · Close & move out** | The owner **closes the search** when the team feels right. The team **graduates to its own Discord server**: the owner creates it and adds BuildPact with one link, and the bot builds the channels and DMs invites. Agents compare calendars privately and post a kickoff slot plus an **agenda**. | "kickoff: Mon 7:00 pm works for all 4 (nobody's schedule was shared)" |
| **3 · Meeting → notes** | The team meets. Upload the **recording** (transcribed with OpenAI speech-to-text), paste notes with `!notes`, or run `!standup`. The **mediator** writes notes: who wants what, decisions, action items, scope. | Meeting notes in the room |
| **4 · Roles & conflicts** | Roles are proposed from skills **and what people said**. Two people want ML Lead → offers in plain English → mediator → a **fairness proof**. | 78% / 83%, Pareto-optimal |
| **5 · Plan** | Charter signed. Milestone 1 tasks come from the plan plus the meeting's action items, scheduled around private busy days. Everyone confirms. | "commitment that day (details private)" |
| **Project tracker** | Created at Milestone 1 kickoff in the team's `#tracker` channel: one pinned message the agents keep editing. It shows progress %, health, every task's status (to do · in progress · waiting on a teammate · in review · changes requested · at risk · done), dependencies, the next sync and booked 1:1s. | A live, self-updating tracker |
| **6 · Scheduled sync** | The charter fixes a cadence (every 3 days, 7 pm; `!advance 3` in the demo, `SYNC_EVERY_MINUTES` for real time). Agents check in; the mediator posts **progress + bottlenecks** (tasks waiting on a slipping task) and an agenda; **everyone votes** meet or async. | "Sync confirmed (4/4)" or "Handled async" |
| **7 · Dependency agents** | When a task others depend on is done, its owner's agent hands off to the dependent's agent and **sends the PR for review**; if it's slipping, the dependent gets a heads-up (reason private). Students can also DM "PR ready". | "🔗 Hand-off → Priya's agent … PR sent" |
| **Every meeting → next steps** | Kickoff, syncs and 1:1s can all be recorded (`!record`/`!stop`) or uploaded. The mediator posts notes, decisions and next steps, and each person's agent asks them to confirm their own items before they join the plan. | "📌 added to the plan: Fix the schema…" |
| **8 · 1:1s** | Changes requested? Either side taps **Book a 1:1**; the two agents find a time privately and post a 15-min agenda. If both ask, it's still one meeting. Then the rescue: the agent with slack takes the at-risk hours. | "1:1 booked · Arjun ↔ Priya · Fri 6 pm" → 🟢 |
| **Bonus · Class mode** | `!class 40`: a TA forms balanced teams for a whole course. | Random vs BuildPact table |

**What stays human on purpose:** choosing who to match, closing the search, the kickoff meeting itself, and every yes. Agents do the legwork (screening, scheduling, note-taking, proposing, negotiating, nudging), but people decide.

## How agents resolve conflicts

```
⚔️ Conflict detected  →  1️⃣ Agents trade offers  →  2️⃣ Neutral mediator  →  3️⃣ Students decide
```

- **Private valuations.** Each agent scores every possible package with its own student's scores: role preference and skill fit, plus how much they value each perk (presenting the demo, first credit, first pick of next tasks). The scores never leave the agent.
- **Offers and concessions.** An agent only proposes or accepts packages above its current *aspiration* (100% → 75% → 55% of its ideal), so it concedes a little each round. It may **reject and counter on its own**, but only the student can **accept**.
- **What crosses between agents:** the offered packages, plus priorities the student *chose* to share ("cares most about presenting"). Never the numbers.
- **Mediator.** After 4 offers, or when a student asks, a mediator picks the **Pareto-optimal package that lifts the worse-off side as high as possible** (egalitarian rule, ties broken by Nash product). It sees both valuations and publishes only percentages.
- **Fairness proof.** Every deal shows both satisfactions, a Pareto-optimality check, and how close the worse-off side is to the best balance possible.

## Privacy by design

| Shared on the table | Kept private by the student's agent |
|---|---|
| Teaser, fit %, "compatible / not a fit" | *Why* you're not a fit |
| Stage-2 details → shortlist only | Real weekly hours (only "≥ minimum") |
| Offers, shared priorities, satisfaction % | Raw valuation scores |
| "Commitment that day (details private)" | Busy-day reasons ("midterm", "campus job") |
| Check-in status: ✅ / ⚠️ / 🛑 | What you said in the check-in |
| Full plan → signed team only | — |

A **leak scanner** checks every public message against every private fact (busy reasons, check-in notes, screening reasons) and reports the count live on the dashboard. The test suite asserts it stays at zero.

**When students are asked to confirm:** revealing more of an idea · accepting an offer · agreeing to a mediator proposal · signing the charter · taking a task outside your role · confirming your task list · taking on a teammate's hours in a rescue · joining as a replacement. No agent can commit a student by itself.

## Architecture

```
 Student DM (plain English) ─► LLM: extract profile / offer reply / check-in ─┐
                                                                              ▼
 Buttons (yes/no) ─────────────────────────────────────────► Negotiation engine (rules, no LLM decisions)
                                                              ├─ matching.py     private screening, role optimiser
                                                              ├─ negotiation.py  packages, aspirations, mediator, fairness
                                                              ├─ scheduling.py   capacity, busy days, dependencies
                                                              ├─ classmode.py    balanced teams for a whole class
                                                              └─ engine.py       state machine + rescue + leak scan
                                                                      │
                                       ┌──────────────────────────────┼──────────────────────────┐
                                       ▼                              ▼                          ▼
                                #buildpact (public)          DMs with buttons            Live dashboard
```

**The LLM never decides anything.** It turns casual texts into structured data, writes task breakdowns, and voices the simulated students. Every decision (screening, assignment, offers, mediation, scheduling, rescue) is plain, testable code, so an agent can't leak a secret or invent an agreement. If the LLM is down, everything falls back to keyword parsing and the demo keeps working.

## Run it

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt   # includes sounddevice for !record
python sim.py --quiet        # the whole story in your terminal, no keys needed
python sim.py --quiet --replay=docs/replay.json   # regenerate the website replay
cp .env.example .env         # add DISCORD_TOKEN and OPENAI_API_KEY (or GEMINI_API_KEY)
python bot.py                # Discord bot + dashboard at http://localhost:8765
pytest -q                    # 16 tests
```

**Discord setup:** create an app at discord.com/developers. On the *Bot* tab, enable **Message Content** and **Server Members** intents. Invite the bot to the hub with `bot` scope, and create `#buildpact` and `#project-board` channels. Team servers are created by the owner during the demo; the bot's "Add BuildPact" link grants it admin on *that* server only, so it can build the channels. If it's already in the server, type `!home` there.

### Table commands

| Command | What it does |
|---|---|
| `!demo` | Reset; post 2 practice projects to the board; add 5 practice students who reply in character |
| `!record` · `!stop` | In the team server: the laptop mic records the meeting (announced first), OpenAI transcribes it, and the mediator posts notes + next steps. Each person confirms their own action items before they join the plan. |
| `!standup` · `!notes …` · upload audio | Other ways to feed a meeting: text stand-up, pasted notes, or a recording file |
| `!advance 3` | Jump 3 days ahead; agents check in and rescue at-risk work |
| `!class 40` | TA mode: balanced teams for a 40-student class |
| `!plan` · `!reset` · `!dashboard` · `!help` | Show the plan · restart · dashboard link · help |

In a DM: talk normally, or use `status` and `I need to drop out`.

## Next steps

- Join the Discord voice call directly as a note-taker (today: `!record` on a laptop mic or upload a recording; Discord's newer voice encryption makes in-call listening unreliable for bots).
- Create the team server from a template in one tap (Discord restricts bots from creating servers today).
- Read real availability from Google Calendar and deadlines from Canvas (Cubbie already has both).
- Let each student bring their own agent (e.g. their Cubbie in iMessage) to the same board via an open protocol.
- Run each agent as a Cubbie Skill inside iMessage.
- Learn valuations from past choices instead of asking.
- Contribution reports for peer evaluation (promised vs delivered hours).
