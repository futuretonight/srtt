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
OVERRIDES_FILE = DATA_FILE.parent / "overrides.json"
ROSTER_FILE = DATA_FILE.parent / "roster.json"
INDEX_FILE = DATA_FILE.parent / "batches_index.json"
OPTIONS_FILE = DATA_FILE.parent / "timetable_options.json"
ROOMS_FILE = DATA_FILE.parent / "room_availability.json"
REPRESENTATIVES_FILE = DATA_FILE.parent / "representatives.json"
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
    "lecture": "",
    "tutorial": "",
    "lab": "",
    "practical": "",
    "seminar": "",
    "workshop": "",
    "project": "",
}


def make_progress_bar(pct: float, length: int = 10) -> str:
    """Generate a clean visual progress bar: e.g. [=====-----] 40%"""
    pct = max(0.0, min(1.0, pct))
    filled = int(round(pct * length))
    bar = "=" * filled + "-" * (length - filled)
    return f"`[{bar}]` **{int(pct * 100)}%**"

_SLOT_RE = re.compile(
    r"^\s*(?P<start>\d{1,2}:\d{2})\s*(?:-|–|—|to)\s*(?P<end>\d{1,2}:\d{2})\s*$",
    re.IGNORECASE,
)
_KIND_RE = re.compile(r"^(?P<name>.+?)\s*\((?P<kind>[^()]+)\)\s*$")
_FREE_WORDS = frozenset(
    {"free", "free period", "lunch", "lunch / free", "lunch/free", "break"}
)

ROOM_SLOTS = [
    "08:30", "09:30", "10:30", "11:30", "12:30",
    "13:30", "14:30", "15:30", "16:30", "17:30",
    "18:30", "19:30", "20:30"
]


def get_room_block(room_name: str) -> str:
    m = re.search(r"[-_]BL(\d+)(?:[-_]|$)", room_name, re.I)
    if m:
        return f"Block {m.group(1)}"
    m = re.search(r"BL(\d+)", room_name, re.I)
    if m:
        return f"Block {m.group(1)}"
    return "Campus / Other"


def find_best_room_slot(t: time) -> str:
    m = t.hour * 60 + t.minute
    if m < 8 * 60 + 30:
        return "08:30"
    if m >= 20 * 60 + 30:
        return "20:30"
    for s in ROOM_SLOTS:
        sh, sm = map(int, s.split(":"))
        slot_m = sh * 60 + sm
        if slot_m <= m < slot_m + 60:
            return s
    return "08:30"


YEAR_MAP = {
    "1": "First",
    "first": "First",
    "1st": "First",
    "2": "Second",
    "second": "Second",
    "2nd": "Second",
    "3": "Third",
    "third": "Third",
    "3rd": "Third",
    "4": "Fourth",
    "fourth": "Fourth",
    "4th": "Fourth",
}

DEGREE_ALIASES: dict[str, str] = {
    "CSE": "BTECH-CSE",
    "CS": "BTECH-CSE",
    "BTECHCSE": "BTECH-CSE",
    "ECE": "BTECH-ECE",
    "BTECHECE": "BTECH-ECE",
    "EEE": "BTECH-EEE",
    "BTECHEEE": "BTECH-EEE",
    "CIVIL": "BTECH-CE",
    "CE": "BTECH-CE",
    "BTECHCE": "BTECH-CE",
    "MECH": "BTECH-ME",
    "ME": "BTECH-ME",
    "BTECHME": "BTECH-ME",
    "BCA": "BCA-CSAI",
    "BCACSAI": "BCA-CSAI",
    "BBA": "BBA-BM",
    "BBABM": "BBA-BM",
    "BCOM": "B.Com-BM",
    "BCOMBM": "B.Com-BM",
    "AGRI": "B.Sc.(Hons.)-AGRI",
    "AGRICULTURE": "B.Sc.(Hons.)-AGRI",
    "BSCAGRI": "B.Sc.(Hons.)-AGRI",
    "BSCCSAI": "B.Sc(Comp.)-CSAI",
    "MBA": "MBA-BM",
    "MBABM": "MBA-BM",
    "MCA": "MCA-CSE",
    "MCACSE": "MCA-CSE",
    "MTECHCSE": "MTECH-CSE",
    "MTECHECE": "MTECH-ECE",
    "MTECHEEE": "MTECH-EEE",
    "MTECHCE": "MTECH-CE",
    "MTECHME": "MTECH-ME",
}

SPECIALIZATION_TAGS: dict[str, list[str]] = {
    "AI": ["AIB", "CAI", "AI"],
    "AIML": ["AIB", "CAI", "AI"],
    "AI/ML": ["AIB", "CAI", "AI"],
    "CORE": ["CSB", "CSBTB", "CS"],
    "CS": ["CSB", "CSBTB", "CS"],
    "CSE": ["CSB", "CSBTB", "CS"],
    "CYBER": ["CYB", "CY"],
    "CYBERSECURITY": ["CYB", "CY"],
    "CY": ["CYB", "CY"],
    "DS": ["DSB", "DS"],
    "DATASCIENCE": ["DSB", "DS"],
    "DATA": ["DSB", "DS"],
    "ECE": ["ECEBT", "ECE", "ECB"],
    "EEE": ["EEEBT", "EEE", "EEB"],
    "CIVIL": ["CIVBT", "CIV", "CEB"],
    "CE": ["CIVBT", "CIV", "CEB"],
    "MECH": ["MECBT", "MEC", "MEB"],
    "ME": ["MECBT", "MEC", "MEB"],
    "BCA": ["CAIBC"],
    "BBA": ["BUSBB", "BBA"],
    "BCOM": ["BUSBC"],
    "AGRI": ["AGBS", "AGMS"],
}


def resolve_batch_from_inputs(
    deg_input: str, yr_input: str, batch_input: str
) -> tuple[Optional[str], Optional[str], Optional[str], str]:
    """Resolve user inputs into (batch_code, matched_degree, matched_year, description)."""
    deg_in = deg_input.strip().upper() if deg_input else ""
    yr_in = YEAR_MAP.get(yr_input.strip().lower(), yr_input.strip().title()) if yr_input else ""
    b_in = batch_input.strip().upper() if batch_input else ""

    options: dict[str, Any] = {}
    if OPTIONS_FILE.is_file():
        try:
            options = json.loads(OPTIONS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass

    index: dict[str, Any] = {}
    if INDEX_FILE.is_file():
        try:
            index = json.loads(INDEX_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass

    # 1. Exact match in index (e.g. "26CAIBTAIB16")
    if b_in in index.get("batches", {}):
        meta = index["batches"][b_in]
        m_deg = meta["degrees"][0] if meta.get("degrees") else ""
        m_yr = meta["years"][0] if meta.get("years") else ""
        return b_in, m_deg, m_yr, "Exact batch code matched"

    # Extract 2-digit number and type prefix from batch_input
    num_m = re.search(r"(\d{1,2})$", b_in)
    b_num = num_m.group(1).zfill(2) if num_m else ""
    raw_type = b_in[:num_m.start()].strip() if num_m else ""
    clean_type = re.sub(r"[\s\-_/]+", "", raw_type).upper()
    spec_tags = SPECIALIZATION_TAGS.get(clean_type, [clean_type]) if clean_type else []

    # 2. Find degree
    matched_degree = None
    if deg_in:
        deg_clean = re.sub(r"[\s\-_/]+", "", deg_in)
        if deg_clean in DEGREE_ALIASES:
            matched_degree = DEGREE_ALIASES[deg_clean]
        else:
            for d in options.keys():
                if deg_in == d.upper() or deg_in in d.upper():
                    matched_degree = d
                    break
            if not matched_degree:
                for d in options.keys():
                    if deg_in in d.upper().split("-"):
                        matched_degree = d
                        break

    # If no degree given, attempt to search across all batches in the index
    if not matched_degree:
        if b_in:
            candidates: list[tuple[str, str, str]] = []
            for b, meta in index.get("batches", {}).items():
                m_deg = meta["degrees"][0] if meta.get("degrees") else ""
                m_yr = meta["years"][0] if meta.get("years") else ""
                num_match = (b.endswith(f"B{b_num}") or b.endswith(b_num)) if b_num else False
                if b == b_in or (b_in in b) or num_match:
                    if spec_tags:
                        if any(tag in b for tag in spec_tags):
                            candidates.append((b, m_deg, m_yr))
                    else:
                        candidates.append((b, m_deg, m_yr))

            if candidates:
                # Prioritize current active academic year (starts with 26)
                candidates.sort(key=lambda c: (0 if c[0].startswith("26") else 1, c[0]))
                b_found, d_found, y_found = candidates[0]
                return b_found, d_found, y_found, f"Matched via type/section in {d_found} ({y_found})"

        return None, None, None, f"Could not find degree `{deg_input}`. Run /degrees to view all programs."

    # 3. Find year in that degree
    deg_years = options.get(matched_degree, {})
    matched_year = None
    if yr_in:
        for y in deg_years.keys():
            if yr_in.lower() == y.lower():
                matched_year = y
                break

    if not matched_year:
        matched_year = list(deg_years.keys())[0] if len(deg_years) == 1 else "First"

    # 4. Find batch in that degree + year
    batches = deg_years.get(matched_year, [])

    # Check specialization tag + number first (e.g. AI 16, Core 35, Cyber 59, DS 57)
    if spec_tags and b_num:
        for b in batches:
            if (b.endswith(f"B{b_num}") or b.endswith(b_num)) and any(tag in b for tag in spec_tags):
                return b, matched_degree, matched_year, f"Matched {clean_type} Batch {b_num} in {matched_degree} ({matched_year})"

    # Check 2-digit number suffix (e.g. "16")
    if b_num:
        for b in batches:
            if b.endswith(f"B{b_num}") or b.endswith(b_num):
                return b, matched_degree, matched_year, f"Matched Batch {b_num} in {matched_degree} ({matched_year})"

    # Check substring
    for b in batches:
        if b_in and b_in in b:
            return b, matched_degree, matched_year, f"Matched batch in {matched_degree} ({matched_year})"

    sample_batches = ", ".join(f"`{b}`" for b in batches[:6])
    return None, matched_degree, matched_year, f"Batch `{batch_input}` not found in **{matched_degree}** ({matched_year}). Options: {sample_batches}..."


def build_unregistered_embed(prefix: str = "!") -> discord.Embed:
    embed = discord.Embed(
        title="Timetable Setup Required",
        description=(
            "You have not registered your timetable profile yet.\n"
            "Because each program, academic year, and section has distinct classes, your details are needed.\n\n"
            "**Registration takes only 10 seconds:**\n"
            "1. **Department / Degree:** e.g. `CSE`, `ECE`, `BTECH-CSE`, `BCA`\n"
            "2. **Academic Year:** `1`, `2`, `3`, or `4`\n"
            "3. **Batch / Section:** e.g. `AI 16`, `Core 35`, `Cyber 59`, `DS 57`, `16`, or `26CAIBTAIB16`\n\n"
            "────────────────────────────────────────\n"
            f"**Quick Setup:** `{prefix}register <degree> <year> <batch>`\n"
            f"*Example:* `{prefix}register CSE 1 AI 16`\n\n"
            f"**Interactive Setup:** Type `{prefix}register` and follow the prompts.\n"
            "────────────────────────────────────────\n"
            f"*Tip: To search batch codes, type `{prefix}findbatch <keyword>` or `{prefix}degrees`.*"
        ),
        color=COLOR_PRIMARY,
    )
    embed.set_author(name="Timetable Setup Required")
    embed.set_footer(text="Once registered, commands like /today, /next, and /week work automatically.")
    return embed


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


def build_unregistered_component_v2(prefix: str = "!") -> dict[str, Any]:
    """Build a modern Discord Components V2 Container payload for timetable setup."""
    return {
        "type": 17,
        "spoiler": False,
        "accent_color": COLOR_PRIMARY,
        "components": [
            {
                "type": 10,
                "content": (
                    "### Timetable Setup Required\n"
                    "Because each program, academic year, and section has distinct classes, your details are needed."
                ),
            },
            {
                "type": 14,
                "divider": True,
                "spacing": 1,
            },
            {
                "type": 10,
                "content": (
                    "**Required Information:**\n"
                    "1. **Department / Degree:** e.g. `CSE`, `ECE`, `BTech`, `BCA`\n"
                    "2. **Academic Year:** `1`, `2`, `3`, `4`\n"
                    "3. **Batch / Section:** e.g. `AI 16`, `Core 35`, `Cyber 59`, `DS 57`, `16`, or `26CAIBTAIB16`"
                ),
            },
            {
                "type": 14,
                "divider": True,
                "spacing": 1,
            },
            {
                "type": 10,
                "content": (
                    f"**Quick Setup:** `{prefix}register <degree> <year> <batch>`\n"
                    f"*Example:* `{prefix}register CSE 1 AI 16`\n\n"
                    f"**Interactive Setup:** Type `{prefix}register` and follow prompts.\n\n"
                    f"-# *Tip: To search batch codes, type {prefix}findbatch <keyword> or {prefix}degrees*"
                ),
            },
        ],
    }


def build_registered_component_v2(prof: Any, prefix: str = "!", mention: str = "") -> dict[str, Any]:
    """Build a modern Discord Components V2 Container payload for successful registration."""
    reg_lines = [
        f"**Degree:** **{getattr(prof, 'degree', '') or 'N/A'}**  •  **Year:** **{getattr(prof, 'year', '') or 'N/A'}**",
        f"**Batch:** **`{getattr(prof, 'batch', '')}`**",
    ]
    if getattr(prof, "name", ""):
        reg_lines.append(f"**Name:** **{prof.name}**")
    if getattr(prof, "roll_no", ""):
        reg_lines.append(f"**Roll / ID:** `{prof.roll_no}`")

    commands_lines = (
        f"• `{prefix}today` — View your classes for today\n"
        f"• `{prefix}next` — What class is on now / next\n"
        f"• `{prefix}week` — Full weekly timetable\n"
        f"• `{prefix}freerooms` — Find empty study rooms during breaks"
    )
    user_greet = f"Welcome {mention}. " if mention else ""
    return {
        "type": 17,
        "spoiler": False,
        "accent_color": COLOR_SUCCESS,
        "components": [
            {
                "type": 10,
                "content": (
                    f"## Timetable Setup Complete\n"
                    f"{user_greet}Your timetable has been registered successfully.\n"
                    f"Whenever you run `{prefix}today`, `{prefix}now`, or `{prefix}week`, your classes will load instantly for **Batch {getattr(prof, 'batch', '')}**."
                ),
            },
            {"type": 14, "divider": True, "spacing": 1},
            {"type": 10, "content": "\n".join(reg_lines)},
            {"type": 14, "divider": True, "spacing": 1},
            {"type": 10, "content": f"### Available Commands\n{commands_lines}"},
            {"type": 14, "divider": True, "spacing": 1},
            {"type": 10, "content": f"-# Saved to data/users.json • Type {prefix}profile to view your profile"},
        ],
    }


async def send_component_v2(
    ctx: commands.Context,
    container_payload: dict[str, Any] | Any,
    fallback_embed: Optional[discord.Embed] = None,
    **kwargs: Any,
) -> Any:
    """Send a modern Discord Components V2 Container message with seamless fallback to Embed."""
    try:
        from discord.http import handle_message_parameters
        from discord.flags import MessageFlags
        from discord.components import Container

        if isinstance(container_payload, dict):
            container_obj = Container(container_payload)
        else:
            container_obj = container_payload

        flags = MessageFlags(components_v2=True)
        channel = await ctx._get_channel()
        state = ctx._state

        with handle_message_parameters(components=[container_obj], flags=flags) as params:
            data = await state.http.send_message(channel.id, params=params)
            return state.create_message(channel=channel, data=data)
    except Exception as exc:
        log.info("Components V2 send fallback: %s (%s)", exc, type(exc).__name__)
        if fallback_embed is not None:
            return await _safe_send(ctx, embed=fallback_embed, **kwargs)
        raise


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


def parse_date_input(text: str, now: datetime) -> Optional[date]:
    """Turn 'today', 'tomorrow', '2026-11-06', '06-11-2026', '6/11', etc. into a date object."""
    s = text.strip().lower().rstrip(".")
    if s in ("today", "now"):
        return now.date()
    if s in ("tomorrow", "tmr", "tmrw"):
        return now.date() + timedelta(days=1)
    if s in ("yesterday",):
        return now.date() - timedelta(days=1)

    # If it's a weekday name (e.g. "friday"), resolve to the upcoming occurrence
    day_key = resolve_day(s)
    if day_key:
        target_weekday = DAY_KEYS.index(day_key)
        days_ahead = (target_weekday - now.weekday()) % 7
        return now.date() + timedelta(days=days_ahead)

    formats = (
        "%Y-%m-%d",
        "%d-%m-%Y",
        "%d/%m/%Y",
        "%Y/%m/%d",
        "%d-%m",
        "%d/%m",
        "%d %b %Y",
        "%d %b",
        "%b %d",
    )
    for fmt in formats:
        try:
            parsed = datetime.strptime(s, fmt)
            yr = parsed.year if ("%Y" in fmt or "%y" in fmt) else now.year
            return date(yr, parsed.month, parsed.day)
        except ValueError:
            continue
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
    degree: str = ""
    year: str = ""
    name: str = ""
    roll_no: str = ""
    linked_at: str = ""

    @classmethod
    def from_dict(cls, user_id: str, data: dict[str, Any]) -> "UserProfile":
        return cls(
            user_id=str(user_id),
            batch=str(data.get("batch", "ALL")).strip().upper(),
            degree=str(data.get("degree", "")).strip(),
            year=str(data.get("year", "")).strip(),
            name=str(data.get("name", "")).strip(),
            roll_no=str(data.get("roll_no", "")).strip(),
            linked_at=str(data.get("linked_at", "")).strip(),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "batch": self.batch,
            "degree": self.degree,
            "year": self.year,
            "name": self.name,
            "roll_no": self.roll_no,
            "linked_at": self.linked_at,
        }


# --------------------------------------------------------------------------- #
# Class Representative Model
# --------------------------------------------------------------------------- #
@dataclass
class ClassRepresentative:
    user_id: int
    name: str
    batches: list[str]
    assigned_by: int = 0
    assigned_at: str = ""

    @classmethod
    def from_dict(cls, user_id: int | str, data: dict[str, Any]) -> "ClassRepresentative":
        raw_batches = data.get("batches")
        if not raw_batches:
            single = data.get("batch")
            raw_batches = [single] if single else ["ALL"]
        if isinstance(raw_batches, str):
            batches = [b.strip().upper() for b in raw_batches.split(",") if b.strip()]
        elif isinstance(raw_batches, list):
            batches = [str(b).strip().upper() for b in raw_batches if str(b).strip()]
        else:
            batches = ["ALL"]

        return cls(
            user_id=int(user_id),
            name=str(data.get("name", "")).strip(),
            batches=batches or ["ALL"],
            assigned_by=int(data.get("assigned_by", 0)),
            assigned_at=str(data.get("assigned_at", "")).strip(),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "name": self.name,
            "batches": self.batches,
            "assigned_by": self.assigned_by,
            "assigned_at": self.assigned_at,
        }

    @property
    def batch_str(self) -> str:
        return ", ".join(self.batches)


# --------------------------------------------------------------------------- #
# Student Roster Model & Parsers
# --------------------------------------------------------------------------- #
def natural_sort_key(s: Any) -> list[int | str]:
    """Natural sort key (e.g. '1', '2', '10' instead of '1', '10', '2')."""
    return [int(text) if text.isdigit() else text.lower() for text in re.split(r"(\d+)", str(s))]


def parse_bulk_roster(text: str) -> list[tuple[str, str]]:
    """Parse text of roll numbers and names in multiple formats (Excel tab, CSV, hyphen, colon)."""
    results: list[tuple[str, str]] = []
    lines = [l.strip() for l in text.strip().splitlines() if l.strip()]

    # If single line with commas and no other separators, split by comma: 101, 102, 103
    if len(lines) == 1 and "," in lines[0] and not any(sep in lines[0] for sep in (":", "-", "\t")):
        for token in lines[0].split(","):
            t = token.strip()
            if t:
                results.append((t, ""))
        return results

    for line in lines:
        if "\t" in line:
            parts = line.split("\t", 1)
            results.append((parts[0].strip(), parts[1].strip()))
        elif " - " in line:
            parts = line.split(" - ", 1)
            results.append((parts[0].strip(), parts[1].strip()))
        elif ":" in line:
            parts = line.split(":", 1)
            results.append((parts[0].strip(), parts[1].strip()))
        elif "," in line:
            parts = line.split(",", 1)
            results.append((parts[0].strip(), parts[1].strip()))
        else:
            results.append((line.strip(), ""))
    return results


@dataclass
class RosterStudent:
    roll_no: str
    name: str = ""
    user_id: Optional[str] = None
    added_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "roll_no": self.roll_no,
            "name": self.name,
            "user_id": self.user_id,
            "added_at": self.added_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RosterStudent":
        return cls(
            roll_no=str(data.get("roll_no", "")).strip(),
            name=str(data.get("name", "")).strip(),
            user_id=str(data["user_id"]).strip() if data.get("user_id") else None,
            added_at=str(data.get("added_at", "")).strip(),
        )


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
    note: str = ""
    is_override: bool = False
    status: str = ""

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

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "start": f"{self.start:%H:%M}",
            "end": f"{self.end:%H:%M}",
            "category": self.category,
            "batches": list(self.batches),
        }
        if self.subject:
            d["subject"] = self.subject
        if self.kind:
            d["kind"] = self.kind
        if self.room:
            d["room"] = self.room
        if self.faculty:
            d["faculty"] = self.faculty
        if self.label:
            d["label"] = self.label
        if self.note:
            d["note"] = self.note
        if self.is_override:
            d["is_override"] = True
        if self.status:
            d["status"] = self.status
        return d

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
            note=str(raw.get("note", "")).strip(),
            is_override=bool(raw.get("is_override", False)),
            status=str(raw.get("status", "")).strip(),
        )

    def block(self, *, quote: bool = True, ongoing: bool = False, show_batch: bool = True) -> str:
        """Render the slot as clean Discord markdown with headers and badges."""
        if self.is_free:
            here = "  ·  **[CURRENT BREAK]**" if ongoing else ""
            batch_tag = f"  ·  Batch {self.batch_str}" if (show_batch and not self.is_all_batches) else ""
            cancel_tag = "  ·  **[CANCELLED]**" if (self.is_override or self.status == "cancelled") else ""
            note_str = f"\n> *{self.note}*" if self.note else ""
            return f"-# **`{self.span}`**  ·  *{self.title.upper()}*{batch_tag}{cancel_tag}{here}{note_str}"

        badge = "  **[ONGOING NOW]**" if ongoing else ""
        kind_str = f" `[{self.kind}]`" if self.kind else ""
        batch_badge = f" `[Batch {self.batch_str}]`" if (show_batch and not self.is_all_batches) else ""

        override_badge = ""
        if self.status == "cancelled":
            override_badge = " `[CANCELLED]`"
        elif self.status == "rescheduled" or self.is_override:
            override_badge = " `[RESCHEDULED]`"
        elif self.status == "extra":
            override_badge = " `[EXTRA SESSION]`"

        header = f"### `{self.span}` • {self.subject}{kind_str}{batch_badge}{override_badge}{badge}"
        meta: list[str] = []
        if self.room:
            meta.append(f"Room: **{self.room}**")
        if self.faculty:
            meta.append(f"Faculty: **{self.faculty}**")
        if show_batch and not self.is_all_batches:
            meta.append(f"Batch: **{self.batch_str}**")

        note_str = f"\n> *Note: {self.note}*" if self.note else ""
        if meta:
            prefix = "> " if quote else ""
            return f"{header}\n{prefix}{'   •   '.join(meta)}{note_str}"
        return f"{header}{note_str}"


# --------------------------------------------------------------------------- #
# Embed builders
# --------------------------------------------------------------------------- #
def error_embed(message: str, title: str = "Timetable Error") -> discord.Embed:
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
        title = f"Today's Timetable — {name} ({now:%d %b}){batch_tag}"
    else:
        title = f"{name}'s Timetable{batch_tag}"

    if is_weekend:
        embed = discord.Embed(
            title=title,
            description=(
                f"> **Weekend Break — {name}**\n"
                "> No lectures or labs scheduled today.\n"
                "> Time to relax and recharge."
            ),
            color=COLOR_SUCCESS,
            timestamp=now,
        )
    else:
        b_note = f" for **Batch {query_batch.upper()}**" if query_batch else f" for **{name}**"
        embed = discord.Embed(
            title=title,
            description=(
                f"> **No classes scheduled{b_note}.**\n"
                "> You have a free day.\n\n"
                f"-# *Need to add classes? Use `/setclass {key} <time> <subject>`.*"
            ),
            color=COLOR_PRIMARY,
            timestamp=now,
        )
    embed.set_author(name="Timetable Bot")

    upcoming = _next_class_day(days, key, query_batch=query_batch)
    if upcoming:
        nkey, slot = upcoming
        kind_str = f" `[{slot.kind}]`" if slot.kind else ""
        room_str = f" in `{slot.room}`" if slot.room else ""
        batch_info = f" (Batch {slot.batch_str})" if not slot.is_all_batches else ""
        embed.add_field(
            name="Next In Schedule",
            value=(
                f"Your next scheduled class is on **{DAY_NAMES[nkey]}** at `{slot.span}`:\n"
                f"> **{slot.subject}**{kind_str}{batch_info}{room_str}"
            ),
            inline=False,
        )

    if user_profile and query_batch == user_profile.batch:
        footer_text = f"Linked to Batch {user_profile.batch} • Timezone: {now.tzname() or 'Local'} • /profile for details"
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
    target_date: Optional[date] = None,
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
    batch_summary = f"   •   **Batch {query_batch.upper()}**" if query_batch else ""
    has_overrides = any(s.is_override for s in slots)
    override_summary = "   •   **[Overrides Active]**" if has_overrides else ""
    summary = (
        f"> **{len(classes)} {noun}**   •   **{_fmt_minutes(total)} teaching**   •   "
        f"**{classes[0].start:%H:%M} → {classes[-1].end:%H:%M}**{batch_summary}{override_summary}"
    )
    description = summary + "\n\n" + "\n\n".join(blocks)
    if len(description) > 4000:
        description = description[:3990] + "\n…"

    batch_tag = f" • Batch {query_batch.upper()}" if query_batch else ""
    date_label = f" ({target_date:%d %b})" if target_date else (f" ({now:%d %b})" if highlight_now else "")
    title = (
        f"Today's Timetable — {DAY_NAMES[key]}{date_label}{batch_tag}"
        if highlight_now
        else f"{DAY_NAMES[key]}'s Timetable{date_label}{batch_tag}"
    )
    embed = discord.Embed(
        title=title,
        description=description,
        color=COLOR_SUCCESS if any_ongoing else COLOR_PRIMARY,
        timestamp=now,
    )
    embed.set_author(name="Timetable Bot")

    if user_profile and query_batch == user_profile.batch:
        footer_text = f"Linked to Batch {user_profile.batch} • Timezone: {now.tzname() or 'Local'} • /profile for details"
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
        title=f"Weekly Timetable Overview{batch_title}",
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
        indicator = "[Today] " if is_today else ""
        field_name = f"{indicator}{day_name}" + ("" if is_today else "")

        if not classes:
            embed.add_field(
                name=field_name,
                value="-# *No classes scheduled*",
                inline=False,
            )
        else:
            lines = []
            for c in classes:
                kind_str = f" `[{c.kind}]`" if c.kind else ""
                batch_str = f" `[Batch {c.batch_str}]`" if (query_batch is None and not c.is_all_batches) else ""
                room_str = f" • `{c.room}`" if c.room else ""
                lines.append(f"• `{c.start:%H:%M} – {c.end:%H:%M}` **{c.subject}**{kind_str}{batch_str}{room_str}")
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
                f"• `{c.start:%H:%M} – {c.end:%H:%M}` **{c.subject}**"
                for c in weekend_classes
            ]
            embed.add_field(
                name=f"**{day_name}** — `{len(weekend_classes)} classes`",
                value="\n".join(lines),
                inline=False,
            )

    noun = "class" if total_classes == 1 else "classes"
    batch_desc = f" for **Batch {query_batch.upper()}**" if query_batch else ""
    embed.description = f"> **{total_classes} {noun}** this week{batch_desc}   •   **{_fmt_minutes(total_minutes)} total teaching time**"

    if user_profile and query_batch == user_profile.batch:
        footer_text = f"Linked to Batch {user_profile.batch} • Timezone: {now.tzname() or 'Local'} • /timetable for day view"
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
                        "This batch selector is for the user who ran the command. Run `/link` to choose yours.",
                        ephemeral=True,
                    )
                return
            prof = self.cog.set_user_profile(
                user_id=interaction.user.id,
                batch=self.batch_name,
                name=getattr(interaction.user, "display_name", ""),
            )
            embed = discord.Embed(
                title="Account Linked Successfully",
                description=(
                    f"Successfully linked {interaction.user.mention} to **Batch {prof.batch}**.\n\n"
                    "Now whenever you run `/timetable`, `/next`, or `/week`, "
                    f"the bot will automatically load the timetable for **Batch {prof.batch}**."
                ),
                color=COLOR_SUCCESS,
            )
            embed.set_author(name="Timetable Bot")
            embed.add_field(name="Student", value=interaction.user.mention, inline=True)
            embed.add_field(name="Linked Batch", value=f"**Batch {prof.batch}**", inline=True)
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
    def read_raw(self, batch: Optional[str] = None) -> dict[str, list]:
        source_file = DATA_FILE
        if batch and batch.strip().upper() not in ("ALL", "*", "ANY"):
            candidate = DATA_FILE.parent / "timetables" / "converted" / f"{batch.strip().upper()}.json"
            if candidate.is_file():
                source_file = candidate

        try:
            raw = json.loads(source_file.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise ScheduleError(f"I couldn't find `{source_file.name}`.") from None
        except json.JSONDecodeError as exc:
            raise ScheduleError(
                f"`{source_file.name}` has a syntax error (line {exc.lineno}, column {exc.colno})."
            ) from exc
        except (OSError, UnicodeDecodeError) as exc:
            raise ScheduleError(f"I couldn't read `{source_file.name}` ({type(exc).__name__}).") from exc
        if not isinstance(raw, dict):
            raise ScheduleError(f"`{source_file.name}` must be a JSON object keyed by day (mon, tue...).")

        data: dict[str, list] = {}
        for key, slots in raw.items():
            k = str(key).lower()
            if k in DAY_KEYS and isinstance(slots, list):
                data[k] = slots
        for k in DAY_KEYS[:5]:
            data.setdefault(k, [])
        return data

    def write_raw(self, data: dict[str, list], batch: Optional[str] = None) -> None:
        ordered = {k: data[k] for k in DAY_KEYS if k in data}
        target_file = DATA_FILE
        if batch and batch.strip().upper() not in ("ALL", "*", "ANY"):
            candidate = DATA_FILE.parent / "timetables" / "converted" / f"{batch.strip().upper()}.json"
            if candidate.is_file() or candidate.parent.is_dir():
                target_file = candidate

        target_file.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=target_file.parent, prefix=f".{target_file.stem}-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(ordered, fh, indent=2, ensure_ascii=False)
                fh.write("\n")
            os.replace(tmp, target_file)  # atomic: never leaves a half-written file
        except Exception as exc:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            if isinstance(exc, OSError):
                raise ScheduleError(f"I couldn't save the schedule ({type(exc).__name__}).") from exc
            raise

    # ----- date-specific overrides ----------------------------------------- #
    def read_overrides(self) -> dict[str, list[dict]]:
        """Read all single-date overrides from data/overrides.json."""
        if not OVERRIDES_FILE.is_file():
            return {}
        try:
            raw = json.loads(OVERRIDES_FILE.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                return raw
        except Exception as exc:
            log.warning("Could not read overrides.json: %s", exc)
        return {}

    def write_overrides(self, data: dict[str, list[dict]]) -> None:
        """Atomically persist date overrides to data/overrides.json."""
        OVERRIDES_FILE.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=OVERRIDES_FILE.parent, prefix=".overrides-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2, ensure_ascii=False)
                fh.write("\n")
            os.replace(tmp, OVERRIDES_FILE)
        except Exception as exc:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise ScheduleError(f"I couldn't save overrides ({type(exc).__name__}).") from exc

    def apply_date_overrides(
        self, slots: list[Slot], target_date: date, batch: Optional[str] = None
    ) -> list[Slot]:
        """Overlay any date-specific overrides on top of recurring weekly slots."""
        date_str = target_date.isoformat()
        all_ovs = self.read_overrides()
        date_ovs = all_ovs.get(date_str, [])
        if not date_ovs:
            return slots

        matching_ovs: list[Slot] = []
        for raw in date_ovs:
            try:
                ov_slot = Slot.from_dict(raw)
                if slot_matches_batch(ov_slot, batch):
                    matching_ovs.append(ov_slot)
            except Exception as exc:
                log.warning("Bad override on %s: %s", date_str, exc)

        if not matching_ovs:
            return slots

        merged: list[Slot] = []
        ov_used = set()
        for s in slots:
            matched = False
            for idx, ov in enumerate(matching_ovs):
                if idx in ov_used:
                    continue
                time_overlap = s.start < ov.end and ov.start < s.end
                if s.start == ov.start or time_overlap:
                    merged.append(ov)
                    ov_used.add(idx)
                    matched = True
                    break
            if not matched:
                merged.append(s)

        for idx, ov in enumerate(matching_ovs):
            if idx not in ov_used:
                merged.append(ov)

        merged.sort(key=lambda s: s.start)
        return merged

    def read_all(
        self, batch: Optional[str] = None, for_date: Optional[date] = None
    ) -> dict[str, list[Slot]]:
        """Parsed + sorted slots for every day. Applies date overrides if for_date is provided."""
        parsed: dict[str, list[Slot]] = {k: [] for k in DAY_KEYS}
        for key, entries in self.read_raw(batch=batch).items():
            for i, raw in enumerate(entries):
                try:
                    parsed[key].append(Slot.from_dict(raw))
                except ValueError as exc:
                    log.warning("Skipping bad slot #%d on %s: %s", i, key, exc)
            parsed[key].sort(key=lambda s: s.start)

        if for_date is not None:
            day_key = DAY_KEYS[for_date.weekday()]
            parsed[day_key] = self.apply_date_overrides(parsed[day_key], for_date, batch=batch)

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
        degree: str = "",
        year: str = "",
    ) -> UserProfile:
        """Link or update a user's batch profile with optional degree and year."""
        profiles = self.read_profiles()
        uid = str(user_id)
        existing = profiles.get(uid)
        now_iso = self._now().isoformat()
        clean_batches = parse_batches(batch)
        clean_batch = clean_batches[0] if clean_batches else batch.strip().upper()

        deg = degree.strip() or (existing.degree if existing else "")
        yr = year.strip() or (existing.year if existing else "")
        if (not deg or not yr) and INDEX_FILE.is_file():
            try:
                idx = json.loads(INDEX_FILE.read_text(encoding="utf-8"))
                b_info = idx.get("batches", {}).get(clean_batch)
                if b_info:
                    if not deg and b_info.get("degrees"):
                        deg = b_info["degrees"][0]
                    if not yr and b_info.get("years"):
                        yr = b_info["years"][0]
            except Exception:
                pass

        profile = UserProfile(
            user_id=uid,
            batch=clean_batch,
            degree=deg,
            year=yr,
            name=name.strip() or (existing.name if existing else ""),
            roll_no=roll_no.strip() or (existing.roll_no if existing else ""),
            linked_at=existing.linked_at if existing and existing.linked_at else now_iso,
        )
        profiles[uid] = profile
        self.write_profiles(profiles)

        # Sync with roster if roll_no is specified
        if profile.roll_no:
            try:
                self.link_user_to_roster(
                    user_id=uid,
                    batch=clean_batch,
                    roll_no=profile.roll_no,
                    name=profile.name,
                )
            except Exception as exc:
                log.warning("Could not sync user profile to roster: %s", exc)

        return profile

    def delete_user_profile(self, user_id: str | int) -> bool:
        """Unlink a user profile. Returns True if removed, False if not found."""
        profiles = self.read_profiles()
        uid = str(user_id)
        if uid in profiles:
            del profiles[uid]
            self.write_profiles(profiles)
            with contextlib.suppress(Exception):
                self.unlink_user_from_roster(uid)
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

    # ----- class representatives ---------------------------------------- #
    def read_representatives(self) -> dict[int, ClassRepresentative]:
        """Read all class representatives from representatives.json."""
        reps: dict[int, ClassRepresentative] = {}
        if not REPRESENTATIVES_FILE.is_file():
            return reps
        try:
            raw = json.loads(REPRESENTATIVES_FILE.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                for uid_str, data in raw.items():
                    if isinstance(data, dict):
                        try:
                            uid = int(uid_str)
                            reps[uid] = ClassRepresentative.from_dict(uid, data)
                        except (ValueError, TypeError):
                            continue
        except Exception as exc:
            log.warning("Could not read representatives.json: %s", exc)
        return reps

    def write_representatives(self, reps: dict[int, ClassRepresentative]) -> None:
        """Atomically persist class representatives to representatives.json."""
        REPRESENTATIVES_FILE.parent.mkdir(parents=True, exist_ok=True)
        raw = {str(uid): rep.to_dict() for uid, rep in reps.items()}
        fd, tmp = tempfile.mkstemp(dir=REPRESENTATIVES_FILE.parent, prefix=".reps-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(raw, fh, indent=2, ensure_ascii=False)
                fh.write("\n")
            os.replace(tmp, REPRESENTATIVES_FILE)
        except Exception as exc:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise ScheduleError(f"I couldn't save representatives ({type(exc).__name__}).") from exc

    def get_representative(self, user_id: int | str) -> Optional[ClassRepresentative]:
        """Get the ClassRepresentative object for user_id if authorized, else None."""
        try:
            uid = int(user_id)
        except (ValueError, TypeError):
            return None
        return self.read_representatives().get(uid)

    def set_representative(
        self,
        user_id: int | str,
        name: str,
        batches: list[str],
        assigned_by: int = 0,
    ) -> ClassRepresentative:
        """Authorize or update a user as Class Representative for one or more batches."""
        reps = self.read_representatives()
        uid = int(user_id)
        clean_batches = [b.strip().upper() for b in batches if b.strip()] or ["ALL"]
        now_iso = self._now().isoformat()

        rep = ClassRepresentative(
            user_id=uid,
            name=name.strip(),
            batches=clean_batches,
            assigned_by=assigned_by,
            assigned_at=now_iso,
        )
        reps[uid] = rep
        self.write_representatives(reps)
        return rep

    def delete_representative(self, user_id: int | str, batch: Optional[str] = None) -> bool:
        """Revoke CR status for a user (or for a specific batch). Returns True if removed/modified."""
        reps = self.read_representatives()
        uid = int(user_id)
        if uid not in reps:
            return False

        if batch:
            target = batch.strip().upper()
            rep = reps[uid]
            if target in rep.batches:
                rep.batches.remove(target)
                if not rep.batches:
                    del reps[uid]
                else:
                    reps[uid] = rep
                self.write_representatives(reps)
                return True
            return False

        del reps[uid]
        self.write_representatives(reps)
        return True

    def is_representative(self, user_id: int | str) -> bool:
        """Check if user is a registered Class Representative for any batch."""
        return self.get_representative(user_id) is not None

    def get_representative_batches(self, user_id: int | str) -> list[str]:
        """Get the list of batches authorized for this CR, or empty list."""
        rep = self.get_representative(user_id)
        return list(rep.batches) if rep else []

    def can_edit_batch(self, user_id: int | str, batch: str) -> bool:
        """Check if user_id is authorized to edit the specified batch."""
        clean = batch.strip().upper()
        batches = self.get_representative_batches(user_id)
        return "ALL" in batches or clean in batches

    def resolve_batch_input(
        self,
        raw_batch: Optional[str],
        user_profile: Optional[UserProfile] = None,
    ) -> str:
        """Resolve user-friendly batch inputs (e.g. 'AI 16', 'Core 35', 'Cyber 59', 'DS 57', '16',
        or full code '26CAIBTAIB16') into an official batch code.
        """
        if not raw_batch or raw_batch.strip().upper() in ("ALL", "*", "ANY"):
            return "ALL"

        b_clean = raw_batch.strip().upper()
        if INDEX_FILE.is_file():
            try:
                idx = json.loads(INDEX_FILE.read_text(encoding="utf-8"))
                if b_clean in idx.get("batches", {}):
                    return b_clean
            except Exception:
                pass

        # If user has a registered degree/year, try with their profile first
        deg = user_profile.degree if user_profile else ""
        yr = user_profile.year if user_profile else ""
        if deg or yr:
            resolved, _, _, _ = resolve_batch_from_inputs(deg, yr, b_clean)
            if resolved:
                return resolved

        # Try across all batches in index
        resolved_any, _, _, _ = resolve_batch_from_inputs("", "", b_clean)
        if resolved_any:
            return resolved_any

        return b_clean

    def get_known_batches(self) -> list[str]:
        """Scan schedule slots, linked profiles, rosters, and batch index to discover active batches."""
        found: set[str] = set()
        index_file = DATA_FILE.parent / "batches_index.json"
        if index_file.is_file():
            try:
                idx_data = json.loads(index_file.read_text(encoding="utf-8"))
                for b_code in idx_data.get("batches", {}).keys():
                    if b_code and b_code != "ALL":
                        found.add(b_code)
            except Exception:
                pass
        for slots in self.read_all().values():
            for s in slots:
                for b in s.batches:
                    if b and b != "ALL":
                        found.add(b)
        for prof in self.read_profiles().values():
            if prof.batch and prof.batch != "ALL":
                found.add(prof.batch)
        try:
            for b in self.read_roster().keys():
                if b and b != "ALL":
                    found.add(b)
        except Exception:
            pass
        return sorted(found) if found else ["1", "2"]

    # ----- student roster & roll numbers ---------------------------------- #
    def read_roster(self) -> dict[str, dict[str, RosterStudent]]:
        """Read all student rosters grouped by batch: batch -> {roll_no -> RosterStudent}."""
        roster: dict[str, dict[str, RosterStudent]] = {}
        if ROSTER_FILE.is_file():
            try:
                raw = json.loads(ROSTER_FILE.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    for b_key, students in raw.items():
                        b_up = str(b_key).strip().upper()
                        if isinstance(students, dict):
                            roster[b_up] = {}
                            for r_no, sdata in students.items():
                                if isinstance(sdata, dict):
                                    roster[b_up][str(r_no)] = RosterStudent.from_dict(sdata)
            except Exception as exc:
                log.warning("Could not read %s: %s", ROSTER_FILE, exc)

        # Automatic sync: Ensure any linked profiles from users.json exist in roster
        profiles = self.read_profiles()
        needs_write = False
        for uid, p in profiles.items():
            if p.batch and p.roll_no:
                b_up = p.batch.upper()
                roster.setdefault(b_up, {})
                if p.roll_no not in roster[b_up]:
                    roster[b_up][p.roll_no] = RosterStudent(
                        roll_no=p.roll_no,
                        name=p.name,
                        user_id=uid,
                        added_at=p.linked_at or self._now().isoformat(),
                    )
                    needs_write = True
                elif not roster[b_up][p.roll_no].user_id:
                    roster[b_up][p.roll_no].user_id = uid
                    if p.name and not roster[b_up][p.roll_no].name:
                        roster[b_up][p.roll_no].name = p.name
                    needs_write = True

        if needs_write and not ROSTER_FILE.is_file():
            try:
                self.write_roster(roster)
            except Exception:
                pass

        return roster

    def write_roster(self, roster: dict[str, dict[str, RosterStudent]]) -> None:
        """Atomically persist student roster to roster.json."""
        ROSTER_FILE.parent.mkdir(parents=True, exist_ok=True)
        raw = {
            b_name: {r_no: stud.to_dict() for r_no, stud in studs.items()}
            for b_name, studs in roster.items()
        }
        fd, tmp = tempfile.mkstemp(dir=ROSTER_FILE.parent, prefix=".roster-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(raw, fh, indent=2, ensure_ascii=False)
                fh.write("\n")
            os.replace(tmp, ROSTER_FILE)
        except Exception as exc:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise ScheduleError(f"I couldn't save student roster ({type(exc).__name__}).") from exc

    def get_batch_students(self, batch: str) -> list[RosterStudent]:
        """Get all students for a batch, sorted naturally by roll number."""
        b_up = batch.strip().upper()
        roster = self.read_roster()
        batch_dict = roster.get(b_up, {})
        students = list(batch_dict.values())
        students.sort(key=lambda s: natural_sort_key(s.roll_no))
        return students

    def populate_batch_range(
        self,
        batch: str,
        start: int,
        end: int,
        prefix: str = "",
        padding: int = 0,
    ) -> int:
        """Populate a sequential range of roll numbers under a batch."""
        if start > end:
            raise ValueError(f"Start ({start}) cannot be greater than end ({end}).")
        if (end - start + 1) > 500:
            raise ValueError("Range cannot exceed 500 roll numbers at a time.")

        b_up = batch.strip().upper()
        roster = self.read_roster()
        batch_dict = roster.setdefault(b_up, {})
        now_iso = self._now().isoformat()
        added_count = 0

        for num in range(start, end + 1):
            if padding > 0:
                num_str = f"{num:0{padding}d}"
            else:
                num_str = str(num)
            roll_str = f"{prefix.strip()}{num_str}"
            if roll_str not in batch_dict:
                batch_dict[roll_str] = RosterStudent(
                    roll_no=roll_str,
                    name="",
                    user_id=None,
                    added_at=now_iso,
                )
                added_count += 1

        self.write_roster(roster)
        return added_count

    def populate_batch_bulk(self, batch: str, text: str) -> tuple[int, int]:
        """Bulk add students from text lines or comma-separated tokens."""
        items = parse_bulk_roster(text)
        if not items:
            return 0, 0

        b_up = batch.strip().upper()
        roster = self.read_roster()
        batch_dict = roster.setdefault(b_up, {})
        now_iso = self._now().isoformat()
        added = 0
        updated = 0

        for roll_str, name_str in items:
            if not roll_str:
                continue
            if roll_str in batch_dict:
                if name_str:
                    batch_dict[roll_str].name = name_str
                    updated += 1
            else:
                batch_dict[roll_str] = RosterStudent(
                    roll_no=roll_str,
                    name=name_str,
                    user_id=None,
                    added_at=now_iso,
                )
                added += 1

        self.write_roster(roster)
        return added, updated

    def add_roster_student(
        self,
        batch: str,
        roll_no: str,
        name: str = "",
        user_id: Optional[str | int] = None,
    ) -> RosterStudent:
        """Add or update an individual student roll number in a batch."""
        b_up = batch.strip().upper()
        r_clean = roll_no.strip()
        uid_str = str(user_id) if user_id else None

        roster = self.read_roster()
        batch_dict = roster.setdefault(b_up, {})
        now_iso = self._now().isoformat()

        if r_clean in batch_dict:
            stud = batch_dict[r_clean]
            if name.strip():
                stud.name = name.strip()
            if uid_str is not None:
                stud.user_id = uid_str
        else:
            stud = RosterStudent(
                roll_no=r_clean,
                name=name.strip(),
                user_id=uid_str,
                added_at=now_iso,
            )
            batch_dict[r_clean] = stud

        self.write_roster(roster)

        # If user_id was given, also update users.json profile
        if uid_str:
            profiles = self.read_profiles()
            existing = profiles.get(uid_str)
            profiles[uid_str] = UserProfile(
                user_id=uid_str,
                batch=b_up,
                name=stud.name or (existing.name if existing else ""),
                roll_no=stud.roll_no,
                linked_at=existing.linked_at if existing and existing.linked_at else now_iso,
            )
            self.write_profiles(profiles)

        return stud

    def delete_roster_student(self, batch: str, roll_no: str) -> bool:
        """Remove a roll number from a batch roster."""
        b_up = batch.strip().upper()
        r_clean = roll_no.strip()
        roster = self.read_roster()
        batch_dict = roster.get(b_up)
        if not batch_dict or r_clean not in batch_dict:
            return False

        stud = batch_dict.pop(r_clean)
        self.write_roster(roster)

        # If linked to a user_id, unlink their roll number in users.json
        if stud.user_id:
            prof = self.get_user_profile(stud.user_id)
            if prof and prof.roll_no == r_clean:
                self.set_user_profile(
                    user_id=stud.user_id,
                    batch=prof.batch,
                    name=prof.name,
                    roll_no="",
                )
        return True

    def clear_batch_roster(self, batch: str) -> int:
        """Clear all student roll numbers under a batch."""
        b_up = batch.strip().upper()
        roster = self.read_roster()
        batch_dict = roster.get(b_up, {})
        count = len(batch_dict)
        if count > 0:
            roster[b_up] = {}
            self.write_roster(roster)
        return count

    def link_user_to_roster(
        self,
        user_id: str | int,
        batch: str,
        roll_no: str,
        name: str = "",
    ) -> RosterStudent:
        """Link a Discord user to a specific roll number in a batch."""
        b_up = batch.strip().upper()
        r_clean = roll_no.strip()
        uid_str = str(user_id)

        # First unlink this user from any previous roll in the roster
        self.unlink_user_from_roster(uid_str)

        # Now link to target roll
        stud = self.add_roster_student(
            batch=b_up,
            roll_no=r_clean,
            name=name,
            user_id=uid_str,
        )
        return stud

    def unlink_user_from_roster(self, user_id: str | int) -> Optional[tuple[str, str]]:
        """Remove user_id binding from any roster entry. Returns (batch, roll_no) if found."""
        uid_str = str(user_id)
        roster = self.read_roster()
        found = None

        for b_name, studs in roster.items():
            for r_no, s in studs.items():
                if s.user_id == uid_str:
                    s.user_id = None
                    found = (b_name, r_no)

        if found:
            self.write_roster(roster)
        return found

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

        target_batch = parsed_batches[0] if (parsed_batches and parsed_batches[0] != "ALL") else None
        async with self.lock:
            data = self.read_raw(batch=target_batch)
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
            self.write_raw(data, batch=target_batch)
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
            data = self.read_raw(batch=target_batch)
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
            self.write_raw(data, batch=target_batch)
        return removed_slot

    async def set_override(
        self,
        target_date: date,
        time_slot: str,
        subject: str,
        batch: str = "ALL",
        room: str = "",
        faculty: str = "",
        note: str = "",
        status: str = "rescheduled",
    ) -> tuple[str, Slot]:
        """Add or update a date-specific class override for a particular hour."""
        match = _SLOT_RE.match(time_slot)
        if not match:
            single_m = re.match(r"^\s*(\d{1,2}:\d{2})\s*$", time_slot)
            if single_m:
                st = _parse_hhmm(single_m.group(1))
                end = time(min(23, st.hour + 1), st.minute)
            else:
                raise ScheduleError("Time slot must look like `09:30-10:30` (or `09:30`).")
        else:
            try:
                st, end = _parse_hhmm(match["start"]), _parse_hhmm(match["end"])
            except ValueError:
                raise ScheduleError("Invalid time. Use 24-hour `HH:MM`.") from None
        if end <= st:
            raise ScheduleError("End time must be after start time.")

        subject = subject.strip()
        room = room.strip()
        faculty = faculty.strip()
        note = note.strip()
        parsed_batches = parse_batches(batch)

        date_key = target_date.isoformat()
        is_free_or_cancelled = (
            subject.lower() in _FREE_WORDS
            or subject.lower() in ("cancelled", "cancel", "no class")
            or status == "cancelled"
        )

        entry: dict[str, Any] = {
            "start": f"{st:%H:%M}",
            "end": f"{end:%H:%M}",
            "batches": parsed_batches,
            "note": note,
            "is_override": True,
            "status": "cancelled" if ("cancel" in subject.lower() or status == "cancelled") else status,
        }

        if is_free_or_cancelled:
            lbl = subject if subject.upper() not in ("FREE", "FREE PERIOD") else "FREE PERIOD"
            if "cancel" in subject.lower() or status == "cancelled":
                lbl = f"CANCELLED: {note or 'Class'}"
            entry.update(category="free", label=lbl)
        else:
            name, kind = subject, ""
            m = _KIND_RE.match(subject)
            if m:
                name, kind = m["name"].strip(), m["kind"].strip()
            entry.update(
                category="class",
                subject=name,
                kind=kind,
                room=room,
                faculty=faculty,
            )

        new_slot = Slot.from_dict(entry)

        async with self.lock:
            all_ovs = self.read_overrides()
            cur_list = all_ovs.setdefault(date_key, [])
            kept = []
            action = "added"
            for raw in cur_list:
                try:
                    existing = Slot.from_dict(raw)
                    if existing.start == new_slot.start and set(existing.batches) == set(new_slot.batches):
                        action = "updated"
                        continue
                except Exception:
                    pass
                kept.append(raw)

            kept.append(new_slot.to_dict())
            kept.sort(key=lambda r: r.get("start", ""))
            all_ovs[date_key] = kept
            self.write_overrides(all_ovs)

        return action, new_slot

    async def delete_override(
        self, target_date: date, start_text: str, batch: Optional[str] = None
    ) -> Optional[Slot]:
        """Delete an override for a date and start time."""
        try:
            start = _parse_hhmm(start_text)
        except ValueError:
            raise ScheduleError("Start time must be `HH:MM`, e.g. `09:30`.")
        date_key = target_date.isoformat()
        target_batch = batch.strip().upper() if batch else None

        async with self.lock:
            all_ovs = self.read_overrides()
            cur_list = all_ovs.get(date_key, [])
            found_slot: Optional[Slot] = None
            kept = []
            for raw in cur_list:
                try:
                    s = Slot.from_dict(raw)
                    if s.start == start:
                        if target_batch is None or target_batch in s.batches or (target_batch in ("ALL", "*", "ANY") and "ALL" in s.batches):
                            found_slot = s
                            continue
                except Exception:
                    pass
                kept.append(raw)

            if found_slot:
                if kept:
                    all_ovs[date_key] = kept
                else:
                    all_ovs.pop(date_key, None)
                self.write_overrides(all_ovs)
            return found_slot

    # ----- embeds ---------------------------------------------------------- #
    def _status_embed(
        self,
        now: datetime,
        query_batch: Optional[str] = None,
        user_profile: Optional[UserProfile] = None,
    ) -> discord.Embed:
        effective_batch = (query_batch.strip().upper() if query_batch else None) or (
            user_profile.batch if user_profile and user_profile.batch != "ALL" else None
        )
        days = self.read_all(batch=effective_batch, for_date=now.date())
        key = DAY_KEYS[now.weekday()]
        all_today = days[key]
        today = [s for s in all_today if slot_matches_batch(s, query_batch)]
        t = now.time()
        tz = now.tzinfo

        current = next((s for s in today if s.start <= t < s.end), None)
        upcoming: Optional[tuple[int, date, Slot]] = None
        for offset in range(8):
            d = now.date() + timedelta(days=offset)
            d_key = DAY_KEYS[d.weekday()]
            day_slots = self.apply_date_overrides(days[d_key], d, batch=effective_batch) if offset > 0 else today
            for slot in day_slots:
                if slot.is_free or (offset == 0 and slot.start <= t):
                    continue
                if not slot_matches_batch(slot, query_batch):
                    continue
                upcoming = (offset, d, slot)
                break
            if upcoming:
                break
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

        batch_str = f"   •   **Batch {query_batch.upper()}**" if query_batch else ""
        embed = discord.Embed(
            title="Live Class Status",
            description=f"**{now:%A, %d %B}**   •   **{now:%H:%M} {now.tzname() or ''}**{batch_str}",
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
                embed.add_field(
                    name="Current Break · In Progress",
                    value=(
                        f"### {current.title}\n"
                        f"> **`{current.span}`** • Ends <t:{end_ts}:R> (<t:{end_ts}:t>)\n"
                        f"> Progress: {progress_str}"
                    ),
                    inline=False,
                )
            else:
                embed.color = COLOR_SUCCESS
                kind_str = f" `[{current.kind}]`" if current.kind else ""
                batch_tag = f" `[Batch {current.batch_str}]`" if (query_batch is None and not current.is_all_batches) else ""
                details = [f"**`{current.span}`** • Ends <t:{end_ts}:R> (<t:{end_ts}:t>)"]
                if current.room:
                    details.append(f"Room: **{current.room}**")
                if current.faculty:
                    details.append(f"Faculty: **{current.faculty}**")
                if query_batch is None and not current.is_all_batches:
                    details.append(f"Batch: **{current.batch_str}**")
                details.append(f"Progress: {progress_str}")

                detail_text = "\n> ".join(details)
                embed.add_field(
                    name="Happening Right Now",
                    value=f"### {current.subject}{kind_str}{batch_tag}\n> {detail_text}",
                    inline=False,
                )
        else:
            if not today:
                b_note = f" for Batch {query_batch.upper()}" if query_batch else ""
                note = f"No classes are scheduled{b_note} on the timetable today."
            elif t >= today[-1].end:
                note = "**All classes for today are done.** Have a good evening."
            elif t < today[0].start:
                note = f"**Day has not started yet.** First class begins at `{today[0].start:%H:%M}`."
            else:
                note = "**Break time.** You are between scheduled slots right now."
            embed.add_field(name="Current Status", value=f"> {note}", inline=False)

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
            kind_str = f" `[{slot.kind}]`" if slot.kind else ""
            batch_tag = f" `[Batch {slot.batch_str}]`" if (query_batch is None and not slot.is_all_batches) else ""

            details = [f"**`{slot.span}`** • Starts <t:{start_ts}:R> (<t:{start_ts}:t>)"]
            if slot.room:
                details.append(f"Room: **{slot.room}**")
            if slot.faculty:
                details.append(f"Faculty: **{slot.faculty}**")
            if query_batch is None and not slot.is_all_batches:
                details.append(f"Batch: **{slot.batch_str}**")

            detail_text = "\n> ".join(details)
            embed.add_field(
                name=f"Up Next • {when}",
                value=f"### {slot.subject}{kind_str}{batch_tag}\n> {detail_text}",
                inline=False,
            )
        else:
            embed.add_field(
                name="Up Next",
                value="> No more classes found in your timetable schedule.",
                inline=False,
            )

        if user_profile and query_batch == user_profile.batch:
            footer_text = f"Linked to Batch {user_profile.batch} • Timezone: {now.tzname() or 'Local'} • Run /profile to view"
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
        target_date: Optional[date] = None
        if day is None or not day.strip():
            key = today_key
            target_date = now.date()
        else:
            target_date = parse_date_input(day, now)
            key = resolve_day(day, now)
            if key is None and target_date is not None:
                key = DAY_KEYS[target_date.weekday()]
            if key is None:
                await _safe_send(
                    ctx,
                    embed=error_embed(
                        f"I don't know the day `{day[:30]}`.\nTry `mon`, `tue`, `wed`, "
                        "`thu`, `fri`, `today` or `tomorrow`, or a date like `2026-11-06`."
                    ),
                    ephemeral=True,
                )
                return

        profile = self.get_user_profile(ctx.author.id)
        effective_batch = (self.resolve_batch_input(batch, profile) if batch else None) or (profile.batch if profile and profile.batch != "ALL" else None)
        if not effective_batch:
            prefix = ctx.prefix if ctx.prefix and not ctx.prefix.startswith("/") else "!"
            c_v2 = build_unregistered_component_v2(prefix)
            f_emb = build_unregistered_embed(prefix)
            await send_component_v2(ctx, c_v2, fallback_embed=f_emb, ephemeral=True)
            return

        embed = build_day_embed(
            key,
            self.read_all(batch=effective_batch, for_date=target_date),
            now,
            highlight_now=(key == today_key and (target_date is None or target_date == now.date())),
            query_batch=effective_batch,
            user_profile=profile,
            target_date=target_date,
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
        effective_batch = (self.resolve_batch_input(batch, profile) if batch else None) or (profile.batch if profile and profile.batch != "ALL" else None)
        if not effective_batch:
            prefix = ctx.prefix if ctx.prefix and not ctx.prefix.startswith("/") else "!"
            c_v2 = build_unregistered_component_v2(prefix)
            f_emb = build_unregistered_embed(prefix)
            await send_component_v2(ctx, c_v2, fallback_embed=f_emb, ephemeral=True)
            return

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
        effective_batch = (self.resolve_batch_input(batch, profile) if batch else None) or (profile.batch if profile and profile.batch != "ALL" else None)
        if not effective_batch:
            prefix = ctx.prefix if ctx.prefix and not ctx.prefix.startswith("/") else "!"
            c_v2 = build_unregistered_component_v2(prefix)
            f_emb = build_unregistered_embed(prefix)
            await send_component_v2(ctx, c_v2, fallback_embed=f_emb, ephemeral=True)
            return

        embed = build_week_embed(
            self.read_all(batch=effective_batch), self._now(), query_batch=effective_batch, user_profile=profile
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

            existing = self.get_user_profile(user_id)
            actual_batch = self.resolve_batch_input(clean_batch, existing)
            resolved_code, m_deg, m_yr, _ = resolve_batch_from_inputs("", "", actual_batch)
            prof = self.set_user_profile(
                user_id=user_id,
                batch=actual_batch,
                degree=m_deg or "",
                year=m_yr or "",
                name=name or "",
                roll_no=roll_no or "",
            )
            embed = discord.Embed(
                title="Account Linked Successfully",
                description=(
                    f"Successfully linked {ctx.author.mention} to **Batch {prof.batch}**.\n\n"
                    "Now whenever you run `/timetable`, `/next`, or `/week`, "
                    f"the bot will automatically load the timetable for **Batch {prof.batch}**."
                ),
                color=COLOR_SUCCESS,
            )
            embed.set_author(name="Timetable Bot")
            embed.add_field(name="Discord User", value=ctx.author.mention, inline=True)
            embed.add_field(name="Linked Batch", value=f"**Batch {prof.batch}**", inline=True)
            if prof.degree:
                embed.add_field(name="Degree", value=f"**{prof.degree}**", inline=True)
            if prof.year:
                embed.add_field(name="Year", value=f"**{prof.year}**", inline=True)
            if prof.name:
                embed.add_field(name="Name", value=prof.name, inline=True)
            if prof.roll_no:
                embed.add_field(name="Roll / ID", value=f"`{prof.roll_no}`", inline=True)
            embed.add_field(
                name="Quick Tips",
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
        batch_list = ", ".join(f"**Batch {b}**" for b in batches[:15]) if batches else "None in schedule"

        embed = discord.Embed(
            title="Link Your Discord Account",
            description=(
                "Link your account to your batch so the bot serves your schedule automatically.\n\n"
                "### How to Link\n"
                "> `/link <batch>` — *e.g.* `/link AI 16`, `/link Core 35`, `/link 16`, or `/link 26CAIBTAIB16`\n"
                "> Or run **`/register`** for a 3-step setup wizard.\n\n"
                "### With Student Information (Optional)\n"
                '> `/link batch:AI 16 name:"Your Name" roll_no:"21CS042"`\n\n'
                f"**Sample Batches:** {batch_list}..."
            ),
            color=COLOR_PRIMARY,
        )
        embed.set_author(name="Timetable Bot")
        if existing:
            extra = f" ({existing.name})" if existing.name else ""
            embed.add_field(
                name="Current Link",
                value=f"You are currently linked to **Batch {existing.batch}**{extra}.",
                inline=False,
            )
        embed.set_footer(text="Run /link <batch> or /register to set up")

        view = None
        if _HAS_UI and batches and BatchSelectView is not None:
            view = BatchSelectView(self, ctx.author.id, batches)

        await _safe_send(ctx, embed=embed, view=view, ephemeral=True)

    @commands.hybrid_command(
        name="register",
        aliases=["setup", "onboard"],
        description="Register your department, year, and batch to unlock your timetable.",
    )
    @app_commands.describe(
        degree="Your department or degree (e.g. CSE, ECE, BTECH-CSE, BCA).",
        year="Your academic year (1, 2, 3, or 4).",
        batch="Your batch or section (e.g. 16, B16, or full code 26CAIBTAIB16).",
        name="Optional: your full real name.",
        roll_no="Optional: your university roll number.",
    )
    async def register(
        self,
        ctx: commands.Context,
        degree: Optional[str] = None,
        year: Optional[str] = None,
        batch: Optional[str] = None,
        name: Optional[str] = None,
        roll_no: Optional[str] = None,
    ) -> None:
        prefix = ctx.prefix if ctx.prefix and not ctx.prefix.startswith("/") else "!"

        # Direct one-line registration if all 3 (or batch alone) are given
        if (degree and year and batch) or (batch and len(batch) >= 10):
            resolved_batch, m_deg, m_yr, desc = resolve_batch_from_inputs(degree or "", year or "", batch or "")
            if resolved_batch:
                prof = self.set_user_profile(
                    user_id=ctx.author.id,
                    batch=resolved_batch,
                    degree=m_deg or (degree.upper() if degree else ""),
                    year=m_yr or (year if year else ""),
                    name=name or "",
                    roll_no=roll_no or "",
                )
                embed = discord.Embed(
                    title="Timetable Setup Complete",
                    description=(
                        f"Welcome {ctx.author.mention}. Your timetable has been registered successfully.\n\n"
                        f"Commands like **`{prefix}today`**, **`{prefix}now`**, and **`{prefix}week`** "
                        f"will immediately load your classes for **Batch {prof.batch}**."
                    ),
                    color=COLOR_SUCCESS,
                )
                embed.set_author(name="Timetable Setup Complete")
                embed.add_field(name="Degree", value=f"**{prof.degree or 'N/A'}**", inline=True)
                embed.add_field(name="Year", value=f"**{prof.year or 'N/A'}**", inline=True)
                embed.add_field(name="Batch", value=f"**`{prof.batch}`**", inline=True)
                if prof.name:
                    embed.add_field(name="Name", value=prof.name, inline=True)
                if prof.roll_no:
                    embed.add_field(name="Roll / ID", value=f"`{prof.roll_no}`", inline=True)
                embed.add_field(
                    name="Available Commands",
                    value=(
                        f"• `{prefix}today` — View your classes for today\n"
                        f"• `{prefix}next` — What class is on now / next\n"
                        f"• `{prefix}week` — Full weekly timetable\n"
                        f"• `{prefix}freerooms` — Find empty study rooms during breaks"
                    ),
                    inline=False,
                )
                embed.set_footer(text="Settings saved permanently • Type /profile to view your info")
                reg_c2 = build_registered_component_v2(prof, prefix=prefix, mention=ctx.author.mention)
                await send_component_v2(ctx, reg_c2, fallback_embed=embed)
                return
            else:
                await _safe_send(
                    ctx,
                    embed=discord.Embed(
                        title="Registration Issue",
                        description=f"> {desc}\n\nTip: You can search valid batches with `{prefix}findbatch {degree or ''}`.",
                        color=COLOR_ERROR,
                    ),
                    ephemeral=True,
                )
                return

        # Interactive setup wizard
        def check(m: discord.Message) -> bool:
            return m.author.id == ctx.author.id and m.channel.id == ctx.channel.id

        # Step 1: Degree
        embed1 = discord.Embed(
            title="Timetable Registration — Step 1 of 3",
            description=(
                "**Which department or degree are you in?**\n\n"
                "Examples:\n"
                "• `CSE` (or `BTECH-CSE`)\n"
                "• `ECE` (or `BTECH-ECE`)\n"
                "• `BCA` (or `BCA-CSAI`)\n"
                "• `BBA` (or `MBA`)\n"
                "• `ME` / `EEE` / `Civil`\n\n"
                "*Reply below with your department or degree (or type `cancel` to exit)*"
            ),
            color=COLOR_PRIMARY,
        )
        embed1.set_footer(text="Step 1/3 • Department / Degree")
        await ctx.send(embed=embed1)

        try:
            m1 = await self.bot.wait_for("message", check=check, timeout=60.0)
        except asyncio.TimeoutError:
            await ctx.send("Registration timed out. Type `!register` whenever you want to set up.")
            return

        deg_ans = m1.content.strip()
        if deg_ans.lower() in ("cancel", "exit", "quit"):
            await ctx.send("Registration cancelled.")
            return

        # Step 2: Year
        embed2 = discord.Embed(
            title="Timetable Registration — Step 2 of 3",
            description=(
                f"Selected Degree: **{deg_ans.upper()}**\n\n"
                "**Which academic year are you in?**\n"
                "• `1` (First Year)\n"
                "• `2` (Second Year)\n"
                "• `3` (Third Year)\n"
                "• `4` (Fourth Year)\n\n"
                "*Reply with `1`, `2`, `3`, or `4`*"
            ),
            color=COLOR_PRIMARY,
        )
        embed2.set_footer(text="Step 2/3 • Academic Year")
        await ctx.send(embed=embed2)

        try:
            m2 = await self.bot.wait_for("message", check=check, timeout=60.0)
        except asyncio.TimeoutError:
            await ctx.send("Registration timed out. Type `!register` to try again.")
            return

        yr_ans = m2.content.strip()
        if yr_ans.lower() in ("cancel", "exit", "quit"):
            await ctx.send("Registration cancelled.")
            return

        # Step 3: Batch
        deg_upper = deg_ans.upper()
        if any(tag in deg_upper for tag in ("CSE", "CS", "COMPUTER")):
            specialization_guide = (
                "**Specialization Formats:**\n"
                "• AI / ML: `AI <number>` (e.g. `AI 16`)\n"
                "• Core CSE: `Core <number>` (e.g. `Core 35`)\n"
                "• Cyber Security: `Cyber <number>` (e.g. `Cyber 59`)\n"
                "• Data Science: `DS <number>` (e.g. `DS 57`)\n"
                "• Or section number: `16`\n"
                "• Or full batch code: `26CAIBTAIB16`\n\n"
            )
        else:
            specialization_guide = (
                "**Batch Input Formats:**\n"
                f"• Type your section number (e.g. `01`, `05`, `16`)\n"
                f"• Or branch + number (e.g. `{deg_ans.split('-')[-1]} 01`)\n"
                "• Or full batch code: `26CAIBTAIB16`\n\n"
            )

        embed3 = discord.Embed(
            title="Timetable Registration — Step 3 of 3",
            description=(
                f"Degree: **{deg_ans.upper()}** • Year: **{yr_ans}**\n\n"
                f"{specialization_guide}"
                "*Reply with your section or batch code below.*"
            ),
            color=COLOR_PRIMARY,
        )
        embed3.set_footer(text="Step 3/3 • Section / Batch")
        await ctx.send(embed=embed3)

        try:
            m3 = await self.bot.wait_for("message", check=check, timeout=60.0)
        except asyncio.TimeoutError:
            await ctx.send("Registration timed out. Type `!register` to try again.")
            return

        batch_ans = m3.content.strip()
        if batch_ans.lower() in ("cancel", "exit", "quit"):
            await ctx.send("Registration cancelled.")
            return

        resolved_batch, m_deg, m_yr, desc = resolve_batch_from_inputs(deg_ans, yr_ans, batch_ans)
        if not resolved_batch:
            await ctx.send(
                embed=discord.Embed(
                    title="Could Not Match Batch",
                    description=(
                        f"Sorry, we could not match your inputs:\n> {desc}\n\n"
                        f"Tip: Use `{prefix}findbatch {deg_ans}` to see your department's exact batch codes, "
                        f"then run `{prefix}register` again."
                    ),
                    color=COLOR_ERROR,
                )
            )
            return

        prof = self.set_user_profile(
            user_id=ctx.author.id,
            batch=resolved_batch,
            degree=m_deg or deg_ans.upper(),
            year=m_yr or yr_ans,
            name=name or "",
            roll_no=roll_no or "",
        )

        embed_done = discord.Embed(
            title="Timetable Setup Complete",
            description=(
                f"Welcome {ctx.author.mention}. Your timetable has been registered successfully.\n\n"
                f"Commands like **`{prefix}today`**, **`{prefix}now`**, and **`{prefix}week`** "
                f"will immediately load your classes for **Batch {prof.batch}**."
            ),
            color=COLOR_SUCCESS,
        )
        embed_done.set_author(name="Timetable Setup Complete")
        embed_done.add_field(name="Degree", value=f"**{prof.degree or 'N/A'}**", inline=True)
        embed_done.add_field(name="Year", value=f"**{prof.year or 'N/A'}**", inline=True)
        embed_done.add_field(name="Batch", value=f"**`{prof.batch}`**", inline=True)
        embed_done.add_field(
            name="Available Commands",
            value=(
                f"• `{prefix}today` — View today's schedule\n"
                f"• `{prefix}next` — What class is on now / next\n"
                f"• `{prefix}week` — Full weekly timetable\n"
                f"• `{prefix}freerooms` — Find empty study rooms during breaks"
            ),
            inline=False,
        )
        embed_done.set_footer(text="Saved permanently • Type /profile to view your info")
        reg_c2 = build_registered_component_v2(prof, prefix=prefix, mention=ctx.author.mention)
        await send_component_v2(ctx, reg_c2, fallback_embed=embed_done)

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
                title="Account Unlinked",
                description=(
                    f"Your account ({ctx.author.mention}) has been unlinked.\n"
                    "The bot will now display timetables for all batches by default.\n\n"
                    "-# *You can link again anytime using `/link <batch>`.*"
                ),
                color=COLOR_WARN,
            )
        else:
            embed = discord.Embed(
                title="No Linked Account Found",
                description=(
                    f"Your account ({ctx.author.mention}) wasn't linked to any batch.\n"
                    "Use `/link <batch>` (e.g. `/link AI 16`) to link your batch."
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
                title=f"Student Profile — {target.display_name}",
                description=f"Discord Account: {target.mention}",
                color=COLOR_PRIMARY,
                timestamp=now,
            )
            embed.set_author(name="Timetable Bot")
            if hasattr(target, "display_avatar") and target.display_avatar:
                embed.set_thumbnail(url=target.display_avatar.url)
            embed.add_field(name="Linked Batch", value=f"**`{prof.batch}`**", inline=True)
            if prof.degree:
                embed.add_field(name="Degree", value=f"**{prof.degree}**", inline=True)
            if prof.year:
                embed.add_field(name="Year", value=f"**{prof.year}**", inline=True)
            if prof.name:
                embed.add_field(name="Name", value=prof.name, inline=True)
            if prof.roll_no:
                embed.add_field(name="Roll / ID", value=f"`{prof.roll_no}`", inline=True)

            day_key = DAY_KEYS[now.weekday()]
            slots = [
                s
                for s in self.read_all(batch=prof.batch).get(day_key, [])
                if not s.is_free and slot_matches_batch(s, prof.batch)
            ]
            noun = "class" if len(slots) == 1 else "classes"
            embed.add_field(
                name=f"Today's Schedule ({DAY_NAMES[day_key]})",
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
                "You have not linked your account to a batch yet.\n\n"
                "Use `/link <batch>` (e.g. `/link AI 16` or `/register`) so `/timetable` will automatically "
                "show your batch's classes without needing any filters."
                if is_self
                else f"{target.mention} has not linked their account to a batch yet."
            )
            embed = discord.Embed(
                title="Profile Not Linked",
                description=desc,
                color=COLOR_MUTED,
            )
            embed.set_author(name="Timetable Bot")
            if is_self:
                embed.set_footer(text="Tip: Link in seconds with /link AI 16 or /register")
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

    @commands.hybrid_command(
        name="freerooms",
        aliases=["emptyrooms", "freeroomsnow"],
        description="Find available empty classrooms by day, time, or block.",
    )
    @app_commands.describe(
        day="Day of week (e.g. today, Friday, Monday). Defaults to today.",
        time="Time slot (e.g. 09:30, 11:30, 2:30 PM). Defaults to current time.",
        block="Filter by block (e.g. 7, 8, 10, 11, BL8).",
    )
    async def freerooms(
        self,
        ctx: commands.Context,
        day: Optional[str] = None,
        time: Optional[str] = None,
        block: Optional[str] = None,
    ) -> None:
        if not ROOMS_FILE.is_file():
            await _safe_send(
                ctx,
                embed=discord.Embed(
                    title="Room Data Not Available",
                    description="The room availability database is not loaded yet.",
                    color=COLOR_ERROR,
                ),
                ephemeral=True,
            )
            return

        try:
            rooms_data = json.loads(ROOMS_FILE.read_text(encoding="utf-8"))
        except Exception as exc:
            await _safe_send(
                ctx,
                embed=discord.Embed(
                    title="Error Loading Room Data",
                    description=str(exc),
                    color=COLOR_ERROR,
                ),
                ephemeral=True,
            )
            return

        now = self._now()
        if day:
            day_key = resolve_day(day, now=now)
            if not day_key or day_key not in DAY_NAMES:
                await _safe_send(
                    ctx,
                    embed=discord.Embed(
                        title="Invalid Day",
                        description=f"Could not understand day `{day}`. Try `mon`, `fri`, `today`.",
                        color=COLOR_ERROR,
                    ),
                    ephemeral=True,
                )
                return
            target_day_name = DAY_NAMES[day_key]
        else:
            day_key = DAY_KEYS[now.weekday()]
            target_day_name = DAY_NAMES.get(day_key, "Monday")

        if time:
            clean_t = time.strip().upper()
            m = re.match(r"^(\d{1,2})(?::(\d{2}))?\s*(AM|PM)?$", clean_t)
            if m:
                h = int(m.group(1))
                minute = int(m.group(2) or 0)
                mer = m.group(3)
                if mer == "PM" and h != 12:
                    h += 12
                elif mer == "AM" and h == 12:
                    h = 0
                target_slot = find_best_room_slot(datetime.min.time().replace(hour=h, minute=minute))
            else:
                target_slot = find_best_room_slot(now.time())
        else:
            target_slot = find_best_room_slot(now.time())

        day_rooms = rooms_data.get(target_day_name, {})
        available_rooms = day_rooms.get(target_slot, [])

        if not available_rooms:
            embed = discord.Embed(
                title=f"Free Rooms — {target_day_name} @ {target_slot}",
                description="No classrooms reported free for this time slot (university may be closed or weekend).",
                color=COLOR_WARN,
            )
            await _safe_send(ctx, embed=embed)
            return

        block_filter = block.strip().upper() if block else None
        if block_filter:
            b_match = re.search(r"(\d+)", block_filter)
            b_num = b_match.group(1) if b_match else block_filter
            filtered_rooms = [
                r for r in available_rooms
                if b_num in r.get("room", "").upper() or (f"BL{b_num}" in r.get("room", "").upper())
            ]
        else:
            filtered_rooms = available_rooms

        total_count = len(filtered_rooms)
        b_info = f" in Block {block.strip()}" if block else ""
        embed = discord.Embed(
            title=f"Free Rooms • {target_day_name} @ {target_slot}",
            description=f"> Found **{total_count} empty room{'s' if total_count != 1 else ''}** available{b_info}.",
            color=COLOR_PRIMARY,
        )
        embed.set_author(name="SRU Room Availability")

        grouped_by_block: dict[str, list[dict[str, Any]]] = {}
        for r in filtered_rooms:
            b_name = get_room_block(r.get("room", ""))
            grouped_by_block.setdefault(b_name, []).append(r)

        for b_name in sorted(grouped_by_block.keys()):
            r_list = grouped_by_block[b_name]
            room_strs = [f"`{r['room']}`" for r in r_list]
            content = ", ".join(room_strs)
            if len(content) > 1024:
                content = content[:1015] + "… and more"
            embed.add_field(
                name=f"{b_name} ({len(r_list)})",
                value=content,
                inline=False,
            )

        end_h = int(target_slot[:2]) + 1
        embed.set_footer(text=f"Slot: {target_slot} - {end_h:02d}:30 • Updated from SRUX")
        await _safe_send(ctx, embed=embed)

    @commands.hybrid_command(
        name="findbatch",
        aliases=["searchbatch", "batches"],
        description="Search university batches by department, degree, or number.",
    )
    @app_commands.describe(query="Search keyword, e.g. CSE, AI, 16, BTech, ECE")
    async def findbatch(self, ctx: commands.Context, query: str) -> None:
        if not INDEX_FILE.is_file():
            await _safe_send(
                ctx,
                embed=discord.Embed(
                    title="Batches Index Missing",
                    description="Run `python scripts/sync_all_timetables.py` to index batches.",
                    color=COLOR_ERROR,
                ),
                ephemeral=True,
            )
            return

        try:
            idx = json.loads(INDEX_FILE.read_text(encoding="utf-8"))
        except Exception as exc:
            await _safe_send(ctx, embed=discord.Embed(title="Error Loading Index", description=str(exc), color=COLOR_ERROR), ephemeral=True)
            return

        q = query.strip().lower()
        matches = []
        for b_code, info in idx.get("batches", {}).items():
            if (
                q in b_code.lower()
                or any(q in d.lower() for d in info.get("degrees", []))
                or any(q in y.lower() for y in info.get("years", []))
                or any(q in s.lower() for s in info.get("subjects", []))
            ):
                matches.append(info)

        if not matches:
            embed = discord.Embed(
                title="Batch Search",
                description=f"No batches found matching `{query}`.\n\nTry searching by department like `CSE`, `ECE`, `BTech`, `MBA`, or a section number like `16`.",
                color=COLOR_MUTED,
            )
            await _safe_send(ctx, embed=embed)
            return

        embed = discord.Embed(
            title=f"Batch Search Results for `{query}`",
            description=f"> Found **{len(matches)} matching batch{'es' if len(matches) != 1 else ''}**.\nLink your account with `/link batch:<CODE>` to save your timetable.",
            color=COLOR_PRIMARY,
        )
        embed.set_author(name="University Batch Directory")

        display_matches = matches[:15]
        lines = []
        for m in display_matches:
            degs = "/".join(m.get("degrees", []))
            yrs = "/".join(m.get("years", []))
            cls_count = m.get("total_classes", 0)
            lines.append(f"• **`{m['batch']}`** — {degs} ({yrs}) • {cls_count} classes/week")

        embed.add_field(
            name=f"Top Results (Showing {len(display_matches)} of {len(matches)})",
            value="\n".join(lines),
            inline=False,
        )
        if len(matches) > 15:
            embed.set_footer(text=f"And {len(matches) - 15} more. Refine your query for more specific results.")
        else:
            embed.set_footer(text="Tip: Use /batchinfo <batch> to view full subjects & professors!")
        await _safe_send(ctx, embed=embed)

    @commands.hybrid_command(
        name="batchinfo",
        description="View subjects, faculties, and weekly class count for any batch.",
    )
    @app_commands.describe(batch="Batch code, e.g. 26CAIBTAIB16")
    async def batchinfo(self, ctx: commands.Context, batch: str) -> None:
        b_code = self.resolve_batch_input(batch)
        if not INDEX_FILE.is_file():
            await _safe_send(ctx, embed=discord.Embed(title="Index Missing", description="Index file not found.", color=COLOR_ERROR), ephemeral=True)
            return

        idx = json.loads(INDEX_FILE.read_text(encoding="utf-8"))
        info = idx.get("batches", {}).get(b_code)
        if not info:
            await _safe_send(
                ctx,
                embed=discord.Embed(
                    title="Batch Not Found",
                    description=f"Batch `{b_code}` was not found in the directory.\nUse `/findbatch` to search for valid batch codes.",
                    color=COLOR_ERROR,
                ),
                ephemeral=True,
            )
            return

        degs = ", ".join(info.get("degrees", [])) or "N/A"
        yrs = ", ".join(info.get("years", [])) or "N/A"
        total_cls = info.get("total_classes", 0)
        subjects = info.get("subjects", [])
        faculties = info.get("faculties", [])

        embed = discord.Embed(
            title=f"Batch Information: {b_code}",
            color=COLOR_PRIMARY,
        )
        embed.set_author(name="University Batch Details")
        embed.add_field(name="Degree", value=f"**{degs}**", inline=True)
        embed.add_field(name="Year", value=f"**{yrs}**", inline=True)
        embed.add_field(name="Classes", value=f"**{total_cls}** / week", inline=True)

        if subjects:
            subj_lines = [f"• {s}" for s in subjects]
            val = "\n".join(subj_lines)
            if len(val) > 1024:
                val = val[:1015] + "…"
            embed.add_field(name=f"Subjects ({len(subjects)})", value=val, inline=False)

        if faculties:
            fac_lines = [f"• {f}" for f in faculties]
            val = "\n".join(fac_lines)
            if len(val) > 1024:
                val = val[:1015] + "…"
            embed.add_field(name=f"Faculties ({len(faculties)})", value=val, inline=False)

        embed.set_footer(text=f"Link this batch to your profile with: /link batch:{b_code}")
        await _safe_send(ctx, embed=embed)

    @commands.hybrid_command(
        name="faculty",
        aliases=["lecturer", "findfaculty", "prof"],
        description="Search faculty to see what subjects they teach and their batches.",
    )
    @app_commands.describe(name="Faculty name or keyword, e.g. Ashim, Dasari")
    async def faculty(self, ctx: commands.Context, name: str) -> None:
        if not INDEX_FILE.is_file():
            await _safe_send(ctx, embed=discord.Embed(title="Index Missing", description="Index file not found.", color=COLOR_ERROR), ephemeral=True)
            return

        idx = json.loads(INDEX_FILE.read_text(encoding="utf-8"))
        q = name.strip().lower()

        fac_matches: dict[str, dict[str, Any]] = {}
        for b_code, info in idx.get("batches", {}).items():
            for f in info.get("faculties", []):
                if q in f.lower():
                    if f not in fac_matches:
                        fac_matches[f] = {"batches": [], "subjects": set()}
                    fac_matches[f]["batches"].append(b_code)
                    for s in info.get("subjects", []):
                        fac_matches[f]["subjects"].add(s)

        if not fac_matches:
            embed = discord.Embed(
                title="Faculty Search",
                description=f"No faculty found matching `{name}`.",
                color=COLOR_MUTED,
            )
            await _safe_send(ctx, embed=embed)
            return

        embed = discord.Embed(
            title=f"Faculty Search Results for `{name}`",
            color=COLOR_PRIMARY,
        )
        embed.set_author(name="University Faculty Directory")

        for f_name, data in list(fac_matches.items())[:10]:
            b_list = sorted(set(data["batches"]))
            b_preview = ", ".join(f"`{b}`" for b in b_list[:6])
            if len(b_list) > 6:
                b_preview += f" ... +{len(b_list) - 6} more"

            s_list = sorted(data["subjects"])
            s_preview = ", ".join(s_list[:3])
            if len(s_list) > 3:
                s_preview += f" ... +{len(s_list) - 3} more"

            embed.add_field(
                name=f"{f_name}",
                value=f"**Batches:** {b_preview}\n**Subjects:** {s_preview}",
                inline=False,
            )

        if len(fac_matches) > 10:
            embed.set_footer(text=f"Showing 10 of {len(fac_matches)} faculty matches. Refine your query.")
        await _safe_send(ctx, embed=embed)

    @commands.hybrid_command(
        name="degrees",
        description="List all 30 university degrees and programs available in the bot.",
    )
    async def degrees(self, ctx: commands.Context) -> None:
        if not OPTIONS_FILE.is_file():
            await _safe_send(ctx, embed=discord.Embed(title="Options Missing", description="Directory not found.", color=COLOR_ERROR), ephemeral=True)
            return

        options = json.loads(OPTIONS_FILE.read_text(encoding="utf-8"))
        embed = discord.Embed(
            title="Available University Degrees & Programs",
            description=f"> The bot supports **{len(options)} degrees** across all academic years and 428 batches.",
            color=COLOR_PRIMARY,
        )
        embed.set_author(name="University Program Directory")

        deg_names = sorted(options.keys())
        lines = []
        for d in deg_names:
            years = ", ".join(sorted(options[d].keys()))
            lines.append(f"• **{d}** ({years})")

        half = (len(lines) + 1) // 2
        embed.add_field(name="Undergraduate & Professional (A - M)", value="\n".join(lines[:half]), inline=True)
        embed.add_field(name="Engineering, Postgrad & Others (M - Z)", value="\n".join(lines[half:]), inline=True)
        embed.set_footer(text="Use /findbatch <degree> to see specific batch sections!")
        await _safe_send(ctx, embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(TimetableCog(bot))
