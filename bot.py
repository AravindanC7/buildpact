"""BuildPact Discord bot.

* DM the bot  -> you're talking to YOUR personal agent (private). Plain English works everywhere:
                 your profile, replies to offers ("fine, but I want to present"), progress check-ins.
* #project-board -> listings: each idea owner's agent posts a one-line card (create this channel).
* #buildpact  -> the negotiation table: handshakes, opt-ins, offers, mediator (public).
* Team server -> once the owner closes the search, they create a server and add the bot; the bot builds
                 #general #standup #meetings #prs and DMs every teammate an invite.
* Buttons     -> your yes/no; no agent commits you without one.
* Meetings    -> !record / !stop in the team server: the laptop mic records, the bot transcribes, and the
                 mediator posts notes + next steps (each person confirms their own action items).
* Dashboard   -> http://localhost:8765 — live view of every agent's private vault vs. what went public.

Table commands:
  !demo                 reset and add five simulated students (Priya, Arjun, Maya, Leo, Sam)
  !seed priya arjun ... add specific simulated students
  !advance 3            jump 3 days ahead; agents check in and rescue at-risk work
  !class 40             TA mode: form balanced teams for a 40-student class
  !plan · !reset · !dashboard · !help
Run:  python bot.py   (needs DISCORD_TOKEN in .env; OPENAI_API_KEY or GEMINI_API_KEY optional)
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).with_name(".env"))

import discord  # noqa: E402
from aiohttp import web  # noqa: E402  (installed with discord.py)

from buildpact import llm  # noqa: E402
from buildpact.engine import Engine  # noqa: E402
from buildpact.recorder import Recorder  # noqa: E402
from buildpact.llm import parse_offline  # noqa: E402
from buildpact.models import DM, Channel, Out, Room  # noqa: E402
from buildpact.personas import (AUTOPILOT_PREFS, DECLINES, GENERIC, OWNERS, REVIEW_STYLE,  # noqa: E402
                                SEEKERS, SIM_CHARACTERS)

CHANNEL_NAME = os.getenv("BUILDPACT_CHANNEL", "buildpact")
BOARD_NAME = os.getenv("BUILDPACT_BOARD", "project-board")
PORT = int(os.getenv("DASHBOARD_PORT", "8765"))
SIM_DELAY = float(os.getenv("SIM_DELAY", "2.5"))
STYLES = {"primary": discord.ButtonStyle.primary, "success": discord.ButtonStyle.success,
          "danger": discord.ButtonStyle.danger, "secondary": discord.ButtonStyle.secondary}

WELCOME = """👋 I'm your **personal BuildPact agent**. Everything you tell me here stays private.
I find you a project team, negotiate roles and deadlines with other students' agents, check in when things get busy, and never commit you without your yes.

Tell me about yourself in plain words, e.g.
> I'm strong in Python and ML (5/5), okay at React. I want to be ML lead on a portfolio or startup project, I can do about 8 hours a week, and I have a midterm Wednesday.

**Have an idea?** Describe it too: what it is, the roles you need, and how many hours a week teammates should give. I'll only ever post a one-line teaser until you approve more.

Commands: `status` · `I need to drop out`"""

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
client = discord.Client(intents=intents)
engine = Engine()
lock = asyncio.Lock()
sim_turns: dict[str, int] = {}
_summarizing = False
recorder = Recorder()
tracker_msg: discord.Message | None = None
tracker_last = ""
AUDIO_EXT = (".m4a", ".mp3", ".wav", ".ogg", ".webm", ".mp4", ".mpeg", ".mpga", ".flac")
project_channel_id: int | None = None
project_guild_id: int | None = None
pending_home: dict | None = None
TEAM_CHANNELS = [("tracker", "Live project tracker — kept up to date by the agents"),
                 ("general", "Team chat + announcements from your agents"),
                 ("standup", "Kickoff stand-up and quick updates"),
                 ("meetings", "Upload meeting recordings here; agendas and notes land here"),
                 ("prs", "Hand-offs, PR reviews and 1:1 requests")]
_dashboard_started = False


def table() -> discord.abc.Messageable | None:
    for g in client.guilds:
        ch = discord.utils.get(g.text_channels, name=CHANNEL_NAME)
        if ch:
            return ch
    return None


def board() -> discord.abc.Messageable | None:
    for g in client.guilds:
        ch = discord.utils.get(g.text_channels, name=BOARD_NAME)
        if ch:
            return ch
    return table()


def room_channel(room: str | None):
    if room == "board":
        return board()
    if room == "meetings" and project_guild_id:
        g = client.get_guild(project_guild_id)
        ch = discord.utils.get(g.text_channels, name="meetings") if g else None
        if ch:
            return ch
    if room in ("project", "meetings") and project_channel_id:
        return client.get_channel(project_channel_id) or table()
    return table()


def hub_guild_id() -> int | None:
    ch = table()
    return ch.guild.id if ch else None


def add_bot_link() -> str:
    app_id = client.user.id if client.user else os.getenv("DISCORD_CLIENT_ID", "<your-app-id>")
    return (f"https://discord.com/oauth2/authorize?client_id={app_id}"
            "&scope=bot%20applications.commands&permissions=8")


async def prompt_home(r: Room):
    """Remember the team; the owner will create a server and add the bot to it."""
    global pending_home
    pending_home = {"slug": r.slug if hasattr(r, "slug") else r.name, "members": r.members, "text": r.text}
    owner = engine.owner_id
    if owner and owner.isdigit():
        user = client.get_user(int(owner)) or await client.fetch_user(int(owner))
        await user.send(f"➕ **Add BuildPact to your new server:**\n{add_bot_link()}\n"
                        "Pick the server you just created → Authorize. Already added me there? Type `!home` "
                        "in any channel of that server.")


async def setup_home(guild: discord.Guild):
    """Build the team's server: channels, intro, invites for every real teammate."""
    global project_channel_id, project_guild_id, pending_home
    if not pending_home or guild.id == hub_guild_id():
        return
    home = pending_home
    pending_home = None
    try:
        category = discord.utils.get(guild.categories, name=engine.idea.title) or \
            await guild.create_category(engine.idea.title)
        chans = {}
        for name, topic in TEAM_CHANNELS:
            chans[name] = discord.utils.get(guild.text_channels, name=name, category=category) or \
                await guild.create_text_channel(name, category=category, topic=topic)
        project_channel_id, project_guild_id = chans["general"].id, guild.id
        await chans["general"].send(home["text"])
        invite = await chans["general"].create_invite(max_age=0, max_uses=0, unique=False,
                                                      reason="BuildPact team invite")
        for uid in home["members"]:
            if uid.isdigit() and uid != engine.owner_id:
                try:
                    user = client.get_user(int(uid)) or await client.fetch_user(int(uid))
                    await user.send(f"🏠 Your team **{engine.idea.title}** has its own server now — join here:\n"
                                    f"{invite.url}")
                except discord.HTTPException:
                    pass
        print(f"[home] set up team server {guild.name}: {invite.url}")
    except (discord.Forbidden, discord.HTTPException) as e:
        print(f"[home] could not set up {guild.name}: {e}")
        ch = table()
        if ch:
            await ch.send("🏠 I joined the team server but couldn't create channels — re-add me with the link "
                          "(it grants admin on *that* server only). Continuing in the hub.")
        project_guild_id = None
        pending_home = home
        return
    async with lock:
        outs = engine.home_ready(guild.name)
    await render(outs)


@client.event
async def on_guild_join(guild: discord.Guild):
    await setup_home(guild)


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
            btn.callback = self._make_cb(b.action, b.label)
            self.add_item(btn)

    def _make_cb(self, action: str, label: str):
        async def cb(interaction: discord.Interaction):
            for item in self.children:
                item.disabled = True
            await interaction.response.edit_message(view=self)
            uid = str(interaction.user.id)
            engine.log_private(uid, "student", f"[tapped {label}]")
            async with lock:
                outs = await run_action(uid, action)
            await render(outs)
        return cb


async def run_action(uid: str, action: str) -> list[Out]:
    if action == "kickoff" and engine.charter:
        c = engine.charter
        m1 = c.milestones[0]
        notes = engine.notes or {}
        context = (engine.idea.full_plan or engine.idea.details) + (
            f"\nKickoff notes: {notes.get('summary', '')} Scope: {notes.get('scope', '')} "
            f"Action items: {notes.get('action_items', [])}" if notes else "")
        tasks = await llm.breakdown_tasks(
            engine.idea.title, context,
            sorted(set(c.roles.values())), m1.name, (m1.due - engine.today).days, c.weekly_hours)
        return engine.kickoff(uid, tasks)
    return engine.handle_action(uid, action)


async def render(outs: list[Out]):
    for o in outs:
        try:
            if isinstance(o, Room):
                await prompt_home(o)
            elif isinstance(o, Channel):
                ch = room_channel(o.room)
                if ch:
                    for part in chunks(o.text):
                        await ch.send(part)
                    await asyncio.sleep(0.7)   # readable pacing for a live demo
            elif isinstance(o, DM):
                engine.log_private(o.user_id, "agent", o.text)
                if o.user_id.startswith("sim:"):
                    if o.buttons or o.meta.get("kind") in ("screening", "standup"):
                        asyncio.create_task(autopilot(o))
                    continue
                user = client.get_user(int(o.user_id)) or await client.fetch_user(int(o.user_id))
                parts = list(chunks(o.text))
                for i, part in enumerate(parts):
                    last = i == len(parts) - 1
                    await user.send(part, view=Buttons(o.buttons) if (last and o.buttons) else None)
        except discord.HTTPException as e:
            print(f"[discord] failed to send: {e}")
    await update_tracker()
    await maybe_summarize()


def tracker_reset():
    global tracker_msg, tracker_last
    tracker_msg, tracker_last = None, ""


def tracker_channel():
    if project_guild_id:
        g = client.get_guild(project_guild_id)
        ch = discord.utils.get(g.text_channels, name="tracker") if g else None
        if ch:
            return ch
    return table()


async def update_tracker():
    """One live message the agents keep editing: the whole team sees the same, current picture."""
    global tracker_msg, tracker_last
    text = engine.tracker_text()
    if not text or text == tracker_last:
        return
    tracker_last = text
    ch = tracker_channel()
    if not ch:
        return
    try:
        if tracker_msg and tracker_msg.channel.id == ch.id:
            await tracker_msg.edit(content=text[:1990])
        else:
            tracker_msg = await ch.send(text[:1990])
            try:
                await tracker_msg.pin()
            except discord.HTTPException:
                pass
    except discord.HTTPException as e:
        print(f"[tracker] {e}")


async def maybe_summarize():
    """When the kickoff meeting has all its input, the mediator writes notes and proposes roles."""
    global _summarizing
    if _summarizing or not engine.meeting.get("ready"):
        return
    _summarizing = True
    try:
        members = [engine.name(u) for u in engine.meeting.get("members", [])]
        roles = [r.name for r in engine.idea.roles_needed]
        notes = await llm.summarize_meeting(engine.meeting_material(), members, roles, engine.idea.title)
        async with lock:
            outs = engine.apply_meeting_notes(notes)
    finally:
        _summarizing = False
    await render(outs)


async def handle_transcript(channel, text: str, source: str):
    """Kickoff meeting → roles; any later meeting → notes + next steps."""
    if not text.strip():
        await channel.send("The recording was empty — nothing to summarize.")
        return
    if engine.meeting.get("open"):
        async with lock:
            outs = engine.meeting_transcript(text, source)
        await render(outs)
        return
    if engine.charter:
        members = [engine.name(u) for u in engine.charter.roles]
        roles = sorted(set(engine.charter.roles.values()))
        notes = await llm.summarize_meeting(text, members, roles, engine.idea.title)
        kind = "1:1" if source == "1:1" else "meeting"
        async with lock:
            outs = engine.meeting_followup(notes, kind)
        await render(outs)
        return
    await channel.send("No team meeting to attach this to yet.")


async def record_command(msg: discord.Message, cmd: str):
    if cmd == "!record":
        try:
            recorder.start(msg.author.display_name)
        except RuntimeError as e:
            await msg.channel.send(f"🎙️ {e}")
            return
        await msg.channel.send(f"🔴 **Recording started** by {msg.author.display_name}. Everyone in this meeting: "
                               "BuildPact is listening to take notes — say so now if you'd rather it didn't. "
                               "Type `!stop` to finish.")
        return
    try:
        audio = recorder.stop()
    except RuntimeError as e:
        await msg.channel.send(f"🎙️ {e}")
        return
    secs = len(audio) / (16000 * 2)
    length = f"{secs / 60:.1f} min" if secs >= 60 else f"{secs:.0f} s"
    await msg.channel.send(f"⏹️ Recording stopped ({length}). Transcribing…")
    transcript = await llm.transcribe(audio, "meeting.wav")
    if not transcript:
        await msg.channel.send("Couldn't transcribe (needs OPENAI_API_KEY). Paste notes with `!notes ...`.")
        return
    preview = transcript[:300] + ("…" if len(transcript) > 300 else "")
    await msg.channel.send(f"📄 Transcript ({len(transcript.split())} words): “{preview}”")
    await handle_transcript(msg.channel, transcript, "recording")


# ------------------------------------------------------------ free-text answers
async def answer_in_words(uid: str, text: str) -> list[Out] | None:
    """Route a plain-English reply to whatever the student's agent is waiting for."""
    waiting = engine.awaiting(uid)
    if not waiting:
        return None
    kind, ref = waiting
    if kind == "offer":
        data = await llm.parse_negotiation(engine.offer_text(ref), text)
        async with lock:
            return engine.negotiation_text(uid, data)
    if kind == "screening":
        async with lock:
            return engine.screening_answer(uid, text)
    if kind == "standup":
        async with lock:
            return engine.meeting_input(uid, text)
    if kind == "checkin":
        tasks = "; ".join(f"{t.id}: {t.title}" for t in engine.open_tasks(uid))
        data = await llm.parse_checkin(tasks, text)
        async with lock:
            return engine.checkin(uid, data)
    return None


async def autopilot(o: DM):
    """Simulated students: answer in character (LLM) when asked in words, else tap a sensible button."""
    await asyncio.sleep(SIM_DELAY)
    key = o.user_id.split(":", 1)[1]
    who = SIM_CHARACTERS.get(key, GENERIC)
    kind = o.meta.get("kind")
    ch = table()
    if kind in ("offer", "checkin", "screening", "standup"):
        if kind == "offer":
            n = sim_turns.get(key, 0)
            sim_turns[key] = n + 1
            script = who["offer"][min(n, len(who["offer"]) - 1)]
        else:
            k = {"screening": "screen"}.get(kind, kind)
            script = who.get(k) or GENERIC[k]
        reply = await llm.sim_reply(who["character"], o.text, script["intent"], script["say"])
        engine.log_private(o.user_id, "student", reply)
        if ch and kind != "standup":
            await ch.send(f"💬 *{engine.name(o.user_id)} replied to their agent privately* 🔒")
        outs = await answer_in_words(o.user_id, reply)
        await render(outs or [])
        return
    pick = next((b for pref in AUTOPILOT_PREFS for b in o.buttons if b.action.startswith(pref)), o.buttons[0])
    if kind == "review" and REVIEW_STYLE.get(key) == "changes":
        pick = next((b for b in o.buttons if b.action.startswith("changes")), pick)
    if kind == "invite" and key in DECLINES:
        lid = o.meta.get("lid", "")
        if lid in engine.listings and DECLINES[key] in engine.listings[lid]["idea"].title:
            pick = next(b for b in o.buttons if b.action.startswith("notforme"))
    if ch:
        await ch.send(f"*({engine.name(o.user_id)} taps “{pick.label}”)*")
    async with lock:
        outs = await run_action(o.user_id, pick.action)
    await render(outs)


# ------------------------------------------------------------------ dashboard
async def start_dashboard():
    global _dashboard_started
    if _dashboard_started:
        return
    _dashboard_started = True
    page = Path(__file__).parent / "buildpact" / "dashboard.html"
    app = web.Application()
    app.router.add_get("/", lambda r: web.FileResponse(page))
    app.router.add_get("/state", lambda r: web.json_response(engine.snapshot()))
    runner = web.AppRunner(app)
    await runner.setup()
    try:
        await web.TCPSite(runner, "127.0.0.1", PORT).start()
        print(f"Live dashboard: http://localhost:{PORT}")
    except OSError as e:
        print(f"[dashboard] could not start on port {PORT}: {e}")


async def sync_loop(minutes: float):
    """Fixed sync schedule in real time (set SYNC_EVERY_MINUTES); the demo uses !advance instead."""
    while True:
        await asyncio.sleep(minutes * 60)
        if engine.phase.value == "running":
            async with lock:
                outs = engine.advance(0)
            await render(outs)


@client.event
async def on_ready():
    if os.getenv("SYNC_EVERY_MINUTES") and not getattr(client, "_sync_started", False):
        client._sync_started = True
        asyncio.create_task(sync_loop(float(os.environ["SYNC_EVERY_MINUTES"])))
    print(f"BuildPact online as {client.user} · LLM provider: {llm.provider()} {llm.model()}")
    print(f"Negotiation table: #{CHANNEL_NAME} -> {'found' if table() else 'NOT FOUND - create it'}")
    await start_dashboard()


@client.event
async def on_message(msg: discord.Message):
    global project_channel_id, pending_home, project_guild_id
    if msg.author.bot:
        return
    uid = str(msg.author.id)
    text = msg.content.strip()

    if isinstance(msg.channel, discord.DMChannel):
        engine.log_private(uid, "student", text)
        if uid not in engine.profiles and len(text) < 25:
            await msg.channel.send(WELCOME)
            async with lock:
                engine.update_profile(uid, msg.author.display_name, {})
            return
        async with lock:
            outs = engine.handle_text(uid, text) if uid in engine.profiles else None
        if outs is None:
            async with msg.channel.typing():
                outs = await answer_in_words(uid, text)
                if outs is None:
                    p = engine.profiles.get(uid)
                    current = {} if not p else {
                        "skills": p.skills, "wanted_roles": p.wanted_roles,
                        "ambition": sorted(p.ambition), "hours_per_week": p.hours_per_week,
                        "has_idea": p.is_owner}
                    data = await llm.extract_profile(text, current)
                    async with lock:
                        outs = engine.update_profile(uid, msg.author.display_name, data)
        await render(outs)
        return

    in_table = getattr(msg.channel, "name", None) == CHANNEL_NAME
    in_room = (project_guild_id is not None and msg.guild is not None and msg.guild.id == project_guild_id) or \
        (project_channel_id is not None and msg.channel.id == project_channel_id)
    if text.lower() == "!home" and msg.guild and msg.guild.id != hub_guild_id():
        if pending_home and uid == engine.owner_id:
            await setup_home(msg.guild)
        else:
            await msg.channel.send("No team is waiting for a server right now.")
        return
    if not (in_table or in_room):
        return
    low = text.lower()
    if low in ("!record", "!stop") and (in_room or in_table):
        await record_command(msg, low)
        return
    if in_room and not engine.meeting.get("open") and engine.charter:
        audio = next((a for a in msg.attachments if a.filename.lower().endswith(AUDIO_EXT)
                      or (a.content_type or "").startswith(("audio/", "video/"))), None)
        if audio:
            await msg.channel.send(f"🎧 Transcribing **{audio.filename}**…")
            transcript = await llm.transcribe(await audio.read(), audio.filename)
            if transcript:
                await handle_transcript(msg.channel, transcript, "recording")
            else:
                await msg.channel.send("Couldn't transcribe that. Paste notes with `!notes ...`.")
            return
        if low.startswith("!notes"):
            await handle_transcript(msg.channel, text[6:].strip(), "notes")
            return
    # --- kickoff meeting inputs: a recording, pasted notes, or a stand-up answer
    if engine.meeting.get("open") and uid in engine.meeting.get("members", []):
        audio = next((a for a in msg.attachments if a.filename.lower().endswith(AUDIO_EXT)
                      or (a.content_type or "").startswith(("audio/", "video/"))), None)
        if audio:
            await msg.channel.send(f"🎧 Transcribing **{audio.filename}**…")
            transcript = await llm.transcribe(await audio.read(), audio.filename)
            if not transcript:
                await msg.channel.send("Couldn't transcribe that (needs an OpenAI key). Paste notes with `!notes ...`.")
                return
            async with lock:
                outs = engine.meeting_transcript(transcript, "recording")
            await render(outs)
            return
        if text.lower().startswith("!notes"):
            async with lock:
                outs = engine.meeting_transcript(text[6:].strip(), "notes")
            await render(outs)
            return
        if text.lower().startswith("!standup"):
            async with lock:
                outs = engine.handle_action(uid, "standup")
            await render(outs)
            return
        if in_room and not text.startswith("!"):
            async with lock:
                outs = engine.meeting_input(uid, text)
            await render(outs)
            return
    if not text.startswith("!"):
        return
    parts = text.split()
    cmd, args = parts[0].lower(), parts[1:]
    async with lock:
        if cmd == "!reset":
            sim_turns.clear()
            project_channel_id, pending_home, project_guild_id = None, None, None
            tracker_reset()
            outs = engine.reset()
        elif cmd == "!demo":
            sim_turns.clear()
            project_channel_id, pending_home, project_guild_id = None, None, None
            tracker_reset()
            for uid_ in [u for u in engine.profiles if u.startswith("sim:")]:
                engine.profiles.pop(uid_)
            engine.listings.clear()
            outs = engine.reset()
            for key, (n, t) in OWNERS.items():
                outs += engine.seed_owner(f"sim:{key}", f"{n} (sim)", parse_offline(t))
            for key in ["priya", "arjun", "maya", "leo", "sam"]:
                n, t = SEEKERS[key]
                outs += engine.update_profile(f"sim:{key}", f"{n} (sim)", parse_offline(t))
            outs.append(Channel(None, f"🎬 Demo ready: 2 projects on the board, 5 students in the pool. DM me your "
                                      f"idea, then tap **Post to the board & find my crew**. "
                                      f"Live dashboard: http://localhost:{PORT}"))
        elif cmd == "!seed":
            outs = []
            for key in args or ["arjun", "leo", "sam"]:
                if key in SEEKERS:
                    n, t = SEEKERS[key]
                    outs += engine.update_profile(f"sim:{key}", f"{n} (sim)", parse_offline(t))
        elif cmd == "!advance":
            days = int(args[0]) if args and args[0].isdigit() else 3
            outs = engine.advance(days)
        elif cmd == "!class":
            n = int(args[0]) if args and args[0].isdigit() else 40
            size = int(args[1]) if len(args) > 1 and args[1].isdigit() else 4
            outs = engine.form_class(n, size)
        elif cmd in ("!plan", "!tracker"):
            tracker_reset()
            outs = [] if engine.tasks else [Channel(None, "No plan yet — the tracker appears at Milestone 1 kickoff.")]
        elif cmd == "!dashboard":
            outs = [Channel(None, f"📊 Live dashboard (on the presenter's laptop): http://localhost:{PORT}")]
        elif cmd == "!help":
            outs = [Channel(None, "Meetings: `!record` / `!stop` (laptop mic), upload a recording, or `!notes ...`. "
                                  "DM me to talk to your personal agent — plain English works. #project-board "
                                  "lists projects; this channel is where agents handshake and negotiate.\n"
                                  "`!demo` reset + 2 practice projects + 5 practice students · `!advance 3` "
                                  "jump ahead (agents check in) · `!class 40` TA team formation · `!plan` · "
                                  "`!reset` · `!dashboard`")]
        else:
            return
    await render(outs)


if __name__ == "__main__":
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        raise SystemExit("Set DISCORD_TOKEN in .env (see .env.example)")
    client.run(token)
