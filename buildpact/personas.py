"""Demo personas. Used by sim.py and by the bot's !seed command (autopilot students)."""

OWNER_ARAV = """skills: product 4, python 4, backend 3
roles: Product Lead, Backend
goal: startup, portfolio
hours: 10
idea: StudyCrew AI | Students study alone and stall. An AI buddy that turns your Canvas deadlines into daily study sprints and pairs you with classmates on the same topic. | RAG over course materials + a matching model on topic embeddings; iMessage-first; pilot with 3 ASU courses. | roles: Product Lead=product,python; ML Lead=ml,python; Data Pipeline=data,python; Frontend=frontend,design | teaser: An AI study buddy that makes studying social | goal: startup, portfolio | min: 6 | weeks: 6"""

SEEKERS = {
    "priya": ("Priya", "skills: ml 5, python 5, data 3\nroles: ML Lead\ngoal: portfolio\nhours: 10\nbusy: wed (midterm), thu (midterm)"),
    "arjun": ("Arjun", "skills: ml 3, python 4, data 4\nroles: ML Lead, Data Pipeline\ngoal: startup, portfolio\nhours: 8"),
    "maya":  ("Maya", "skills: frontend 4, design 4, js 3\nroles: Frontend\ngoal: portfolio\nhours: 7"),
    "leo":   ("Leo", "skills: frontend 3, design 2, js 3\nroles: Frontend\ngoal: portfolio, hackathon\nhours: 7"),
    "sam":   ("Sam", "skills: research 4, stats 4\nroles: Research\ngoal: paper\nhours: 3"),
}

# Which button an autopilot student taps, in order of preference.
AUTOPILOT_PREFS = ("counter", "swapno", "accept", "sign", "confirm", "moveyes", "join", "reveal")
