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
     "weeks": int
  }
}
Current profile (for context, don't repeat unchanged fields): %s
Student message: %s
Return only the JSON object."""

TASKS_PROMPT = """Break milestone 1 of this student project into 5-9 concrete tasks.
Project: %s
Plan: %s
Team roles (assign each task to exactly one of these role names): %s
Milestone: %s, due in %d days. Each member gives about %g hrs/week.
Return JSON: {"tasks": [{"id": "t1", "title": str (<= 8 words), "role": one of the role names,
"est_hours": number, "depends_on": [ids]}]}. Include one final integration task for the
first role. Keep total hours realistic for the time available."""


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
    return idea
