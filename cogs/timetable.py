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
from typing import Any, Optional
from zoneinfo import ZoneInfo

import discord
try:
    from discord import app_commands
except ImportError:
    class _DummyAppCommands:
        class Choice:
            def __init__(self, name: str, value: Any):
                self.name = name
                self.value = value

        class AppCommandError(Exception):
            pass

        class CheckFailure(Exception):
            pass

        @staticmethod
        def describe(**kwargs):
            return lambda func: func

        @staticmethod
        def choices(**kwargs):
            return lambda func: func

    app_commands = _DummyAppCommands()  # type: ignore
from discord.ext import commands

if not hasattr(commands, "hybrid_command"):
    commands.hybrid_command = commands.command  # type: ignore[attr-defined]

if not hasattr(commands, "hybrid_group"):
    commands.hybrid_group = commands.group  # type: ignore[attr-defined]

_HAS_UI = False
try:
    if hasattr(discord, "ui") and hasattr(discord.ui, "View") and hasattr(discord.ui, "Button"):
        _HAS_UI = True
except Exception:
    _HAS_UI = False

log = logging.getLogger("timetable_bot.timetable")

DATA_FILE = Path(__file__).resolve().parent.parent / "data" / "schedule.json"
CONFIG_FILE = DATA_FILE.parent / "config.json"
USERS_FILE = DATA_FILE.parent / "users.json"
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
    "practical": "🔬",
    "seminar": "🎤",
    "workshop": "🛠️",
    "project": "💻",
}


def make_progress_bar(pct: float, length: int = 10) -> str:
    """Generate a clean visual progress bar: e.g. ▰▰▰▰▱▱▱▱▱▱ 40%"""
    pct = max(0.0, min(1.0, pct))
    filled = int(round(pct * length))
    bar = "▰" * filled + "▱" * (length - filled)
    return f"`{bar}` **{int(pct * 100)}%**"

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


async def _safe_send(ctx: commands.Context, **kwargs) -> Any:
    """Send a message, safely dropping unsupported kwargs (e.g. ephemeral) if rejected."""
    try:
        return await ctx.send(**kwargs)
    except TypeError:
        kwargs.pop("ephemeral", None)
        try:
            return await ctx.send(**kwargs)
        except TypeError:
            kwargs.pop("view", None)
            return await ctx.send(**kwargs)


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


def parse_batches(text: str) -> list[str]:
    """Parse comma/slash separated batch numbers or names. Defaults to ['ALL']."""
    if not text or text.strip().upper() in ("ALL", "*", "ANY"):
        return ["ALL"]
    cleaned = re.sub(r"(?i)\bbatch\s*", "", text)
    parts = [p.strip().upper() for p in re.split(r"[,/&]+", cleaned) if p.strip()]
    return sorted(set(parts)) if parts else ["ALL"]


def batches_overlap(b1: tuple[str, ...] | list[str], b2: tuple[str, ...] | list[str]) -> bool:
    """Check if two batch collections share any batches or if either applies to ALL."""
    if "ALL" in b1 or "ALL" in b2:
        return True
    return bool(set(b1) & set(b2))


def slot_matches_batch(slot: "Slot", query_batch: Optional[str]) -> bool:
    """Check if a slot matches the requested batch."""
    if not query_batch or query_batch.strip().upper() in ("ALL", "*", "ANY"):
        return True
    qb = query_batch.strip().upper()
    return "ALL" in slot.batches or qb in slot.batches


# --------------------------------------------------------------------------- #
# User Profile model
# --------------------------------------------------------------------------- #
@dataclass
class UserProfile:
    user_id: str
    batch: str
    name: str = ""
    roll_no: str = ""
    linked_at: str = ""

    @classmethod
    def from_dict(cls, user_id: str, data: dict[str, Any]) -> "UserProfile":
        return cls(
            user_id=str(user_id),
            batch=str(data.get("batch", "ALL")).strip().upper(),
            name=str(data.get("name", "")).strip(),
            roll_no=str(data.get("roll_no", "")).strip(),
            linked_at=str(data.get("linked_at", "")).strip(),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "batch": self.batch,
            "name": self.name,
            "roll_no": self.roll_no,
            "linked_at": self.linked_at,
        }


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
    batches: tuple[str, ...] = ("ALL",)

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

    @property
    def batch_str(self) -> str:
        if not self.batches or "ALL" in self.batches:
            return "ALL"
        return ", ".join(self.batches)

    @property
    def is_all_batches(self) -> bool:
        return not self.batches or "ALL" in self.batches

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

        raw_batches = raw.get("batches")
        if raw_batches is None:
            raw_batches = raw.get("batch")
        if isinstance(raw_batches, str):
            batches = tuple(parse_batches(raw_batches))
        elif isinstance(raw_batches, (list, tuple)):
            batches = tuple(str(b).strip().upper() for b in raw_batches if str(b).strip()) or ("ALL",)
        else:
            batches = ("ALL",)

        return cls(
            start=start,
            end=end,
            category=category,
            subject=subject,
            kind=str(raw.get("kind", "")).strip(),
            room=str(raw.get("room", "")).strip(),
            faculty=str(raw.get("faculty", "")).strip(),
            label=str(raw.get("label", "")).strip(),
            batches=batches,
        )

    def block(self, *, quote: bool = True, ongoing: bool = False, show_batch: bool = True) -> str:
        """Render the slot as modern Discord markdown with headers and badges."""
        if self.is_free:
            icon = "🍱" if "lunch" in self.title.lower() else "☕"
            here = "  ·  🟢 **CURRENT BREAK**" if ongoing else ""
            batch_tag = f"  ·  👥 Batch {self.batch_str}" if (show_batch and not self.is_all_batches) else ""
            return f"-# {icon} **`{self.span}`**  ·  *{self.title.upper()}*{batch_tag}{here}"

        icon = KIND_ICONS.get(self.kind.lower(), "📚")
        badge = "  🟢 **ONGOING NOW**" if ongoing else ""
        kind_str = f" `[{self.kind}]`" if self.kind else ""
        batch_badge = f" `[Batch {self.batch_str}]`" if (show_batch and not self.is_all_batches) else ""

        header = f"### {icon} `{self.span}` • {self.subject}{kind_str}{batch_badge}{badge}"
        meta: list[str] = []
        if self.room:
            meta.append(f"📍 Room: **{self.room}**")
        if self.faculty:
            meta.append(f"👨‍🏫 Faculty: **{self.faculty}**")
        if show_batch and not self.is_all_batches:
            meta.append(f"👥 Batch: **{self.batch_str}**")

        if meta:
            prefix = "> " if quote else ""
            return f"{header}\n{prefix}{'   •   '.join(meta)}"
        return header


# --------------------------------------------------------------------------- #
# Embed builders
# --------------------------------------------------------------------------- #
def error_embed(message: str, title: str = "⚠️ Timetable Error") -> discord.Embed:
    return discord.Embed(title=title, description=message, color=COLOR_ERROR)


def _next_class_day(
    days: dict[str, list[Slot]], key: str, query_batch: Optional[str] = None
) -> Optional[tuple[str, Slot]]:
    """First class on the next day (after ``key``) matching the optional batch."""
    idx = DAY_KEYS.index(key)
    for step in range(1, 8):
        nxt = DAY_KEYS[(idx + step) % 7]
        for slot in days.get(nxt, []):
            if not slot.is_free and slot_matches_batch(slot, query_batch):
                return nxt, slot
    return None


def _empty_day_embed(
    key: str,
    days: dict[str, list[Slot]],
    now: datetime,
    *,
    highlight_now: bool = False,
    query_batch: Optional[str] = None,
    user_profile: Optional[UserProfile] = None,
) -> discord.Embed:
    name = DAY_NAMES[key]
    is_weekend = key in ("sat", "sun")

    batch_tag = f" • Batch {query_batch.upper()}" if query_batch else ""
    if highlight_now:
        title = f"📅 Today's Timetable — {name} ({now:%d %b}){batch_tag}"
    else:
        title = f"📅 {name}'s Timetable{batch_tag}"

    if is_weekend:
        embed = discord.Embed(
            title=title,
            description=(
                f"> 🌴 **Weekend Break — {name}**\n"
                "> No lectures or labs scheduled today.\n"
                "> Time to relax, recharge, and enjoy your time off! 🌿"
            ),
            color=COLOR_SUCCESS,
            timestamp=now,
        )
    else:
        b_note = f" for **Batch {query_batch.upper()}**" if query_batch else f" for **{name}**"
        embed = discord.Embed(
            title=title,
            description=(
                f"> 📭 **No classes scheduled{b_note}.**\n"
                "> You have a free day! ✨\n\n"
                f"-# 💡 *Need to add classes? Use `/setclass {key} <time> <subject>`.*"
            ),
            color=COLOR_PRIMARY,
            timestamp=now,
        )
    embed.set_author(name="Timetable Bot")

    upcoming = _next_class_day(days, key, query_batch=query_batch)
    if upcoming:
        nkey, slot = upcoming
        icon = KIND_ICONS.get(slot.kind.lower(), "📖")
        kind_str = f" `[{slot.kind}]`" if slot.kind else ""
        room_str = f" in `{slot.room}`" if slot.room else ""
        batch_info = f" (Batch {slot.batch_str})" if not slot.is_all_batches else ""
        embed.add_field(
            name="ℹ️ Next In Schedule",
            value=(
                f"Your next scheduled class is on **{DAY_NAMES[nkey]}** at `{slot.span}`:\n"
                f"> {icon} **{slot.subject}**{kind_str}{batch_info}{room_str}"
            ),
            inline=False,
        )

    if user_profile and query_batch == user_profile.batch:
        footer_text = f"👤 Linked to Batch {user_profile.batch} • Timezone: {now.tzname() or 'Local'} • /profile for details"
    elif query_batch:
        footer_text = f"Viewing Batch {query_batch.upper()} • Timezone: {now.tzname() or 'Local'} • Tip: /link <batch>"
    else:
        footer_text = f"Showing All Batches • Timezone: {now.tzname() or 'Local'} • Tip: /link <batch> to personalize"
    embed.set_footer(text=footer_text)
    return embed


def build_day_embed(
    key: str,
    days: dict[str, list[Slot]],
    now: datetime,
    *,
    highlight_now: bool,
    query_batch: Optional[str] = None,
    user_profile: Optional[UserProfile] = None,
) -> discord.Embed:
    all_slots = days.get(key, [])
    slots = [s for s in all_slots if slot_matches_batch(s, query_batch)]
    classes = [s for s in slots if not s.is_free]
    if not classes:
        return _empty_day_embed(
            key, days, now, highlight_now=highlight_now, query_batch=query_batch, user_profile=user_profile
        )

    t = now.time()
    any_ongoing = highlight_now and any(s.start <= t < s.end and not s.is_free for s in classes)

    blocks = [
        slot.block(
            ongoing=highlight_now and slot.start <= t < slot.end,
            show_batch=(query_batch is None),
        )
        for slot in slots
    ]
    total = sum(s.minutes for s in classes)
    noun = "class" if len(classes) == 1 else "classes"
    batch_summary = f"   •   👥 **Batch {query_batch.upper()}**" if query_batch else ""
    summary = (
        f"> 📊 **{len(classes)} {noun}**   •   ⏳ **{_fmt_minutes(total)} teaching**   •   "
        f"⏰ **{classes[0].start:%H:%M} → {classes[-1].end:%H:%M}**{batch_summary}"
    )
    description = summary + "\n\n" + "\n\n".join(blocks)
    if len(description) > 4000:
        description = description[:3990] + "\n…"

    batch_tag = f" • Batch {query_batch.upper()}" if query_batch else ""
    title = (
        f"📅 Today's Timetable — {DAY_NAMES[key]} ({now:%d %b}){batch_tag}"
        if highlight_now
        else f"📅 {DAY_NAMES[key]}'s Timetable{batch_tag}"
    )
    embed = discord.Embed(
        title=title,
        description=description,
        color=COLOR_SUCCESS if any_ongoing else COLOR_PRIMARY,
        timestamp=now,
    )
    embed.set_author(name="Timetable Bot")

    if user_profile and query_batch == user_profile.batch:
        footer_text = f"👤 Linked to Batch {user_profile.batch} • Timezone: {now.tzname() or 'Local'} • /profile for details"
    elif query_batch:
        footer_text = f"Viewing Batch {query_batch.upper()} • Timezone: {now.tzname() or 'Local'} • Tip: /link <batch>"
    else:
        footer_text = f"Showing All Batches • Timezone: {now.tzname() or 'Local'} • Tip: /link <batch> to personalize"
    embed.set_footer(text=footer_text)
    return embed


def build_week_embed(
    days: dict[str, list[Slot]],
    now: datetime,
    query_batch: Optional[str] = None,
    user_profile: Optional[UserProfile] = None,
) -> discord.Embed:
    today_key = DAY_KEYS[now.weekday()]
    total_classes = 0
    total_minutes = 0

    batch_title = f" • Batch {query_batch.upper()}" if query_batch else ""
    embed = discord.Embed(
        title=f"🗓️ Weekly Timetable Overview{batch_title}",
        color=COLOR_PRIMARY,
        timestamp=now,
    )
    embed.set_author(name="Timetable Bot")

    for key in DAY_KEYS[:5]:  # Mon - Fri
        day_name = DAY_NAMES[key]
        slots = [s for s in days.get(key, []) if slot_matches_batch(s, query_batch)]
        classes = [s for s in slots if not s.is_free]
        total_classes += len(classes)
        total_minutes += sum(s.minutes for s in classes)

        is_today = key == today_key
        indicator = "🟢 " if is_today else "▫️ "
        field_name = f"{indicator}{day_name}" + (" (Today)" if is_today else "")

        if not classes:
            embed.add_field(
                name=field_name,
                value="-# *No classes scheduled*",
                inline=False,
            )
        else:
            lines = []
            for c in classes:
                icon = KIND_ICONS.get(c.kind.lower(), "📖")
                kind_str = f" `[{c.kind}]`" if c.kind else ""
                batch_str = f" `[Batch {c.batch_str}]`" if (query_batch is None and not c.is_all_batches) else ""
                room_str = f" • `{c.room}`" if c.room else ""
                lines.append(f"{icon} `{c.start:%H:%M} – {c.end:%H:%M}` **{c.subject}**{kind_str}{batch_str}{room_str}")
            header = f"`{len(classes)} classes • {classes[0].start:%H:%M} → {classes[-1].end:%H:%M}`"
            embed.add_field(
                name=f"{field_name} — {header}",
                value="\n".join(lines),
                inline=False,
            )

    # Check weekend slots if any
    for key in ("sat", "sun"):
        weekend_classes = [s for s in days.get(key, []) if not s.is_free and slot_matches_batch(s, query_batch)]
        if weekend_classes:
            day_name = DAY_NAMES[key]
            total_classes += len(weekend_classes)
            total_minutes += sum(s.minutes for s in weekend_classes)
            lines = [
                f"{KIND_ICONS.get(c.kind.lower(), '📖')} `{c.start:%H:%M} – {c.end:%H:%M}` **{c.subject}**"
                for c in weekend_classes
            ]
            embed.add_field(
                name=f"📅 **{day_name}** — `{len(weekend_classes)} classes`",
                value="\n".join(lines),
                inline=False,
            )

    noun = "class" if total_classes == 1 else "classes"
    batch_desc = f" for **Batch {query_batch.upper()}**" if query_batch else ""
    embed.description = f"> 📊 **{total_classes} {noun}** this week{batch_desc}   •   ⏳ **{_fmt_minutes(total_minutes)} total teaching time**"

    if user_profile and query_batch == user_profile.batch:
        footer_text = f"👤 Linked to Batch {user_profile.batch} • Timezone: {now.tzname() or 'Local'} • /timetable for day view"
    elif query_batch:
        footer_text = f"Viewing Batch {query_batch.upper()} • Timezone: {now.tzname() or 'Local'} • Tip: /link <batch>"
    else:
        footer_text = f"Showing All Batches • Timezone: {now.tzname() or 'Local'} • Tip: /link <batch> to personalize"
    embed.set_footer(text=footer_text)
    return embed


# --------------------------------------------------------------------------- #
# Cog
# --------------------------------------------------------------------------- #
def _detect_android_timezone() -> Optional[str]:
    """Detect Android system timezone on Termux via getprop."""
    try:
        import subprocess

        res = subprocess.run(
            ["getprop", "persist.sys.timezone"],
            capture_output=True,
            text=True,
            timeout=1,
        )
        tz = res.stdout.strip()
        if tz:
            return tz
    except Exception:
        pass
    return None


if _HAS_UI:
    class BatchButton(discord.ui.Button):
        def __init__(self, batch_name: str, cog: "TimetableCog", author_id: int):
            super().__init__(
                label=f"Batch {batch_name}",
                style=discord.ButtonStyle.primary,
                custom_id=f"tt_batch_{batch_name}_{author_id}",
            )
            self.batch_name = batch_name
            self.cog = cog
            self.author_id = author_id

        async def callback(self, interaction: discord.Interaction):
            if interaction.user.id != self.author_id:
                if hasattr(interaction, "response"):
                    await interaction.response.send_message(
                        "❌ This batch selector is for the user who ran the command. Run `/link` to choose yours!",
                        ephemeral=True,
                    )
                return
            prof = self.cog.set_user_profile(
                user_id=interaction.user.id,
                batch=self.batch_name,
                name=getattr(interaction.user, "display_name", ""),
            )
            embed = discord.Embed(
                title="🎓 Account Linked Successfully!",
                description=(
                    f"Successfully linked {interaction.user.mention} to **Batch {prof.batch}**! 🎉\n\n"
                    "Now whenever you run `/timetable`, `/next`, or `/week`, "
                    f"the bot will automatically load the timetable for **Batch {prof.batch}**."
                ),
                color=COLOR_SUCCESS,
            )
            embed.set_author(name="Timetable Bot")
            embed.add_field(name="👤 Student", value=interaction.user.mention, inline=True)
            embed.add_field(name="👥 Linked Batch", value=f"**Batch {prof.batch}**", inline=True)
            embed.set_footer(text=f"Batch {prof.batch} • Run /profile to view your info")
            if self.view:
                for child in self.view.children:
                    setattr(child, "disabled", True)
                await interaction.response.edit_message(embed=embed, view=self.view)
            else:
                await interaction.response.edit_message(embed=embed)

    class BatchSelectView(discord.ui.View):
        def __init__(self, cog: "TimetableCog", author_id: int, batches: list[str], timeout: float = 60.0):
            super().__init__(timeout=timeout)
            for b in batches[:10]:
                self.add_item(BatchButton(b, cog, author_id))
else:
    BatchSelectView = None  # type: ignore[assignment, misc]


class TimetableCog(commands.Cog):
    """Show the timetable and what's happening now / next."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.lock = asyncio.Lock()
        self._tz_warned = False

    # ----- time ------------------------------------------------------------ #
    def _tz(self) -> tzinfo:
        name = (
            os.environ.get("TIMEZONE")
            or os.environ.get("TZ")
            or str(getattr(self.bot, "config", {}).get("timezone") or "")
        ).strip()

        if not name:
            android_tz = _detect_android_timezone()
            name = android_tz if android_tz else DEFAULT_TZ_NAME

        try:
            return ZoneInfo(name)
        except (KeyError, ValueError, OSError):
            if name.upper() in ("IST", "ASIA/KOLKATA", "ASIA/CALCUTTA"):
                return IST
            try:
                local_tz = datetime.now().astimezone().tzinfo
                if local_tz is not None:
                    return local_tz
            except Exception:
                pass
            if not self._tz_warned:
                log.warning("Timezone %r unavailable (install tzdata via pip). Using IST.", name)
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

    # ----- user profiles & batch preferences ---------------------------- #
    def read_profiles(self) -> dict[str, UserProfile]:
        """Read all user profiles from users.json, falling back to config.json migration."""
        profiles: dict[str, UserProfile] = {}
        if USERS_FILE.is_file():
            try:
                raw = json.loads(USERS_FILE.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    for uid, pdata in raw.items():
                        if isinstance(pdata, dict):
                            profiles[str(uid)] = UserProfile.from_dict(uid, pdata)
            except Exception as exc:
                log.warning("Could not read users.json: %s", exc)

        # Smooth fallback/migration for existing user_batches in config.json
        if not profiles and CONFIG_FILE.is_file():
            try:
                conf = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
                user_batches = conf.get("user_batches", {})
                if isinstance(user_batches, dict):
                    for uid, b in user_batches.items():
                        if str(b).strip():
                            profiles[str(uid)] = UserProfile(
                                user_id=str(uid),
                                batch=str(b).strip().upper(),
                                linked_at=self._now().isoformat(),
                            )
            except Exception:
                pass
        return profiles

    def write_profiles(self, profiles: dict[str, UserProfile]) -> None:
        """Atomically persist user profiles to users.json."""
        USERS_FILE.parent.mkdir(parents=True, exist_ok=True)
        raw = {uid: prof.to_dict() for uid, prof in profiles.items()}
        fd, tmp = tempfile.mkstemp(dir=USERS_FILE.parent, prefix=".users-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(raw, fh, indent=2, ensure_ascii=False)
                fh.write("\n")
            os.replace(tmp, USERS_FILE)
        except Exception as exc:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise ScheduleError(f"I couldn't save user profile ({type(exc).__name__}).") from exc

    def get_user_profile(self, user_id: str | int) -> Optional[UserProfile]:
        """Get the UserProfile for a user, or None if unlinked."""
        return self.read_profiles().get(str(user_id))

    def set_user_profile(
        self,
        user_id: str | int,
        batch: str,
        name: str = "",
        roll_no: str = "",
    ) -> UserProfile:
        """Link or update a user's batch profile."""
        profiles = self.read_profiles()
        uid = str(user_id)
        existing = profiles.get(uid)
        now_iso = self._now().isoformat()
        clean_batches = parse_batches(batch)
        clean_batch = clean_batches[0] if clean_batches else batch.strip().upper()
        profile = UserProfile(
            user_id=uid,
            batch=clean_batch,
            name=name.strip() or (existing.name if existing else ""),
            roll_no=roll_no.strip() or (existing.roll_no if existing else ""),
            linked_at=existing.linked_at if existing and existing.linked_at else now_iso,
        )
        profiles[uid] = profile
        self.write_profiles(profiles)
        return profile

    def delete_user_profile(self, user_id: str | int) -> bool:
        """Unlink a user profile. Returns True if removed, False if not found."""
        profiles = self.read_profiles()
        uid = str(user_id)
        if uid in profiles:
            del profiles[uid]
            self.write_profiles(profiles)
            return True
        return False

    def get_user_batch(self, user_id: str | int) -> Optional[str]:
        prof = self.get_user_profile(user_id)
        return prof.batch if prof else None

    def set_user_batch(self, user_id: str | int, batch: Optional[str]) -> None:
        if batch is None:
            self.delete_user_profile(user_id)
        else:
            self.set_user_profile(user_id, batch)

    def get_known_batches(self) -> list[str]:
        """Scan schedule slots and linked profiles to discover active batches."""
        found: set[str] = set()
        for slots in self.read_all().values():
            for s in slots:
                for b in s.batches:
                    if b and b != "ALL":
                        found.add(b)
        for prof in self.read_profiles().values():
            if prof.batch and prof.batch != "ALL":
                found.add(prof.batch)
        return sorted(found) if found else ["1", "2"]

    # ----- editing API (used by the admin cog) ----------------------------- #
    async def set_slot(
        self,
        day: str,
        time_slot: str,
        subject: str,
        batch: str = "ALL",
        room: str = "",
        faculty: str = "",
    ) -> tuple[str, Slot]:
        """Insert or replace a slot for specific batch(es). Returns ('added'|'updated', slot)."""
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

        parsed_batches = parse_batches(batch)
        entry: dict[str, Any] = {
            "start": f"{start:%H:%M}",
            "end": f"{end:%H:%M}",
            "batches": parsed_batches,
        }
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

                same_time = existing.start == new_slot.start and existing.end == new_slot.end
                time_overlap = existing.start < new_slot.end and new_slot.start < existing.end
                same_batches = set(existing.batches) == set(new_slot.batches)
                overlap_batch = batches_overlap(existing.batches, new_slot.batches)

                # Same start time and same batch target -> update
                if existing.start == new_slot.start and same_batches:
                    action = "updated"
                    continue

                # Time overlap with batch collision
                if time_overlap and overlap_batch:
                    overlap_info = f"Batch {existing.batch_str}" if not existing.is_all_batches else "All Batches"
                    raise ScheduleError(
                        f"That overlaps **{existing.title}** (`{existing.span}`) for **{overlap_info}**.\n"
                        f"Different batches can share the time slot, but {overlap_info} already has a class.\n"
                        "Remove it first with `delclass` if you want to replace it."
                    )
                kept.append(raw)

            kept.append(entry)
            kept.sort(key=lambda r: (_raw_sort_key(r), str(r.get("batches", ""))))
            data[key] = kept
            self.write_raw(data)
        return action, new_slot

    async def delete_slot(
        self, day: str, start_text: str, batch: Optional[str] = None
    ) -> Slot:
        key = resolve_day(day)
        if key is None:
            raise ScheduleError(f"`{day}` isn't a valid day. Use mon, tue, wed, thu, fri, sat or sun.")
        try:
            start = _parse_hhmm(start_text)
        except ValueError:
            raise ScheduleError("Give the slot's start time as `HH:MM`, e.g. `09:30`.") from None

        target_batch = batch.strip().upper() if batch else None

        async with self.lock:
            data = self.read_raw()
            day_slots = data.setdefault(key, [])
            matching: list[tuple[int, Slot]] = []

            for idx, raw in enumerate(day_slots):
                try:
                    s = Slot.from_dict(raw)
                    if s.start == start:
                        if target_batch is None or target_batch in s.batches or target_batch == "ALL":
                            matching.append((idx, s))
                except ValueError:
                    continue

            if not matching:
                b_info = f" for Batch {target_batch}" if target_batch else ""
                raise ScheduleError(f"No slot starts at `{start:%H:%M}`{b_info} on {DAY_NAMES[key]}.")

            if len(matching) > 1 and target_batch is None:
                details = ", ".join(f"`{s.title}` (Batch {s.batch_str})" for _, s in matching)
                raise ScheduleError(
                    f"Multiple slots start at `{start:%H:%M}` on {DAY_NAMES[key]}:\n{details}\n"
                    f"Please specify the batch to remove, e.g. `delclass {day} {start:%H:%M} batch:1`"
                )

            del_idx, removed_slot = matching[0]
            del day_slots[del_idx]
            self.write_raw(data)
        return removed_slot

    # ----- embeds ---------------------------------------------------------- #
    def _status_embed(
        self,
        now: datetime,
        query_batch: Optional[str] = None,
        user_profile: Optional[UserProfile] = None,
    ) -> discord.Embed:
        days = self.read_all()
        key = DAY_KEYS[now.weekday()]
        all_today = days[key]
        today = [s for s in all_today if slot_matches_batch(s, query_batch)]
        t = now.time()
        tz = now.tzinfo

        current = next((s for s in today if s.start <= t < s.end), None)
        upcoming: Optional[tuple[int, date, Slot]] = None
        for offset in range(8):
            d = now.date() + timedelta(days=offset)
            for slot in days[DAY_KEYS[d.weekday()]]:
                if slot.is_free or (offset == 0 and slot.start <= t):
                    continue
                if not slot_matches_batch(slot, query_batch):
                    continue
                upcoming = (offset, d, slot)
                break
            if upcoming:
                break

        def ts(d: date, at: time) -> int:
            return int(datetime.combine(d, at, tzinfo=tz).timestamp())

        batch_str = f"   •   👥 **Batch {query_batch.upper()}**" if query_batch else ""
        embed = discord.Embed(
            title="🧭 Live Class Status",
            description=f"📅 **{now:%A, %d %B}**   •   ⏰ **{now:%H:%M} {now.tzname() or ''}**{batch_str}",
            color=COLOR_PRIMARY,
            timestamp=now,
        )
        embed.set_author(name="Timetable Bot")

        if current is not None:
            end_ts = ts(now.date(), current.end)
            now_m = _minutes(t)
            start_m = _minutes(current.start)
            end_m = _minutes(current.end)
            dur = max(1, end_m - start_m)
            elapsed = max(0, min(dur, now_m - start_m))
            pct = elapsed / dur
            progress_str = make_progress_bar(pct)

            if current.is_free:
                embed.color = COLOR_WARN
                icon = "🍱" if "lunch" in current.title.lower() else "☕"
                embed.add_field(
                    name=f"{icon} Current Break · In Progress",
                    value=(
                        f"### {current.title}\n"
                        f"> ⏰ **`{current.span}`** • Ends <t:{end_ts}:R> (<t:{end_ts}:t>)\n"
                        f"> 📊 Progress: {progress_str}"
                    ),
                    inline=False,
                )
            else:
                embed.color = COLOR_SUCCESS
                icon = KIND_ICONS.get(current.kind.lower(), "📖")
                kind_str = f" `[{current.kind}]`" if current.kind else ""
                batch_tag = f" `[Batch {current.batch_str}]`" if (query_batch is None and not current.is_all_batches) else ""
                details = [f"⏰ **`{current.span}`** • Ends <t:{end_ts}:R> (<t:{end_ts}:t>)"]
                if current.room:
                    details.append(f"📍 Room: **{current.room}**")
                if current.faculty:
                    details.append(f"👨‍🏫 Faculty: **{current.faculty}**")
                if query_batch is None and not current.is_all_batches:
                    details.append(f"👥 Batch: **{current.batch_str}**")
                details.append(f"📊 Progress: {progress_str}")

                detail_text = "\n> ".join(details)
                embed.add_field(
                    name="🟢 Happening Right Now",
                    value=f"### {icon} {current.subject}{kind_str}{batch_tag}\n> {detail_text}",
                    inline=False,
                )
        else:
            if not today:
                b_note = f" for Batch {query_batch.upper()}" if query_batch else ""
                note = f"📭 No classes are scheduled{b_note} on the timetable today."
            elif t >= today[-1].end:
                note = "🎉 **All classes for today are done!** Have a wonderful evening."
            elif t < today[0].start:
                note = f"🌅 **Day hasn't started yet.** First class begins at `{today[0].start:%H:%M}`."
            else:
                note = "☕ **Break time.** You're between scheduled slots right now."
            embed.add_field(name="💤 Current Status", value=f"> {note}", inline=False)

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
            icon = KIND_ICONS.get(slot.kind.lower(), "📖")
            kind_str = f" `[{slot.kind}]`" if slot.kind else ""
            batch_tag = f" `[Batch {slot.batch_str}]`" if (query_batch is None and not slot.is_all_batches) else ""

            details = [f"⏰ **`{slot.span}`** • Starts <t:{start_ts}:R> (<t:{start_ts}:t>)"]
            if slot.room:
                details.append(f"📍 Room: **{slot.room}**")
            if slot.faculty:
                details.append(f"👨‍🏫 Faculty: **{slot.faculty}**")
            if query_batch is None and not slot.is_all_batches:
                details.append(f"👥 Batch: **{slot.batch_str}**")

            detail_text = "\n> ".join(details)
            embed.add_field(
                name=f"⏭️ Up Next • {when}",
                value=f"### {icon} {slot.subject}{kind_str}{batch_tag}\n> {detail_text}",
                inline=False,
            )
        else:
            embed.add_field(
                name="⏭️ Up Next",
                value="> No more classes found in your timetable schedule.",
                inline=False,
            )

        if user_profile and query_batch == user_profile.batch:
            footer_text = f"👤 Linked to Batch {user_profile.batch} • Timezone: {now.tzname() or 'Local'} • Run /profile to view"
        elif query_batch:
            footer_text = f"Viewing Batch {query_batch.upper()} • Timezone: {now.tzname() or 'Local'} • Tip: /link <batch>"
        else:
            footer_text = f"Showing All Batches • Timezone: {now.tzname() or 'Local'} • Tip: /link <batch> to personalize"
        embed.set_footer(text=footer_text)
        return embed

    # ----- commands -------------------------------------------------------- #
    @commands.hybrid_command(
        name="timetable",
        aliases=["tt", "day"],
        description="Show your class timetable for a day and optional batch.",
    )
    @app_commands.describe(
        day="mon–sun, 'today' or 'tomorrow'. Leave empty for today.",
        batch="Optional batch filter (e.g. 1, 2). If omitted, uses your saved batch or all.",
    )
    @app_commands.choices(day=[app_commands.Choice(name=n, value=v) for n, v in DAY_CHOICES])
    async def timetable(
        self, ctx: commands.Context, day: Optional[str] = None, batch: Optional[str] = None
    ) -> None:
        now = self._now()
        today_key = DAY_KEYS[now.weekday()]
        if day is None or not day.strip():
            key = today_key
        else:
            key = resolve_day(day, now)
            if key is None:
                await _safe_send(
                    ctx,
                    embed=error_embed(
                        f"I don't know the day `{day[:30]}`.\nTry `mon`, `tue`, `wed`, "
                        "`thu`, `fri`, `today` or `tomorrow`."
                    ),
                    ephemeral=True,
                )
                return

        profile = self.get_user_profile(ctx.author.id)
        effective_batch = (batch.strip().upper() if batch else None) or (profile.batch if profile else None)
        embed = build_day_embed(
            key,
            self.read_all(),
            now,
            highlight_now=(key == today_key),
            query_batch=effective_batch,
            user_profile=profile,
        )
        await _safe_send(ctx, embed=embed)

    @commands.hybrid_command(
        name="next",
        aliases=["now", "upnext"],
        description="See what class is on right now and what's coming up next.",
    )
    @app_commands.describe(batch="Optional batch filter (e.g. 1, 2). If omitted, uses your saved batch.")
    async def next_class(self, ctx: commands.Context, batch: Optional[str] = None) -> None:
        profile = self.get_user_profile(ctx.author.id)
        effective_batch = (batch.strip().upper() if batch else None) or (profile.batch if profile else None)
        await _safe_send(
            ctx,
            embed=self._status_embed(self._now(), query_batch=effective_batch, user_profile=profile),
        )

    @commands.hybrid_command(
        name="week",
        aliases=["weekly", "schedule"],
        description="Show a weekly overview of all your classes.",
    )
    @app_commands.describe(batch="Optional batch filter (e.g. 1, 2). If omitted, uses your saved batch.")
    async def weekly_schedule(self, ctx: commands.Context, batch: Optional[str] = None) -> None:
        profile = self.get_user_profile(ctx.author.id)
        effective_batch = (batch.strip().upper() if batch else None) or (profile.batch if profile else None)
        embed = build_week_embed(
            self.read_all(), self._now(), query_batch=effective_batch, user_profile=profile
        )
        await _safe_send(ctx, embed=embed)

    @commands.hybrid_command(
        name="link",
        aliases=["linkaccount"],
        description="Link your Discord account to your batch (and optional student info).",
    )
    @app_commands.describe(
        batch="Your batch number or name (e.g. 1, 2, A, B).",
        name="Optional student name.",
        roll_no="Optional student roll number / ID.",
    )
    async def link_account(
        self,
        ctx: commands.Context,
        batch: Optional[str] = None,
        name: Optional[str] = None,
        roll_no: Optional[str] = None,
    ) -> None:
        user_id = str(ctx.author.id)
        if batch is not None and batch.strip():
            clean_batches = parse_batches(batch)
            clean_batch = clean_batches[0] if clean_batches else batch.strip().upper()
            if clean_batch in ("CLEAR", "NONE", "RESET", "REMOVE"):
                await self.unlink_account(ctx)
                return

            prof = self.set_user_profile(
                user_id=user_id,
                batch=clean_batch,
                name=name or "",
                roll_no=roll_no or "",
            )
            embed = discord.Embed(
                title="🎓 Account Linked Successfully!",
                description=(
                    f"Successfully linked {ctx.author.mention} to **Batch {prof.batch}**! 🎉\n\n"
                    "Now whenever you run `/timetable`, `/next`, or `/week`, "
                    f"the bot will automatically load the timetable for **Batch {prof.batch}**."
                ),
                color=COLOR_SUCCESS,
            )
            embed.set_author(name="Timetable Bot")
            embed.add_field(name="👤 Discord User", value=ctx.author.mention, inline=True)
            embed.add_field(name="👥 Linked Batch", value=f"**Batch {prof.batch}**", inline=True)
            if prof.name:
                embed.add_field(name="📝 Name", value=prof.name, inline=True)
            if prof.roll_no:
                embed.add_field(name="🆔 Roll / ID", value=f"`{prof.roll_no}`", inline=True)
            embed.add_field(
                name="💡 Quick Tips",
                value=(
                    f"• Run `/timetable` to view your schedule.\n"
                    f"• Want to check another batch? Run `/timetable batch:2`.\n"
                    f"• Run `/profile` to view your profile anytime."
                ),
                inline=False,
            )
            embed.set_footer(text=f"Batch {prof.batch} • Tip: /profile to check your saved info")
            await _safe_send(ctx, embed=embed, ephemeral=True)
            return

        # No batch parameter passed: show interactive picker if UI available, else show guide
        existing = self.get_user_profile(user_id)
        batches = self.get_known_batches()
        batch_list = ", ".join(f"**Batch {b}**" for b in batches) if batches else "None in schedule"

        embed = discord.Embed(
            title="🔗 Link Your Discord Account",
            description=(
                "Link your account to your batch so the bot always serves your personal schedule!\n\n"
                "### 📌 How to Link\n"
                "> `/link <batch>` — *e.g.* `/link 1` or `/link 2`\n\n"
                "### 📝 With Student Info (Optional)\n"
                '> `/link batch:1 name:"Your Name" roll_no:"21CS042"`\n\n'
                f"**Available Batches in Schedule:** {batch_list}"
            ),
            color=COLOR_PRIMARY,
        )
        embed.set_author(name="Timetable Bot")
        if existing:
            extra = f" ({existing.name})" if existing.name else ""
            embed.add_field(
                name="ℹ️ Current Link",
                value=f"You are currently linked to **Batch {existing.batch}**{extra}.",
                inline=False,
            )
        embed.set_footer(text="Run /link <batch> or pick your batch below")

        view = None
        if _HAS_UI and batches and BatchSelectView is not None:
            view = BatchSelectView(self, ctx.author.id, batches)

        await _safe_send(ctx, embed=embed, view=view, ephemeral=True)

    @commands.hybrid_command(
        name="unlink",
        aliases=["unlinkaccount", "clearbatch"],
        description="Unlink your Discord account from any batch.",
    )
    async def unlink_account(self, ctx: commands.Context) -> None:
        user_id = str(ctx.author.id)
        removed = self.delete_user_profile(user_id)
        if removed:
            embed = discord.Embed(
                title="🔗 Account Unlinked",
                description=(
                    f"Your account ({ctx.author.mention}) has been unlinked.\n"
                    "The bot will now display timetables for all batches by default.\n\n"
                    "-# 💡 *You can link again anytime using `/link <batch>`.*"
                ),
                color=COLOR_WARN,
            )
        else:
            embed = discord.Embed(
                title="ℹ️ No Linked Account Found",
                description=(
                    f"Your account ({ctx.author.mention}) wasn't linked to any batch.\n"
                    "Use `/link <batch>` (e.g. `/link 1`) to link your batch!"
                ),
                color=COLOR_PRIMARY,
            )
        embed.set_author(name="Timetable Bot")
        await _safe_send(ctx, embed=embed, ephemeral=True)

    @commands.hybrid_command(
        name="profile",
        aliases=["me", "myprofile", "whoami"],
        description="View your linked account profile and today's schedule summary.",
    )
    @app_commands.describe(member="Optional: View another member's profile.")
    async def view_profile(
        self, ctx: commands.Context, member: Optional[discord.Member] = None
    ) -> None:
        target = member or ctx.author
        prof = self.get_user_profile(target.id)
        now = self._now()
        if prof:
            embed = discord.Embed(
                title=f"👤 Student Profile — {target.display_name}",
                description=f"Discord Account: {target.mention}",
                color=COLOR_PRIMARY,
                timestamp=now,
            )
            embed.set_author(name="Timetable Bot")
            if hasattr(target, "display_avatar") and target.display_avatar:
                embed.set_thumbnail(url=target.display_avatar.url)
            embed.add_field(name="👥 Linked Batch", value=f"**Batch {prof.batch}**", inline=True)
            if prof.name:
                embed.add_field(name="📝 Name", value=f"**{prof.name}**", inline=True)
            if prof.roll_no:
                embed.add_field(name="🆔 Roll / ID", value=f"`{prof.roll_no}`", inline=True)

            day_key = DAY_KEYS[now.weekday()]
            slots = [
                s
                for s in self.read_all().get(day_key, [])
                if not s.is_free and slot_matches_batch(s, prof.batch)
            ]
            noun = "class" if len(slots) == 1 else "classes"
            embed.add_field(
                name=f"📅 Today's Schedule ({DAY_NAMES[day_key]})",
                value=(
                    f"> **{len(slots)} {noun}** scheduled for Batch {prof.batch}"
                    if slots
                    else "> No classes scheduled today"
                ),
                inline=False,
            )
            footer_text = f"Linked since {prof.linked_at[:10]}" if prof.linked_at else "Linked"
            embed.set_footer(text=f"{footer_text} • Run /timetable to view schedule")
        else:
            is_self = target.id == ctx.author.id
            desc = (
                "You haven't linked your account to a batch yet!\n\n"
                "Use `/link <batch>` (e.g. `/link 1`) so `/timetable` will automatically "
                "show your batch's classes without needing any filters."
                if is_self
                else f"{target.mention} has not linked their account to a batch yet."
            )
            embed = discord.Embed(
                title="👤 Profile Not Linked",
                description=desc,
                color=COLOR_MUTED,
            )
            embed.set_author(name="Timetable Bot")
            if is_self:
                embed.set_footer(text="Tip: Link in seconds with /link 1")
        await _safe_send(ctx, embed=embed, ephemeral=True)

    @commands.hybrid_command(
        name="mybatch",
        aliases=["setbatch"],
        description="Check, set, or update your batch preference.",
    )
    @app_commands.describe(batch="Your batch number (e.g. 1, 2) or 'clear' to unlink.")
    async def mybatch(self, ctx: commands.Context, batch: Optional[str] = None) -> None:
        """Alias for /link and /profile for backwards compatibility."""
        if batch is None:
            await self.view_profile(ctx)
        else:
            await self.link_account(ctx, batch=batch)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(TimetableCog(bot))
