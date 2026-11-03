"""Timetable cog: view your class schedule and see what's on now / next.

The schedule lives in ``data/schedule.json`` and is re-read on every command,
so edits (by hand or through ``!setclass``) apply instantly, no restart needed.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone, tzinfo
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

import discord
from discord import app_commands
from discord.ext import commands

log = logging.getLogger("timetable_bot.timetable")

DATA_FILE = Path(__file__).resolve().parent.parent / "data" / "schedule.json"
DEFAULT_TZ_NAME = "Asia/Kolkata"
IST = timezone(timedelta(hours=5, minutes=30), "IST")  # fallback if tzdata is missing

COLOR_PRIMARY = 0x5865F2
COLOR_SUCCESS = 0x57F287
COLOR_WARN = 0xFEE75C
COLOR_ERROR = 0xED4245
COLOR_MUTED = 0x4F545C

DAY_KEYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
DAY_NAMES = {
    "mon": "Monday",
    "tue": "Tuesday",
    "wed": "Wednesday",
    "thu": "Thursday",
    "fri": "Friday",
    "sat": "Saturday",
    "sun": "Sunday",
}
_DAY_ALIASES: dict[str, str] = {}
for _key, _name in DAY_NAMES.items():
    _DAY_ALIASES[_key] = _key
    _DAY_ALIASES[_name.lower()] = _key
_DAY_ALIASES.update({"tues": "tue", "weds": "wed", "thur": "thu", "thurs": "thu"})

DAY_CHOICES = [
    ("Today", "today"),
    ("Tomorrow", "tomorrow"),
    ("Monday", "mon"),
    ("Tuesday", "tue"),
    ("Wednesday", "wed"),
    ("Thursday", "thu"),
    ("Friday", "fri"),
    ("Saturday", "sat"),
    ("Sunday", "sun"),
]

KIND_ICONS = {
    "lecture": "📖",
    "tutorial": "✏️",
    "lab": "🧪",
    "practical": "🧪",
    "seminar": "🎤",
    "workshop": "🛠️",
}

_SLOT_RE = re.compile(
    r"^\s*(?P<start>\d{1,2}:\d{2})\s*(?:-|–|—|to)\s*(?P<end>\d{1,2}:\d{2})\s*$",
    re.IGNORECASE,
)
_KIND_RE = re.compile(r"^(?P<name>.+?)\s*\((?P<kind>[^()]+)\)\s*$")
_FREE_WORDS = frozenset(
    {"free", "free period", "lunch", "lunch / free", "lunch/free", "break"}
)


# --------------------------------------------------------------------------- #
# Errors & small helpers
# --------------------------------------------------------------------------- #
class ScheduleError(Exception):
    """A problem the user should be told about (bad input, unreadable file...)."""

    @property
    def user_message(self) -> str:
        return str(self)


def resolve_day(text: str, now: Optional[datetime] = None) -> Optional[str]:
    """Turn 'tue', 'Tuesday', 'today', 'tomorrow'... into a day key (or None)."""
    word = text.strip().lower().rstrip(".")
    if word in _DAY_ALIASES:
        return _DAY_ALIASES[word]
    if now is not None:
        if word in ("today", "now"):
            return DAY_KEYS[now.weekday()]
        if word in ("tomorrow", "tmr", "tmrw"):
            return DAY_KEYS[(now.weekday() + 1) % 7]
    return None


def _parse_hhmm(value: object) -> time:
    return datetime.strptime(str(value).strip(), "%H:%M").time()


def _minutes(t: time) -> int:
    return t.hour * 60 + t.minute


def _fmt_minutes(total: int) -> str:
    hours, mins = divmod(total, 60)
    if hours and mins:
        return f"{hours}h {mins}m"
    return f"{hours}h" if hours else f"{mins}m"


def _raw_sort_key(raw: object) -> time:
    try:
        return _parse_hhmm(raw["start"])  # type: ignore[index]
    except (KeyError, TypeError, ValueError):
        return time.max


# --------------------------------------------------------------------------- #
# Slot model
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Slot:
    start: time
    end: time
    category: str  # "class" | "free"
    subject: str = ""
    kind: str = ""
    room: str = ""
    faculty: str = ""
    label: str = ""

    @property
    def is_free(self) -> bool:
        return self.category == "free"

    @property
    def span(self) -> str:
        return f"{self.start:%H:%M} – {self.end:%H:%M}"

    @property
    def minutes(self) -> int:
        return _minutes(self.end) - _minutes(self.start)

    @property
    def title(self) -> str:
        return (self.label or "Free period") if self.is_free else self.subject

    @classmethod
    def from_dict(cls, raw: object) -> "Slot":
        if not isinstance(raw, dict):
            raise ValueError("slot must be a JSON object")
        try:
            start = _parse_hhmm(raw["start"])
            end = _parse_hhmm(raw["end"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"invalid start/end time ({exc})") from exc
        if end <= start:
            raise ValueError("end must be after start")
        category = str(raw.get("category", "class")).strip().lower()
        if category not in ("class", "free"):
            raise ValueError(f"unknown category {category!r}")
        subject = str(raw.get("subject", "")).strip()
        if category == "class" and not subject:
            raise ValueError("class slots need a subject")
        return cls(
            start=start,
            end=end,
            category=category,
            subject=subject,
            kind=str(raw.get("kind", "")).strip(),
            room=str(raw.get("room", "")).strip(),
            faculty=str(raw.get("faculty", "")).strip(),
            label=str(raw.get("label", "")).strip(),
        )

    def block(self, *, quote: bool = True, ongoing: bool = False) -> str:
        """Render the slot as Discord markdown. Free periods are muted subtext."""
        if self.is_free:
            here = "  ·  *you are here*" if ongoing else ""
            return f"-# ▫️ {self.span}  ·  {self.title.capitalize()}{here}"
        icon = KIND_ICONS.get(self.kind.lower(), "📚")
        kind = f"  ·  `{self.kind}`" if self.kind else ""
        badge = "   🟢 **ONGOING**" if ongoing else ""
        lines = [f"⏰ **{self.span}**{badge}", f"{icon} **{self.subject}**{kind}"]
        if self.room:
            lines.append(f"📍 {self.room}")
        if self.faculty:
            lines.append(f"🎓 {self.faculty}")
        return "\n".join(f"> {line}" for line in lines) if quote else "\n".join(lines)


# --------------------------------------------------------------------------- #
# Embed builders
# --------------------------------------------------------------------------- #
def error_embed(message: str, title: str = "⚠️ Something's off") -> discord.Embed:
    return discord.Embed(title=title, description=message, color=COLOR_ERROR)


def _next_class_day(days: dict[str, list[Slot]], key: str) -> Optional[tuple[str, Slot]]:
    """First class on the next day (after ``key``) that has any classes."""
    idx = DAY_KEYS.index(key)
    for step in range(1, 8):
        nxt = DAY_KEYS[(idx + step) % 7]
        for slot in days.get(nxt, []):
            if not slot.is_free:
                return nxt, slot
    return None


def _empty_day_embed(key: str, days: dict[str, list[Slot]], now: datetime) -> discord.Embed:
    name = DAY_NAMES[key]
    if key in ("sat", "sun"):
        embed = discord.Embed(
            title=f"🎉 {name} — no classes",
            description="It's the weekend. Rest, recharge, maybe touch some grass. 🌿",
            color=COLOR_SUCCESS,
        )
    else:
        embed = discord.Embed(
            title=f"📭 {name} — nothing scheduled",
            description="There are no classes in the timetable for this day.",
            color=COLOR_MUTED,
        )
    upcoming = _next_class_day(days, key)
    if upcoming:
        nkey, slot = upcoming
        embed.add_field(
            name="⏭️ Next classes",
            value=f"**{DAY_NAMES[nkey]}** — first up: {slot.subject} at `{slot.start:%H:%M}`",
            inline=False,
        )
    embed.set_footer(text=f"Times in {now.tzname() or 'local time'}")
    return embed


def build_day_embed(
    key: str, days: dict[str, list[Slot]], now: datetime, *, highlight_now: bool
) -> discord.Embed:
    slots = days.get(key, [])
    classes = [s for s in slots if not s.is_free]
    if not classes:
        return _empty_day_embed(key, days, now)

    t = now.time()
    blocks = [
        slot.block(ongoing=highlight_now and slot.start <= t < slot.end) for slot in slots
    ]
    total = sum(s.minutes for s in classes)
    noun = "class" if len(classes) == 1 else "classes"
    summary = (
        f"🗓️ **{len(classes)} {noun}**  ·  ⏳ {_fmt_minutes(total)} of teaching  ·  "
        f"🕗 {classes[0].start:%H:%M} → {classes[-1].end:%H:%M}"
    )
    description = summary + "\n\n" + "\n\n".join(blocks)
    if len(description) > 4000:
        description = description[:3990] + "\n…"

    suffix = " — Today" if highlight_now else ""
    embed = discord.Embed(
        title=f"📅 {DAY_NAMES[key]}{suffix}",
        description=description,
        color=COLOR_PRIMARY,
        timestamp=now,
    )
    embed.set_author(name="Class Timetable")
    embed.set_footer(text=f"Times in {now.tzname() or 'local time'}")
    return embed


# --------------------------------------------------------------------------- #
# Cog
# --------------------------------------------------------------------------- #
class TimetableCog(commands.Cog):
    """Show the timetable and what's happening now / next."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.lock = asyncio.Lock()
        self._tz_warned = False

    # ----- time ------------------------------------------------------------ #
    def _tz(self) -> tzinfo:
        name = str(getattr(self.bot, "config", {}).get("timezone", DEFAULT_TZ_NAME))
        try:
            return ZoneInfo(name)
        except (KeyError, ValueError, OSError):
            if not self._tz_warned:
                log.warning("Timezone %r unavailable (is tzdata installed?). Using IST.", name)
                self._tz_warned = True
            return IST

    def _now(self) -> datetime:
        return datetime.now(self._tz())

    # ----- storage (re-read from disk on every call) ----------------------- #
    def read_raw(self) -> dict[str, list]:
        try:
            raw = json.loads(DATA_FILE.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise ScheduleError("I couldn't find `data/schedule.json`.") from None
        except json.JSONDecodeError as exc:
            raise ScheduleError(
                f"`schedule.json` has a syntax error (line {exc.lineno}, column {exc.colno})."
            ) from exc
        except (OSError, UnicodeDecodeError) as exc:
            raise ScheduleError(f"I couldn't read `schedule.json` ({type(exc).__name__}).") from exc
        if not isinstance(raw, dict):
            raise ScheduleError("`schedule.json` must be a JSON object keyed by day (mon, tue...).")

        data: dict[str, list] = {}
        for key, slots in raw.items():
            k = str(key).lower()
            if k in DAY_KEYS and isinstance(slots, list):
                data[k] = slots
        for k in DAY_KEYS[:5]:
            data.setdefault(k, [])
        return data

    def write_raw(self, data: dict[str, list]) -> None:
        ordered = {k: data[k] for k in DAY_KEYS if k in data}
        DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=DATA_FILE.parent, prefix=".schedule-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(ordered, fh, indent=2, ensure_ascii=False)
                fh.write("\n")
            os.replace(tmp, DATA_FILE)  # atomic: never leaves a half-written file
        except Exception as exc:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            if isinstance(exc, OSError):
                raise ScheduleError(f"I couldn't save the schedule ({type(exc).__name__}).") from exc
            raise

    def read_all(self) -> dict[str, list[Slot]]:
        """Parsed + sorted slots for every day. Malformed slots are skipped and logged."""
        parsed: dict[str, list[Slot]] = {k: [] for k in DAY_KEYS}
        for key, entries in self.read_raw().items():
            for i, raw in enumerate(entries):
                try:
                    parsed[key].append(Slot.from_dict(raw))
                except ValueError as exc:
                    log.warning("Skipping bad slot #%d on %s: %s", i, key, exc)
            parsed[key].sort(key=lambda s: s.start)
        return parsed

    # ----- editing API (used by the admin cog) ----------------------------- #
    async def set_slot(
        self, day: str, time_slot: str, subject: str, room: str = "", faculty: str = ""
    ) -> tuple[str, Slot]:
        """Insert or replace (same start time) a slot. Returns ('added'|'updated', slot)."""
        key = resolve_day(day)
        if key is None:
            raise ScheduleError(f"`{day}` isn't a valid day. Use mon, tue, wed, thu, fri, sat or sun.")
        match = _SLOT_RE.match(time_slot)
        if not match:
            raise ScheduleError("Time slot must look like `09:30-10:30` (24-hour clock).")
        try:
            start, end = _parse_hhmm(match["start"]), _parse_hhmm(match["end"])
        except ValueError:
            raise ScheduleError("That isn't a valid time. Use 24-hour `HH:MM`.") from None
        if end <= start:
            raise ScheduleError("The end time must be after the start time.")

        subject, room, faculty = subject.strip(), room.strip(), faculty.strip()
        if not subject:
            raise ScheduleError("The subject can't be empty.")
        if len(subject) > 100 or len(room) > 50 or len(faculty) > 80:
            raise ScheduleError("Subject (100), room (50) or faculty (80) is too long.")

        entry: dict[str, str] = {"start": f"{start:%H:%M}", "end": f"{end:%H:%M}"}
        if subject.lower() in _FREE_WORDS:
            label = "FREE PERIOD" if subject.lower() in ("free", "free period") else subject
            entry.update(category="free", label=label)
        else:
            name, kind = subject, ""
            m = _KIND_RE.match(subject)
            if m:
                name, kind = m["name"].strip(), m["kind"].strip()
            entry.update(category="class", subject=name, kind=kind, room=room, faculty=faculty)
        new_slot = Slot.from_dict(entry)

        async with self.lock:
            data = self.read_raw()
            kept: list = []
            action = "added"
            for raw in data.setdefault(key, []):
                try:
                    existing = Slot.from_dict(raw)
                except ValueError:
                    kept.append(raw)  # leave hand-edited oddities untouched
                    continue
                if existing.start == new_slot.start:
                    action = "updated"
                    continue
                if existing.start < new_slot.end and new_slot.start < existing.end:
                    raise ScheduleError(
                        f"That overlaps **{existing.title}** (`{existing.span}`). "
                        "Remove it first with `delclass`."
                    )
                kept.append(raw)
            kept.append(entry)
            kept.sort(key=_raw_sort_key)
            data[key] = kept
            self.write_raw(data)
        return action, new_slot

    async def delete_slot(self, day: str, start_text: str) -> Slot:
        key = resolve_day(day)
        if key is None:
            raise ScheduleError(f"`{day}` isn't a valid day. Use mon, tue, wed, thu, fri, sat or sun.")
        try:
            start = _parse_hhmm(start_text)
        except ValueError:
            raise ScheduleError("Give the slot's start time as `HH:MM`, e.g. `09:30`.") from None

        async with self.lock:
            data = self.read_raw()
            kept: list = []
            removed: Optional[Slot] = None
            for raw in data.setdefault(key, []):
                try:
                    existing = Slot.from_dict(raw)
                except ValueError:
                    kept.append(raw)
                    continue
                if removed is None and existing.start == start:
                    removed = existing
                    continue
                kept.append(raw)
            if removed is None:
                raise ScheduleError(f"No slot starts at `{start:%H:%M}` on {DAY_NAMES[key]}.")
            data[key] = kept
            self.write_raw(data)
        return removed

    # ----- embeds ---------------------------------------------------------- #
    def _status_embed(self, now: datetime) -> discord.Embed:
        days = self.read_all()
        key = DAY_KEYS[now.weekday()]
        today = days[key]
        t = now.time()
        tz = now.tzinfo

        current = next((s for s in today if s.start <= t < s.end), None)
        upcoming: Optional[tuple[int, date, Slot]] = None
        for offset in range(8):
            d = now.date() + timedelta(days=offset)
            for slot in days[DAY_KEYS[d.weekday()]]:
                if slot.is_free or (offset == 0 and slot.start <= t):
                    continue
                upcoming = (offset, d, slot)
                break
            if upcoming:
                break

        def ts(d: date, at: time) -> int:
            return int(datetime.combine(d, at, tzinfo=tz).timestamp())

        embed = discord.Embed(
            title="🧭 Class Status",
            description=f"It's **{now:%A, %H:%M}** right now.",
            color=COLOR_PRIMARY,
            timestamp=now,
        )
        if current is not None:
            end_ts = ts(now.date(), current.end)
            if current.is_free:
                embed.color = COLOR_MUTED
                embed.add_field(
                    name=f"☕ {current.title.capitalize()}",
                    value=f"Ends <t:{end_ts}:R> (<t:{end_ts}:t>)",
                    inline=False,
                )
            else:
                embed.color = COLOR_SUCCESS
                embed.add_field(
                    name="🟢 Ongoing now",
                    value=f"{current.block(quote=False)}\n⏳ Ends <t:{end_ts}:R> (<t:{end_ts}:t>)",
                    inline=False,
                )
        else:
            if not today:
                note = "No classes are scheduled today."
            elif t >= today[-1].end:
                note = "Classes are done for today. 🎉"
            elif t < today[0].start:
                note = "Your day hasn't started yet."
            else:
                note = "You're between slots right now."
            embed.add_field(name="💤 Nothing on right now", value=note, inline=False)

        if upcoming is not None:
            offset, d, slot = upcoming
            when = (
                "Today"
                if offset == 0
                else "Tomorrow"
                if offset == 1
                else f"{DAY_NAMES[DAY_KEYS[d.weekday()]]}, {d:%d %b}"
            )
            start_ts = ts(d, slot.start)
            embed.add_field(
                name=f"⏭️ Up next · {when}",
                value=f"{slot.block(quote=False)}\n🚀 Starts <t:{start_ts}:R> (<t:{start_ts}:t>)",
                inline=False,
            )
        else:
            embed.add_field(
                name="⏭️ Up next",
                value="No upcoming classes found in your schedule.",
                inline=False,
            )
        embed.set_footer(text=f"Times in {now.tzname() or 'local time'}")
        return embed

    # ----- commands -------------------------------------------------------- #
    @commands.hybrid_command(
        name="timetable",
        aliases=["tt", "day"],
        description="Show your class timetable for a day (defaults to today).",
    )
    @app_commands.describe(day="mon–sun, 'today' or 'tomorrow'. Leave empty for today.")
    @app_commands.choices(day=[app_commands.Choice(name=n, value=v) for n, v in DAY_CHOICES])
    async def timetable(self, ctx: commands.Context, day: Optional[str] = None) -> None:
        now = self._now()
        today_key = DAY_KEYS[now.weekday()]
        if day is None or not day.strip():
            key = today_key
        else:
            key = resolve_day(day, now)
            if key is None:
                await ctx.send(
                    embed=error_embed(
                        f"I don't know the day `{day[:30]}`.\nTry `mon`, `tue`, `wed`, "
                        "`thu`, `fri`, `today` or `tomorrow`."
                    ),
                    ephemeral=True,
                )
                return
        embed = build_day_embed(key, self.read_all(), now, highlight_now=(key == today_key))
        await ctx.send(embed=embed)

    @commands.hybrid_command(
        name="next",
        aliases=["now", "upnext"],
        description="See what class is on right now and what's coming up next.",
    )
    async def next_class(self, ctx: commands.Context) -> None:
        await ctx.send(embed=self._status_embed(self._now()))


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(TimetableCog(bot))
