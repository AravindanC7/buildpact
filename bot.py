"""BuildPact Discord bot.

* DM the bot  -> you're talking to YOUR personal agent (private).
* #buildpact  -> the negotiation table where agents talk to each other (public).
* Buttons in DMs are your yes/no; no agent commits you without one.

Channel commands: !reset (restart the demo, keeps profiles), !plan, !seed [names], !help
Run:  python bot.py   (needs DISCORD_TOKEN in .env; GEMINI_API_KEY optional)
"""
from __future__ import annotations

import asyncio
import os

from dotenv import load_dotenv

load_dotenv()

import discord  # noqa: E402

from buildpact import llm  # noqa: E402
from buildpact.engine import Engine  # noqa: E402
from buildpact.llm import parse_offline  # noqa: E402
from buildpact.models import DM, Channel, Out  # noqa: E402
from buildpact.personas import AUTOPILOT_PREFS, SEEKERS  # noqa: E402

CHANNEL_NAME = os.getenv("BUILDPACT_CHANNEL", "buildpact")
STYLES = {"primary": discord.ButtonStyle.primary, "success": discord.ButtonStyle.success,
          "danger": discord.ButtonStyle.danger, "secondary": discord.ButtonStyle.secondary}

WELCOME = """👋 I'm your **personal BuildPact agent**. Everything you tell me here stays private.
I find you a project team, negotiate roles and deadlines with other students' agents, and never commit you without your yes.

Tell me about yourself in plain words, e.g.
> I'm strong in Python and ML (5/5), okay at React. I want to be ML lead on a portfolio or startup project, I can do about 8 hours a week, and I have a midterm Wednesday.

**Have an idea?** Describe it too: what it is, who it's for, the roles you need, and how many hours a week teammates should give. I'll only ever post a one-line teaser until you approve more.

Commands: `status` · `I need to drop out`"""

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
client = discord.Client(intents=intents)
engine = Engine()
lock = asyncio.Lock()


def table() -> discord.abc.Messageable | None:
    for g in client.guilds:
        ch = discord.utils.get(g.text_channels, name=CHANNEL_NAME)
        if ch:
            return ch
    return None


def chunks(text: str, n: int = 1900):
    while text:
        yield text[:n]
        text = text[n:]


class Buttons(discord.ui.View):
    def __init__(self, buttons):
        super().__init__(timeout=None)
        for b in buttons:
            btn = discord.ui.Button(label=b.label, style=STYLES.get(b.style, STYLES["primary"]),
                                    custom_id=f"bp:{b.action}:{id(self)}")
            btn.callback = self._make_cb(b.action)
            self.add_item(btn)

    def _make_cb(self, action: str):
        async def cb(interaction: discord.Interaction):
            for item in self.children:
                item.disabled = True
            await interaction.response.edit_message(view=self)
            async with lock:
                outs = await run_action(str(interaction.user.id), action)
            await render(outs)
        return cb


async def run_action(uid: str, action: str) -> list[Out]:
    if action == "kickoff" and engine.charter:
        c = engine.charter
        m1 = c.milestones[0]
        tasks = await llm.breakdown_tasks(
            engine.idea.title, engine.idea.full_plan or engine.idea.details,
            sorted(set(c.roles.values())), m1.name, (m1.due - engine.today).days, c.weekly_hours)
        return engine.kickoff(uid, tasks)
    return engine.handle_action(uid, action)


async def render(outs: list[Out]):
    ch = table()
    for o in outs:
        try:
            if isinstance(o, Channel):
                if ch:
                    for part in chunks(o.text):
                        await ch.send(part)
                    await asyncio.sleep(0.6)   # readable pacing for a live demo
            elif isinstance(o, DM) and o.user_id.startswith("sim:"):
                if o.buttons:
                    asyncio.create_task(autopilot(o))
            elif isinstance(o, DM):
                user = client.get_user(int(o.user_id)) or await client.fetch_user(int(o.user_id))
                parts = list(chunks(o.text))
                for i, part in enumerate(parts):
                    last = i == len(parts) - 1
                    await user.send(part, view=Buttons(o.buttons) if (last and o.buttons) else None)
        except discord.HTTPException as e:
            print(f"[discord] failed to send: {e}")


async def autopilot(o: DM):
    """A simulated student taps a sensible button after a short pause."""
    await asyncio.sleep(2.5)
    pick = next((b for pref in AUTOPILOT_PREFS for b in o.buttons if b.action.startswith(pref)), o.buttons[0])
    ch = table()
    if ch:
        await ch.send(f"*({engine.name(o.user_id)} taps “{pick.label}”)*")
    async with lock:
        outs = await run_action(o.user_id, pick.action)
    await render(outs)


@client.event
async def on_ready():
    print(f"BuildPact online as {client.user} · LLM provider: {llm.provider()} {llm.model()}")
    print(f"Negotiation table: #{CHANNEL_NAME} -> {'found' if table() else 'NOT FOUND - create it'}")


@client.event
async def on_message(msg: discord.Message):
    if msg.author.bot:
        return
    uid = str(msg.author.id)
    text = msg.content.strip()

    if isinstance(msg.channel, discord.DMChannel):
        async with lock:
            if uid not in engine.profiles and len(text) < 25:
                await msg.channel.send(WELCOME)
                engine.update_profile(uid, msg.author.display_name, {})
                return
            outs = engine.handle_text(uid, text) if uid in engine.profiles else None
            if outs is None:
                async with msg.channel.typing():
                    p = engine.profiles.get(uid)
                    current = {} if not p else {
                        "skills": p.skills, "wanted_roles": p.wanted_roles,
                        "ambition": sorted(p.ambition), "hours_per_week": p.hours_per_week,
                        "has_idea": p.is_owner}
                    data = await llm.extract_profile(text, current)
                outs = engine.update_profile(uid, msg.author.display_name, data)
        await render(outs)
        return

    if getattr(msg.channel, "name", None) == CHANNEL_NAME and text.startswith("!"):
        async with lock:
            if text == "!reset":
                outs = engine.reset()
            elif text.startswith("!seed"):
                names = text.split()[1:] or ["arjun", "leo", "sam"]
                outs = []
                for key in names:
                    if key in SEEKERS:
                        n, t = SEEKERS[key]
                        outs += engine.update_profile(f"sim:{key}", f"{n} (sim)", parse_offline(t))
            elif text == "!plan":
                outs = [Channel(None, engine.plan_table() if engine.tasks else "No plan yet.")]
            elif text == "!help":
                outs = [Channel(None, "DM me to talk to your personal agent. This channel is where agents "
                                      "negotiate. `!reset` restarts the demo, `!plan` shows the task plan, `!seed arjun leo sam` adds autopilot students.")]
            else:
                return
        await render(outs)


if __name__ == "__main__":
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        raise SystemExit("Set DISCORD_TOKEN in .env (see .env.example)")
    client.run(token)
