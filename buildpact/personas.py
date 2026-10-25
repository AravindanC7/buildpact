"""Demo personas. Used by sim.py and by the bot's !seed command (autopilot students)."""

OWNER_ARAV = """skills: product 4, python 4, backend 3
roles: Product Lead, Backend
goal: startup, portfolio
hours: 10
idea: StudyCrew AI | Students study alone and stall. An AI buddy that turns your Canvas deadlines into daily study sprints and pairs you with classmates on the same topic. | RAG over course materials + a matching model on topic embeddings; iMessage-first; pilot with 3 ASU courses. | roles: Product Lead=product,python; ML Lead=ml,python; Data Pipeline=data,python; Frontend=frontend,design | teaser: An AI study buddy that makes studying social | goal: startup, portfolio | min: 6 | weeks: 6 | ask: Share a link to a past project or your GitHub (one line is fine)"""

SEEKERS = {
    "priya": ("Priya", "skills: ml 5, python 5, data 3\nroles: ML Lead\ngoal: portfolio\nhours: 10\nbusy: wed (midterm), thu (midterm)"),
    "arjun": ("Arjun", "skills: ml 3, python 4, data 4\nroles: ML Lead, Data Pipeline\ngoal: startup, portfolio\nhours: 8"),
    "maya":  ("Maya", "skills: frontend 4, design 4, js 3\nroles: Frontend\ngoal: portfolio\nhours: 7"),
    "leo":   ("Leo", "skills: frontend 3, design 2, js 3\nroles: Frontend\ngoal: portfolio, hackathon\nhours: 7"),
    "sam":   ("Sam", "skills: research 4, stats 4\nroles: Research\ngoal: paper\nhours: 3"),
}

# Which button an autopilot student taps, in order of preference.
AUTOPILOT_PREFS = ("medyes", "counter", "accept", "interested", "match", "apply", "browse",
                   "oneyes", "onetoone", "vote", "approve", "fuadd", "sign", "confirm", "moveyes", "join", "reveal")

# In-character behaviour for simulated students. `intent` steers the LLM; `say` is the
# scripted fallback used offline or if the LLM call fails.
SIM_CHARACTERS = {
    "arjun": {
        "character": "Arjun, MS CS student. Decent at ML, strongest at data engineering. Really wanted to be "
                     "ML lead. Loves presenting. Works a campus job on Thursdays.",
        "offer": [
            {"intent": "Push back: you really wanted ML lead, but you'd take Data Pipeline if you get to present "
                       "the final demo. Mention you can't work Thursdays because of your campus job.",
             "say": "ugh I really wanted ML lead 😕 ok I could do data pipeline, but only if I get to present the "
                    "final demo. also I can't do thursdays, campus job"},
            {"intent": "You're going back and forth. Ask for the neutral mediator to settle it fairly.",
             "say": "we're going in circles lol, can we just get the mediator to settle this fairly?"},
            {"intent": "Agree to the deal.", "say": "ok deal, works for me"},
        ],
        "checkin": {"intent": "Say you're on track with your data pipeline tasks.",
                    "say": "pipeline's going fine, on track for the deadline"},
        "screen": {"intent": "Answer the screening question in one line with a plausible GitHub link and one project.",
                   "say": "github.com/arjun-builds — built an ETL pipeline over ASU dining data last semester"},
        "standup": {"intent": "Kickoff intro: you're Arjun, strongest at data engineering, but you really want to be "
                              "ML lead and grow in ML; you also love presenting demos. 2 sentences.",
                    "say": "Hey, I'm Arjun — mostly data engineering so far, but I'd really like to be ML lead and "
                           "grow there. Also happy to present the demo, I love that part."},
    },
    "priya": {
        "character": "Priya, strong ML student building her portfolio. Wants to stay ML lead. Has midterms "
                     "Wednesday and Thursday.",
        "offer": [{"intent": "Accept, as long as you stay ML lead.", "say": "works for me as long as I keep ML lead"}],
        "checkin": {"intent": "Say you're on track with the model work.",
                    "say": "on track, baseline model is coming along"},
        "screen": {"intent": "Answer the screening question in one line with a plausible GitHub link and an ML project.",
                   "say": "github.com/priya-ml — course-recommendation model with embeddings, 0.81 NDCG"},
        "standup": {"intent": "Kickoff intro: you're Priya, ML is your strength, you want to own the embedding/"
                              "matching model as ML lead and learn RAG evaluation. 2 sentences.",
                    "say": "Hi! I'm Priya, ML is my thing — I'd love to lead the embedding and matching model, and "
                           "I want to get better at evaluating RAG."},
    },
    "maya": {
        "character": "Maya, frontend and design student. Overcommitted this week.",
        "offer": [{"intent": "Accept.", "say": "sounds good"}],
        "checkin": {"intent": "Admit you're behind: you've barely started the chat UI because midterms are "
                              "crushing you this week. Sound stressed but casual.",
                    "say": "honestly I've barely started the chat UI 😩 midterms are crushing me this week"},
        "screen": {"intent": "Answer the screening question in one line with a portfolio link and a UI project.",
                   "say": "maya.design/work — designed + built a React study planner, 300 users at ASU"},
        "standup": {"intent": "Kickoff intro: you're Maya, frontend and design, you want to own the chat UI and "
                              "the iMessage-style experience. 2 sentences.",
                    "say": "Hey I'm Maya — frontend + design. I'd love to own the chat UI and make it feel like "
                           "iMessage."},
    },
    "leo": {"character": "Leo, frontend student who loves hackathons.",
            "offer": [{"intent": "Accept.", "say": "yeah I'm in"}],
            "checkin": {"intent": "Say you're on track.", "say": "all good, on track"},
            "screen": {"intent": "Answer briefly with a link.", "say": "github.com/leo-hacks — 3 hackathon UIs"},
            "standup": {"intent": "Short intro as Leo, frontend.", "say": "Leo here, frontend, happy to help on UI."}},
    "sam": {"character": "Sam, research-minded stats student.",
            "offer": [{"intent": "Accept.", "say": "fine by me"}],
            "checkin": {"intent": "Say you're on track.", "say": "on track"}},
}
GENERIC = {"character": "a busy university student",
           "offer": [{"intent": "Accept.", "say": "ok, works for me"}],
           "checkin": {"intent": "Say you're on track.", "say": "on track"},
           "screen": {"intent": "Answer the screening question in one line.", "say": "github.com/student — a class project"},
           "standup": {"intent": "Give a short friendly intro.", "say": "Hi! Happy to help wherever needed."}}

# Practice idea owners whose projects sit on the board (for the people → project direction).
OWNERS = {
    "noor": ("Noor", """skills: product 4, backend 4, js 3
roles: Product Lead
goal: startup, portfolio
hours: 9
idea: CampusCart | Move-out week: students dump furniture while freshmen buy new. A campus-only marketplace with pickup scheduling. | Next.js + Postgres, ASU SSO, pickup slots, sustainability stats for the dorms. | roles: Product Lead=product; Backend=backend,python; Frontend=frontend,design | teaser: A campus-only marketplace for move-out week | goal: startup, portfolio, hackathon | min: 5 | weeks: 5"""),
    "omar": ("Omar", """skills: research 5, ml 3, stats 4
roles: Research Lead
goal: paper
hours: 6
idea: ThesisLens | Grad students drown in papers. An assistant that maps a literature review into claims, methods and gaps. | Citation graph + LLM claim extraction, evaluated on 3 ASU labs' reading lists; workshop paper target. | roles: Research Lead=research; ML Engineer=ml,python; Evaluation=stats,research | teaser: Turn a pile of papers into a map of claims and gaps | goal: paper | min: 3 | weeks: 8 | must: stats"""),
}
# Which invites a simulated seeker turns down (shows that saying no is private and painless).
DECLINES = {"leo": "StudyCrew"}
# How simulated reviewers respond to a PR (shows the 1:1 path in the demo).
REVIEW_STYLE = {"priya": "changes"}
