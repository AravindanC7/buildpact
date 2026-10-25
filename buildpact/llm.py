"""LLM layer: turns casual texts into structured data.

The LLM never decides anything. It only (1) extracts a profile/idea from what a
student types and (2) proposes a task breakdown. All decisions happen in the
engine with plain rules, so agents can't leak secrets or invent agreements.

Providers: gemini (default, free tier), anthropic, openai, or "offline".
Offline mode uses a simple parser so the demo still works with no key at all.
"""
from __future__ import annotations

import json
import os
import re

import httpx

from .models import AMBITIONS



def provider() -> str:
    """Read at call time so a .env loaded after import still counts."""
    if os.getenv("LLM_PROVIDER"):
        return os.getenv("LLM_PROVIDER")
    if os.getenv("OPENAI_API_KEY"):
        return "openai"
    return "gemini" if os.getenv("GEMINI_API_KEY") else "offline"


def model() -> str:
    return os.getenv("LLM_MODEL", {"gemini": "gemini-3.1-flash-lite", "openai": "gpt-4.1-mini",
                                    "anthropic": "claude-haiku-4-5"}.get(provider(), ""))


PROFILE_PROMPT = """You are the private agent of a university student on BuildPact, a tool
that forms project teams. Extract what the student just told you into JSON.
Only include fields the message actually states or clearly implies. Never invent.

Schema (all keys optional):
{
  "name": str,
  "skills": {skill_lowercase: level 1-5},          // guess 3 if level not given
  "wanted_roles": [str],                            // most wanted first, e.g. "ML Lead", "Frontend"
  "ambition": [one or more of %s],
  "hours_per_week": number,
  "busy_days": [weekday like "wed" or ISO date],
  "busy_reasons": {busy_day: short private reason},
  "idea": {                                         // ONLY if they describe their own project idea
     "title": str,
     "teaser": str,      // <= 12 words, intriguing but reveals no method or secret sauce
     "details": str,     // 2-3 sentences: the problem and the approach
     "full_plan": str,   // 3-5 sentences: architecture, data, differentiator
     "roles_needed": [{"name": str, "skills": [str]}],  // 2-4 roles INCLUDING the owner's own
     "ambition": [one or more of %s],
     "min_hours": number,   // weekly hours each teammate must give
     "weeks": int,
     "must_have": [str],    // ONLY skills the owner says teammates MUST have
     "screening_question": str   // ONLY if the owner wants to ask candidates something (e.g. "share a GitHub link")
  },
  "requirements": {"must_have": [str], "screening_question": str}   // if an owner adds screening rules later
}
Current profile (for context, don't repeat unchanged fields): %s
Student message: %s
Return only the JSON object."""

TASKS_PROMPT = """Break milestone 1 of this student project into 5-9 concrete tasks.
Project: %s
Plan and kickoff-meeting notes: %s
Team roles (assign each task to exactly one of these role names): %s
Milestone: %s, due in %d days. Each member gives about %g hrs/week.
Return JSON: {"tasks": [{"id": "t1", "title": str (<= 8 words), "role": one of the role names,
"est_hours": number, "depends_on": [ids]}]}. Include one final integration task for the
first role. Keep total hours realistic for the time available."""


NEGOTIATION_PROMPT = """You are a student's private negotiation agent. The student just replied,
in their own words, to an offer about who gets a project role.
Offer on the table: %s
Student's reply: %s

Extract JSON:
{
 "decision": "accept" | "counter" | "mediator" | "leave",   // accept only if they clearly agree; "mediator" if they want a neutral third party / to settle it fairly
 "priorities": {"present": "high"|"low", "credit": "high"|"low", "pick": "high"|"low"},   // only perks they mention.
        // present = presenting the final demo, credit = first name on README/write-up credits, pick = first pick of next milestone's tasks
 "protect": {"present": "high", ...},   // perks they want to KEEP for themselves rather than give away
 "busy_days": ["thu"],                  // weekdays (mon..sun) or ISO dates they can't work
 "busy_reasons": {"thu": "short private reason"}
}
Only include keys that the reply supports. Return only JSON."""

CHECKIN_PROMPT = """You are a student's private project agent doing a progress check-in.
Their open tasks: %s
Student's reply: %s
Extract JSON: {"progress": {"<task id>": percent_done_0_to_100}, "availability": 0.0-1.0 (how much of their
normal time they have before the deadline; 1.0 = normal, 0.5 = swamped), "status": "on_track"|"behind"|"blocked",
"private_reason": "short reason, never shared"}. Return only JSON."""

SIM_PROMPT = """You are role-playing a university student in a live demo of BuildPact, a tool where AI agents
negotiate group-project roles. Stay in character; reply like a real student texting their own private agent:
casual, 1-2 short sentences, no emojis overload, no quotation marks.
Your character: %s
Your agent just sent you: %s
What you want to do: %s
Reply as the student:"""


MEETING_PROMPT = """You are the neutral mediator for a student project team. Below is their kickoff meeting
(a transcript, pasted notes, or each person's stand-up). Team members: %s. Project roles: %s.
Project: %s.

Return JSON:
{
 "summary": "2-3 sentences",
 "people": {"<member name exactly as listed>": {"wants_role": one of the project roles or null,
            "interests": [short phrases], "strengths": [short phrases]}},
 "decisions": [short phrases],
 "action_items": [{"who": member name, "what": short task}],
 "scope": "one sentence: what Milestone 1 should deliver"
}
Only use what was said. Do not include private reasons someone gives for being busy.
Meeting:
%s"""


async def summarize_meeting(material: str, members: list[str], roles: list[str], project: str) -> dict:
    if provider() != "offline":
        try:
            return await _call(MEETING_PROMPT % (", ".join(members), ", ".join(roles), project, material[:12000]))
        except Exception as e:
            print(f"[llm] meeting summary failed ({e!r}); using keywords")
    return meeting_offline(material, members, roles)


def meeting_offline(material: str, members: list[str], roles: list[str]) -> dict:
    """Keyword fallback: per speaker, pick the role whose specific words they mention, preferring the
    last one mentioned (people give background first, then say what they want)."""
    generic = {"lead", "person", "dev", "developer", "engineer", "the", "and"}
    alias = {"ml": ["ml", "machine learning", "model", "embedding"], "frontend": ["frontend", "ui", "design"],
             "data": ["data", "pipeline", "etl"], "product": ["product", "pilot", "pm"],
             "research": ["research", "paper"], "backend": ["backend", "api", "server"]}
    people = {}
    for line in material.splitlines():
        name, _, said = line.partition(":")
        name = name.strip()
        if name not in members:
            continue
        low = said.lower()
        best, best_pos = None, -1
        for r in roles:
            words = [w for w in re.split(r"[\s/]+", r.lower()) if w and w not in generic]
            keys = [k for w in words for k in alias.get(w, [w])]
            pos = max((m.start() for k in keys for m in re.finditer(rf"\b{re.escape(k)}\b", low)), default=-1)
            if pos > best_pos:
                best, best_pos = r, pos
        people[name] = {"wants_role": best, "interests": [said.strip()[:70]], "strengths": []}
    return {"summary": f"Kickoff with {len(people) or len(members)} members; everyone shared intros and interests.",
            "people": people, "decisions": ["Agents check in every 3 days", "Milestone 1 = working prototype"],
            "action_items": [], "scope": "A working prototype of the core flow"}


async def transcribe(audio: bytes, filename: str) -> str | None:
    """Speech-to-text for an uploaded meeting recording (OpenAI)."""
    key = os.getenv("OPENAI_API_KEY")
    if not key:
        return None
    try:
        async with httpx.AsyncClient(timeout=180) as client:
            r = await client.post("https://api.openai.com/v1/audio/transcriptions",
                                  headers={"Authorization": f"Bearer {key}"},
                                  data={"model": os.getenv("TRANSCRIBE_MODEL", "gpt-4o-mini-transcribe")},
                                  files={"file": (filename, audio)})
            r.raise_for_status()
            return r.json().get("text", "")
    except Exception as e:
        print(f"[llm] transcription failed ({e!r})")
        return None


async def _text_call(prompt: str) -> str:
    """Plain-text completion (for in-character replies)."""
    PROVIDER, MODEL = provider(), model()
    async with httpx.AsyncClient(timeout=30) as client:
        if PROVIDER == "openai":
            r = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}"},
                json={"model": MODEL, "temperature": 0.9,
                      "messages": [{"role": "user", "content": prompt}]})
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"].strip().strip('"')
        if PROVIDER == "gemini":
            r = await client.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent",
                headers={"x-goog-api-key": os.environ["GEMINI_API_KEY"]},
                json={"contents": [{"role": "user", "parts": [{"text": prompt}]}]})
            r.raise_for_status()
            parts = r.json()["candidates"][0]["content"]["parts"]
            return "".join(p.get("text", "") for p in parts if not p.get("thought")).strip()
        if PROVIDER == "anthropic":
            r = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={"x-api-key": os.environ["ANTHROPIC_API_KEY"], "anthropic-version": "2023-06-01"},
                json={"model": MODEL, "max_tokens": 200, "messages": [{"role": "user", "content": prompt}]})
            r.raise_for_status()
            return r.json()["content"][0]["text"].strip()
    raise RuntimeError("offline")


async def parse_negotiation(offer: str, reply: str) -> dict:
    if provider() != "offline":
        try:
            return await _call(NEGOTIATION_PROMPT % (offer, reply))
        except Exception as e:
            print(f"[llm] negotiation parse failed ({e!r}); using keywords")
    return negotiation_offline(reply)


async def parse_checkin(tasks: str, reply: str) -> dict:
    if provider() != "offline":
        try:
            return await _call(CHECKIN_PROMPT % (tasks, reply))
        except Exception as e:
            print(f"[llm] check-in parse failed ({e!r}); using keywords")
    return checkin_offline(reply)


async def sim_reply(character: str, agent_msg: str, intent: str, fallback: str) -> str:
    if provider() != "offline":
        try:
            text = await _text_call(SIM_PROMPT % (character, agent_msg, intent))
            if text:
                return text[:300]
        except Exception as e:
            print(f"[llm] sim reply failed ({e!r}); using script")
    return fallback


DAYS = {"monday": "mon", "tuesday": "tue", "wednesday": "wed", "thursday": "thu", "friday": "fri",
        "saturday": "sat", "sunday": "sun"}


def negotiation_offline(reply: str) -> dict:
    t = reply.lower()
    out: dict = {"priorities": {}}
    if any(w in t for w in ("mediator", "settle", "fair", "neutral")):
        out["decision"] = "mediator"
    elif any(w in t for w in ("i'm out", "leave", "not worth", "quit")):
        out["decision"] = "leave"
    elif re.search(r"\b(deal|ok|okay|fine|sounds good|accept|works for me|agreed)\b", t) and " but " not in t:
        out["decision"] = "accept"
    else:
        out["decision"] = "counter"
    if "present" in t or "demo" in t:
        out["priorities"]["present"] = "high"
    if "credit" in t or "readme" in t or "first name" in t:
        out["priorities"]["credit"] = "high"
    if "first pick" in t or "pick" in t:
        out["priorities"]["pick"] = "high"
    days = [short for full, short in DAYS.items() if full in t or re.search(rf"\b{short}s?\b", t)]
    if days:
        out["busy_days"] = days
    return out


def checkin_offline(reply: str) -> dict:
    t = reply.lower()
    m = re.search(r"(\d{1,3})\s*%", t)
    pct = int(m.group(1)) if m else None
    if any(w in t for w in ("blocked", "stuck", "can't start")):
        return {"status": "blocked", "availability": 0.4, "progress_all": pct or 10, "private_reason": reply[:80]}
    if any(w in t for w in ("behind", "barely", "swamped", "haven't", "not started", "slammed", "midterm")):
        return {"status": "behind", "availability": 0.5, "progress_all": pct or 15, "private_reason": reply[:80]}
    return {"status": "on_track", "availability": 1.0, "progress_all": pct}


async def _call(prompt: str) -> dict:
    PROVIDER, MODEL = provider(), model()
    async with httpx.AsyncClient(timeout=30) as client:
        if PROVIDER == "gemini":
            r = await client.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent",
                headers={"x-goog-api-key": os.environ["GEMINI_API_KEY"]},
                json={"contents": [{"role": "user", "parts": [{"text": prompt}]}],
                      "generationConfig": {"responseMimeType": "application/json",
                                           "thinkingConfig": {"thinkingLevel": "low"}}})
            if r.status_code == 400 and "hinking" in r.text:   # model without thinking levels
                r = await client.post(
                    f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent",
                    headers={"x-goog-api-key": os.environ["GEMINI_API_KEY"]},
                    json={"contents": [{"role": "user", "parts": [{"text": prompt}]}],
                          "generationConfig": {"responseMimeType": "application/json"}})
            r.raise_for_status()
            parts = r.json()["candidates"][0]["content"]["parts"]
            text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
        elif PROVIDER == "anthropic":
            r = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={"x-api-key": os.environ["ANTHROPIC_API_KEY"],
                         "anthropic-version": "2023-06-01"},
                json={"model": MODEL, "max_tokens": 1500,
                      "messages": [{"role": "user", "content": prompt}]})
            r.raise_for_status()
            text = r.json()["content"][0]["text"]
        elif PROVIDER == "openai":
            r = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}"},
                json={"model": MODEL, "response_format": {"type": "json_object"},
                      "messages": [{"role": "user", "content": prompt}]})
            r.raise_for_status()
            text = r.json()["choices"][0]["message"]["content"]
        else:
            raise RuntimeError("offline")
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    return json.loads(text)


async def extract_profile(message: str, current: dict) -> dict:
    if provider() != "offline":
        try:
            amb = ", ".join(AMBITIONS)
            return await _call(PROFILE_PROMPT % (amb, amb, json.dumps(current), message))
        except Exception as e:  # network / quota / bad JSON -> fall back, never crash the demo
            detail = getattr(getattr(e, "response", None), "text", "")[:300]
            print(f"[llm] extract failed ({e!r}) {detail}; using offline parser")
    return parse_offline(message)


async def breakdown_tasks(title: str, plan: str, roles: list[str], milestone: str,
                          days: int, hours: float) -> list[dict] | None:
    if provider() == "offline":
        return None
    try:
        data = await _call(TASKS_PROMPT % (title, plan, roles, milestone, days, hours))
        tasks = data.get("tasks", [])
        valid = [t for t in tasks if t.get("role") in roles and t.get("title")]
        return valid or None
    except Exception as e:
        print(f"[llm] task breakdown failed ({e!r}); using template tasks")
        return None


# ---------------------------------------------------------------- offline parser

def parse_offline(message: str) -> dict:
    """Understands 'key: value' lines, plus a few free-text patterns.

    Example:
        skills: python 5, ml 4, react 2
        roles: ML Lead, Backend
        goal: portfolio, startup
        hours: 8
        busy: wed (midterm)
        idea: AI study buddy | Matches students to... | full plan... | roles: Product=product; ML Lead=ml,python
    """
    out: dict = {}
    for line in message.splitlines():
        if ":" not in line:
            continue
        key, val = (s.strip() for s in line.split(":", 1))
        key = key.lower()
        if key in ("name",):
            out["name"] = val
        elif key.startswith("skill"):
            skills = {}
            for part in val.split(","):
                m = re.match(r"\s*(.+?)\s*(\d)?\s*$", part)
                if m and m.group(1):
                    skills[m.group(1).lower()] = int(m.group(2) or 3)
            out["skills"] = skills
        elif key.startswith("role"):
            out["wanted_roles"] = [r.strip() for r in val.split(",") if r.strip()]
        elif key in ("goal", "goals", "ambition"):
            out["ambition"] = _ambitions(val)
        elif key.startswith("hour"):
            m = re.search(r"\d+(\.\d+)?", val)
            if m:
                out["hours_per_week"] = float(m.group())
        elif key.startswith("busy"):
            days, reasons = [], {}
            for part in val.split(","):
                m = re.match(r"\s*([\w-]+)\s*(?:\((.*)\))?", part)
                if m:
                    day = m.group(1).lower()
                    days.append(day)
                    if m.group(2):
                        reasons[day] = m.group(2)
            out["busy_days"], out["busy_reasons"] = days, reasons
        elif key == "idea":
            out["idea"] = _parse_idea(val)
    # free-text fallbacks
    low = message.lower()
    if "hours_per_week" not in out:
        m = re.search(r"(\d+(?:\.\d+)?)\s*(?:hrs?|hours)", low)
        if m:
            out["hours_per_week"] = float(m.group(1))
    if "ambition" not in out:
        found = _ambitions(low)
        if found:
            out["ambition"] = found
    return out


def _ambitions(text: str) -> list[str]:
    """Whole words only, so 'ASU courses' doesn't count as the goal 'course'."""
    return [a for a in AMBITIONS if re.search(rf"\b{a}\b", text.lower())]


def _parse_idea(val: str) -> dict:
    parts = [p.strip() for p in val.split("|")]
    title = parts[0] if parts else "Untitled project"
    idea = {"title": title, "teaser": title, "details": parts[1] if len(parts) > 1 else title,
            "full_plan": parts[2] if len(parts) > 2 else "", "roles_needed": [],
            "ambition": [], "min_hours": 6, "weeks": 6}
    for p in parts[3:]:
        k, _, v = p.partition(":")
        k = k.strip().lower()
        if k == "roles":
            for chunk in v.split(";"):
                name, _, skills = chunk.partition("=")
                if name.strip():
                    idea["roles_needed"].append(
                        {"name": name.strip(), "skills": [s.strip() for s in skills.split(",") if s.strip()]})
        elif k == "teaser":
            idea["teaser"] = v.strip()
        elif k in ("goal", "ambition"):
            idea["ambition"] = _ambitions(v)
        elif k.startswith("min"):
            idea["min_hours"] = float(re.search(r"\d+", v).group())
        elif k == "weeks":
            idea["weeks"] = int(re.search(r"\d+", v).group())
        elif k == "must":
            idea["must_have"] = [x.strip().lower() for x in v.split(",") if x.strip()]
        elif k == "ask":
            idea["screening_question"] = v.strip()
    return idea
