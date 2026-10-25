# BuildPact 🤝

**Your agent finds your project crew, negotiates the deal, and keeps the team on track — without ever exposing your private life.**

Cubbie Hackathon · Track 2 (Agent-to-Agent Coordination)

## The campus problem

Students with ideas can't find committed teammates, and students who want real projects can't find good ideas. When teams *do* form, it happens on vibes in a Discord channel: nobody aligns on goals, roles, hours or credit, so the team falls apart by week 3. And people don't share ideas openly because they're afraid of having them taken.

## What BuildPact does

Every student gets a **personal agent** (a private Discord DM). Agents meet at a shared negotiation table (`#buildpact`) and take a team from strangers to a working plan:

| Phase | What the agents do | Student's yes needed for |
|---|---|---|
| **1 · Screen** | Owner's agent posts only a one-line **teaser**. Every seeker's agent checks fit **privately** (goal + hours are pass/fail; reasons never posted). | — |
| **2 · Reveal** | With the owner's OK, problem + approach go to the **shortlist only**. | Owner approves stage 2 |
| **3 · Roles** | An optimiser assigns roles (skill fit + preference). Conflicts become **offers with perks**; the loser can accept, ask for a swap, or leave. | Accepting an offer, agreeing to a swap |
| **4 · Charter** | Roles, weekly minimum, milestones, decision rules, credit, drop-out rule. **Full plan unlocks only when everyone signs.** | Signing |
| **5 · Kick off** | Milestone 1 → tasks → assigned by charter role → scheduled around each student's busy days (reasons private) → backwards from the due date. | Taking a task outside your role; confirming your list |
| **6 · Adapt** | "Too much this week" hands off a task; a drop-out triggers the charter rule and invites the next-best match from the waitlist. | Taking the handed-off task; joining |

## How the agents handle the three things judges asked for

**Conflicting constraints:** incompatible goals (startup vs paper), hours below the minimum, two people wanting the same role, a midterm in the middle of the sprint, task dependencies, overload, and drop-outs.

**What they share (and what they never share):**

| Shared on the table | Kept private by your agent |
|---|---|
| Teaser; fit %; "compatible / not a fit" | *Why* you're not a fit |
| Stage 2 details → shortlist only | Your real weekly hours (only "≥ the minimum" is public) |
| Skill fit for a contested role | Busy-day reasons ("midterm", "family stuff") |
| Charter, task plan, due dates | Why you're dropping out or overloaded |
| Full plan → signed team only | — |

**When they ask the student:** before revealing more of an idea, before accepting a role trade or swap, before signing, before taking a task outside your role, before confirming a plan, and before joining as a replacement. No agent can commit a student by itself.

## Architecture

```
 Student DM ─► LLM extraction ─► Profile (private) ─┐
 (casual text)   (Gemini, or                        │
                  offline parser)                   ▼
                                          ┌─────────────────────┐
  Buttons (yes/no) ─────────────────────► │  Negotiation engine │ ──► #buildpact (public table)
                                          │  state machine      │ ──► DMs with buttons (private)
                                          │  matching.py        │
                                          │  scheduling.py      │
                                          └─────────────────────┘
```

**The LLM never decides anything.** It only turns casual texts into structured profiles and proposes a task breakdown. Screening, role assignment, disclosure, scheduling and every commitment are plain rules in the engine, so an agent **can't leak a secret or hallucinate an agreement**, and every outcome is explainable.

- `buildpact/models.py`: profiles, ideas, charter, tasks, and the DM/Channel messages the engine emits
- `buildpact/matching.py`: private screening, utility = 0.6·skill fit + 0.4·preference, exhaustive role assignment, conflict detection
- `buildpact/scheduling.py`: role-based task assignment, per-day capacity, busy days, dependencies, move proposals
- `buildpact/engine.py`: the protocol and state machine (POOL → SCREENING → SHORTLISTED → ROLES → CHARTER → SIGNED → TASKS → RUNNING)
- `buildpact/llm.py`: Gemini / Anthropic / OpenAI over HTTPS, falling back to an offline parser on any error
- `bot.py`: the Discord transport; `sim.py`: the same demo in a terminal

## Run it

```bash
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env        # add DISCORD_TOKEN, and GEMINI_API_KEY (optional)
python sim.py               # full demo in the terminal, no keys needed
python bot.py               # the real thing on Discord
pytest -q                   # tests
```

**Discord setup:** create an app at discord.com/developers. On the *Bot* tab, copy the token and enable **Message Content** and **Server Members** intents. Under *OAuth2 → URL Generator*, pick `bot` with Send Messages + Read Message History + Embed Links, then invite it to your server. Create a `#buildpact` channel.

**No LLM key?** It still works. Send profile lines like:
```
skills: python 5, ml 4
roles: ML Lead, Data Pipeline
goal: portfolio, startup
hours: 8
busy: wed (midterm)
```

## Live demo (about 2 minutes)

1. Each person DMs the bot and describes themselves in plain words. One person also describes an idea. Short on people? Type `!seed arjun leo sam` in `#buildpact` to add autopilot students (`priya`, `maya` are also available).
2. The owner taps **Find my crew**. The table shows compatible agents and a silent "not a fit" (only that student learns why).
3. The owner taps **Reveal stage 2**. The role proposal appears, then **⚔️ Conflict** and an offer with perks. The loser taps *Ask for a swap*; the winner keeps the role; the loser accepts.
4. Everyone **signs**. The full plan unlocks in DMs.
5. The owner taps **Kick off Milestone 1**. Tasks are scheduled around a midterm ("commitment that day, details private"). Someone taps **Too much this week** and a teammate takes the task. Everyone confirms, and the plan is posted.
6. Bonus: someone DMs "I need to drop out". The charter rule fires, the waitlisted student is invited, joins, and tasks are re-planned.

`!reset` restarts the demo (keeps profiles) · `!plan` shows the plan · `status` in a DM shows your role and tasks.

## Next steps

Real calendar and Canvas availability (Cubbie already has both), multiple teams in parallel, embedding-based idea matching, and running the same agents as Cubbie Skills inside iMessage.
