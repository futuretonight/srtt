"""Attendance cog: lecturer access control, class-timeframe locking,
interactive roll-number multi-select UI, student shortage/safe-bunk calculators,
and CSV attendance exports.
"""
from __future__ import annotations

import asyncio
import contextlib
import csv
import io
import json
import logging
import math
import os
import re
import tempfile
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone, tzinfo
from pathlib import Path
from typing import Any, Optional

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

from cogs.timetable import DAY_KEYS, DAY_NAMES, slot_matches_batch, natural_sort_key

_HAS_UI = False
try:
    if hasattr(discord, "ui") and hasattr(discord.ui, "View") and hasattr(discord.ui, "Select") and hasattr(discord.ui, "Button"):
        _HAS_UI = True
except Exception:
    _HAS_UI = False

log = logging.getLogger("timetable_bot.attendance")

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
LECTURERS_FILE = DATA_DIR / "lecturers.json"
ATTENDANCE_FILE = DATA_DIR / "attendance.json"
CONFIG_FILE = DATA_DIR / "config.json"

COLOR_PRIMARY = 0x5865F2
COLOR_SUCCESS = 0x57F287
COLOR_WARN = 0xFEE75C
COLOR_ERROR = 0xED4245
COLOR_MUTED = 0x4F545C

DEFAULT_THRESHOLD = 75.0


async def _safe_send(ctx: commands.Context, **kwargs) -> Any:
    """Send a message, safely dropping unsupported kwargs if rejected by backend."""
    try:
        return await ctx.send(**kwargs)
    except TypeError:
        kwargs.pop("ephemeral", None)
        try:
            return await ctx.send(**kwargs)
        except TypeError:
            kwargs.pop("view", None)
            return await ctx.send(**kwargs)


def _fmt_pct_bar(pct: float, length: int = 10) -> str:
    """Generate a clean visual progress bar: e.g. ▰▰▰▰▱▱▱▱▱▱ 40%"""
    pct = max(0.0, min(1.0, pct))
    filled = int(round(pct * length))
    bar = "▰" * filled + "▱" * (length - filled)
    return f"`{bar}` **{pct * 100:.1f}%**"


# --------------------------------------------------------------------------- #
# Data Models
# --------------------------------------------------------------------------- #
@dataclass
class Lecturer:
    user_id: str
    name: str
    subjects: list[str]
    batches: list[str] = field(default_factory=lambda: ["ALL"])
    added_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "subjects": self.subjects,
            "batches": self.batches,
            "added_at": self.added_at,
        }

    @classmethod
    def from_dict(cls, user_id: str, data: dict[str, Any]) -> "Lecturer":
        raw_subs = data.get("subjects") or []
        if isinstance(raw_subs, str):
            raw_subs = [s.strip() for s in raw_subs.split(",") if s.strip()]
        raw_batches = data.get("batches") or ["ALL"]
        if isinstance(raw_batches, str):
            raw_batches = [b.strip().upper() for b in raw_batches.split(",") if b.strip()]
        return cls(
            user_id=str(user_id),
            name=str(data.get("name", "")).strip(),
            subjects=[str(s).strip() for s in raw_subs if str(s).strip()],
            batches=[str(b).strip().upper() for b in raw_batches if str(b).strip()] or ["ALL"],
            added_at=str(data.get("added_at", "")).strip(),
        )


@dataclass
class AttendanceRecord:
    user_id: str
    roll_no: str
    name: str
    status: str  # "present" | "absent"

    def to_dict(self) -> dict[str, Any]:
        return {
            "roll_no": self.roll_no,
            "name": self.name,
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, user_id: str, data: dict[str, Any]) -> "AttendanceRecord":
        return cls(
            user_id=str(user_id),
            roll_no=str(data.get("roll_no", "")).strip(),
            name=str(data.get("name", "")).strip(),
            status=str(data.get("status", "absent")).strip().lower(),
        )


@dataclass
class AttendanceSession:
    session_id: str
    date: str  # YYYY-MM-DD
    slot_start: str  # HH:MM
    slot_end: str  # HH:MM
    subject: str
    batch: str
    lecturer_id: str
    marked_at: str
    approval_override: bool
    records: dict[str, AttendanceRecord]

    @property
    def total_students(self) -> int:
        return len(self.records)

    @property
    def present_count(self) -> int:
        return sum(1 for r in self.records.values() if r.status == "present")

    @property
    def absent_count(self) -> int:
        return sum(1 for r in self.records.values() if r.status == "absent")

    @property
    def percentage(self) -> float:
        if not self.records:
            return 0.0
        return (self.present_count / self.total_students) * 100.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "date": self.date,
            "slot_start": self.slot_start,
            "slot_end": self.slot_end,
            "subject": self.subject,
            "batch": self.batch,
            "lecturer_id": self.lecturer_id,
            "marked_at": self.marked_at,
            "approval_override": self.approval_override,
            "records": {uid: rec.to_dict() for uid, rec in self.records.items()},
        }

    @classmethod
    def from_dict(cls, sid: str, data: dict[str, Any]) -> "AttendanceSession":
        raw_recs = data.get("records") or {}
        records: dict[str, AttendanceRecord] = {}
        if isinstance(raw_recs, dict):
            for uid, rdata in raw_recs.items():
                if isinstance(rdata, dict):
                    records[str(uid)] = AttendanceRecord.from_dict(uid, rdata)

        return cls(
            session_id=str(sid),
            date=str(data.get("date", "")).strip(),
            slot_start=str(data.get("slot_start", "")).strip(),
            slot_end=str(data.get("slot_end", "")).strip(),
            subject=str(data.get("subject", "")).strip(),
            batch=str(data.get("batch", "ALL")).strip().upper(),
            lecturer_id=str(data.get("lecturer_id", "")).strip(),
            marked_at=str(data.get("marked_at", "")).strip(),
            approval_override=bool(data.get("approval_override", False)),
            records=records,
        )


# --------------------------------------------------------------------------- #
# Interactive UI Components (when _HAS_UI is True)
# --------------------------------------------------------------------------- #
if _HAS_UI:
    class StudentSelect(discord.ui.Select):
        def __init__(
            self,
            view: "AttendanceWorkflowView",
            options: list[discord.SelectOption],
            placeholder: str = "Select students...",
            custom_id: str = "att_student_select",
        ):
            self.att_view = view
            super().__init__(
                placeholder=placeholder,
                min_values=0,
                max_values=len(options),
                options=options,
                custom_id=custom_id,
            )

        async def callback(self, interaction: discord.Interaction):
            if interaction.user.id != self.att_view.lecturer_id:
                if hasattr(interaction, "response"):
                    await interaction.response.send_message(
                        "This attendance session is restricted to the authorized lecturer.",
                        ephemeral=True,
                    )
                return

            chosen_uids = set(self.values)
            # Apply changes based on current selection mode
            if self.att_view.selection_mode == "absentees":
                # Selected students are marked ABSENT, all others in this chunk marked PRESENT
                for opt in self.options:
                    uid = opt.value
                    self.att_view.status_map[uid] = "absent" if uid in chosen_uids else "present"
            else:
                # Selected students are marked PRESENT, all others in this chunk marked ABSENT
                for opt in self.options:
                    uid = opt.value
                    self.att_view.status_map[uid] = "present" if uid in chosen_uids else "absent"

            embed = self.att_view.build_preview_embed()
            await interaction.response.edit_message(embed=embed, view=self.att_view)

    class AttendanceWorkflowView(discord.ui.View):
        def __init__(
            self,
            cog: "AttendanceCog",
            lecturer_id: int,
            subject: str,
            batch: str,
            slot_span: str,
            students: list[dict[str, str]],
            approval_override: bool = False,
            timeout: float = 300.0,
        ):
            super().__init__(timeout=timeout)
            self.cog = cog
            self.lecturer_id = lecturer_id
            self.subject = subject
            self.batch = batch
            self.slot_span = slot_span
            self.students = students  # list of {user_id, roll_no, name}
            self.approval_override = approval_override
            self.selection_mode = "absentees"  # "absentees" or "present"

            # Initial state: default all students to PRESENT
            self.status_map: dict[str, str] = {s["user_id"]: "present" for s in students}
            self._chunk_index = 0
            self._setup_components()

        def _setup_components(self) -> None:
            self.clear_items()

            # Row 0: Action buttons
            btn_all_present = discord.ui.Button(
                label="Mark All Present",
                style=discord.ButtonStyle.success,
                emoji="",
                custom_id="att_btn_all_present",
                row=0,
            )
            btn_all_present.callback = self._on_mark_all_present
            self.add_item(btn_all_present)

            btn_all_absent = discord.ui.Button(
                label="Mark All Absent",
                style=discord.ButtonStyle.danger,
                emoji="",
                custom_id="att_btn_all_absent",
                row=0,
            )
            btn_all_absent.callback = self._on_mark_all_absent
            self.add_item(btn_all_absent)

            mode_label = "Switch: Pick Present" if self.selection_mode == "absentees" else "Switch: Pick Absent"
            btn_toggle_mode = discord.ui.Button(
                label=mode_label,
                style=discord.ButtonStyle.secondary,
                emoji="",
                custom_id="att_btn_toggle_mode",
                row=0,
            )
            btn_toggle_mode.callback = self._on_toggle_mode
            self.add_item(btn_toggle_mode)

            # Row 1/2: Student multi-select (Discord allows max 25 options per select)
            chunk_size = 25
            total_students = len(self.students)
            if total_students > 0:
                start = self._chunk_index * chunk_size
                end = min(start + chunk_size, total_students)
                chunk = self.students[start:end]

                options: list[discord.SelectOption] = []
                for s in chunk:
                    uid = s["user_id"]
                    curr_status = self.status_map.get(uid, "present")
                    roll = s.get("roll_no") or "?"
                    name = s.get("name") or f"Student {uid[:5]}"
                    is_linked = s.get("is_linked", False)
                    # Default selection state depending on mode
                    is_selected = (curr_status == "absent") if self.selection_mode == "absentees" else (curr_status == "present")
                    icon = "" if curr_status == "absent" else ""
                    link_desc = "Linked" if is_linked else "Unlinked"
                    options.append(
                        discord.SelectOption(
                            label=f"Roll {roll}: {name}"[:100],
                            value=uid,
                            description=f"Status: {curr_status.upper()} • {link_desc}",
                            emoji=icon,
                            default=is_selected,
                        )
                    )

                placeholder = f"Select {self.selection_mode.title()} (Students {start + 1}–{end} of {total_students})"
                select = StudentSelect(self, options, placeholder=placeholder, custom_id=f"att_select_{self._chunk_index}")
                self.add_item(select)

            # Row 3: Pagination if more than 25 students
            if total_students > chunk_size:
                max_chunks = math.ceil(total_students / chunk_size)
                btn_prev = discord.ui.Button(
                    label="Previous Page",
                    style=discord.ButtonStyle.secondary,
                    emoji="◀️",
                    disabled=(self._chunk_index == 0),
                    row=2,
                )
                btn_prev.callback = self._on_prev_page
                self.add_item(btn_prev)

                btn_next = discord.ui.Button(
                    label="Next Page",
                    style=discord.ButtonStyle.secondary,
                    emoji="▶️",
                    disabled=(self._chunk_index >= max_chunks - 1),
                    row=2,
                )
                btn_next.callback = self._on_next_page
                self.add_item(btn_next)

            # Row 4: Submit button
            btn_submit = discord.ui.Button(
                label="Submit Attendance Record",
                style=discord.ButtonStyle.primary,
                emoji="",
                custom_id="att_btn_submit",
                row=3,
            )
            btn_submit.callback = self._on_submit
            self.add_item(btn_submit)

        def build_preview_embed(self) -> discord.Embed:
            total = len(self.students)
            present_count = sum(1 for s in self.status_map.values() if s == "present")
            absent_count = total - present_count
            pct = (present_count / total * 100.0) if total else 0.0

            absent_rolls = []
            for s in self.students:
                if self.status_map.get(s["user_id"]) == "absent":
                    r = s.get("roll_no") or "?"
                    n = s.get("name") or s["user_id"][:6]
                    absent_rolls.append(f"`{r}` ({n})")

            embed = discord.Embed(
                title=f"Mark Attendance — {self.subject}",
                description=(
                    f"> **Batch {self.batch.upper()}**   •   **`{self.slot_span}`**\n"
                    f"> **Summary:** `{present_count}/{total}` Present (**{pct:.1f}%**)   •   "
                    f"`{absent_count}` Absent\n\n"
                    f"**Current Mode:** Marking *{self.selection_mode.upper()}*.\n"
                    "Use the buttons or dropdown below, then click **Submit Attendance Record**."
                ),
                color=COLOR_PRIMARY,
            )
            embed.set_author(name="Timetable Attendance System")

            if absent_rolls:
                absent_str = ", ".join(absent_rolls[:15])
                if len(absent_rolls) > 15:
                    absent_str += f" … (+{len(absent_rolls) - 15} more)"
                embed.add_field(
                    name=f"Absentees ({absent_count})",
                    value=absent_str,
                    inline=False,
                )
            else:
                embed.add_field(
                    name="Absentees (0)",
                    value="*All students are currently marked as Present!*",
                    inline=False,
                )

            if self.approval_override:
                embed.set_footer(text="Marked with Admin Approval Override • Saved in data/attendance.json")
            else:
                embed.set_footer(text="Scheduled Class Window Active • Saved in data/attendance.json")
            return embed

        async def _on_mark_all_present(self, interaction: discord.Interaction):
            if interaction.user.id != self.lecturer_id:
                return
            for uid in self.status_map:
                self.status_map[uid] = "present"
            self._setup_components()
            await interaction.response.edit_message(embed=self.build_preview_embed(), view=self)

        async def _on_mark_all_absent(self, interaction: discord.Interaction):
            if interaction.user.id != self.lecturer_id:
                return
            for uid in self.status_map:
                self.status_map[uid] = "absent"
            self._setup_components()
            await interaction.response.edit_message(embed=self.build_preview_embed(), view=self)

        async def _on_toggle_mode(self, interaction: discord.Interaction):
            if interaction.user.id != self.lecturer_id:
                return
            self.selection_mode = "present" if self.selection_mode == "absentees" else "absentees"
            self._setup_components()
            await interaction.response.edit_message(embed=self.build_preview_embed(), view=self)

        async def _on_prev_page(self, interaction: discord.Interaction):
            if interaction.user.id != self.lecturer_id:
                return
            if self._chunk_index > 0:
                self._chunk_index -= 1
            self._setup_components()
            await interaction.response.edit_message(embed=self.build_preview_embed(), view=self)

        async def _on_next_page(self, interaction: discord.Interaction):
            if interaction.user.id != self.lecturer_id:
                return
            self._chunk_index += 1
            self._setup_components()
            await interaction.response.edit_message(embed=self.build_preview_embed(), view=self)

        async def _on_submit(self, interaction: discord.Interaction):
            if interaction.user.id != self.lecturer_id:
                return

            now = self.cog._now()
            date_str = now.strftime("%Y-%m-%d")
            # Parse slot start/end
            parts = self.slot_span.split("–")
            start_hhmm = parts[0].strip() if len(parts) > 0 else "00:00"
            end_hhmm = parts[1].strip() if len(parts) > 1 else "00:00"

            clean_sub = re.sub(r"[^\w]+", "_", self.subject).strip("_")
            session_id = f"{date_str}_{start_hhmm.replace(':', '')}_{clean_sub}_B{self.batch}"

            # Build record objects
            records: dict[str, AttendanceRecord] = {}
            for s in self.students:
                uid = s["user_id"]
                records[uid] = AttendanceRecord(
                    user_id=uid,
                    roll_no=s.get("roll_no", ""),
                    name=s.get("name", ""),
                    status=self.status_map.get(uid, "present"),
                )

            session = AttendanceSession(
                session_id=session_id,
                date=date_str,
                slot_start=start_hhmm,
                slot_end=end_hhmm,
                subject=self.subject,
                batch=self.batch,
                lecturer_id=str(self.lecturer_id),
                marked_at=now.isoformat(),
                approval_override=self.approval_override,
                records=records,
            )

            # Persist to disk
            sessions = self.cog.read_attendance()
            sessions[session_id] = session
            self.cog.write_attendance(sessions)

            # Construct finalized celebration embed
            total = session.total_students
            present = session.present_count
            absent = session.absent_count
            pct = session.percentage

            embed = discord.Embed(
                title="Attendance Submitted Successfully!",
                description=(
                    f"Attendance for **{self.subject}** (Batch {self.batch.upper()}) has been permanently saved.\n\n"
                    f"> Date: **{session.date}**   •   Time: **`{session.slot_start} – {session.slot_end}`**\n"
                    f"> Present: **{present}/{total}** (`{pct:.1f}%`)\n"
                    f"> Absent: **{absent}** student{'s' if absent != 1 else ''}"
                ),
                color=COLOR_SUCCESS,
                timestamp=now,
            )
            embed.set_author(name="Timetable Attendance System")

            if absent > 0:
                absent_names = [f"Roll {r.roll_no} ({r.name})" for r in session.records.values() if r.status == "absent"]
                embed.add_field(
                    name=f"Absentees List ({absent})",
                    value=", ".join(absent_names[:20]) + (" …" if len(absent_names) > 20 else ""),
                    inline=False,
                )

            embed.set_footer(text=f"Session ID: {session_id} • Students can run /myattendance to view")

            # Disable all controls
            for child in self.children:
                setattr(child, "disabled", True)

            await interaction.response.edit_message(embed=embed, view=self)

    class ApprovalRequestView(discord.ui.View):
        def __init__(self, cog: "AttendanceCog", lecturer: discord.Member, subject: str, batch: str, slot_span: str):
            super().__init__(timeout=180.0)
            self.cog = cog
            self.lecturer = lecturer
            self.subject = subject
            self.batch = batch
            self.slot_span = slot_span

        @discord.ui.button(label="Request Admin Approval", style=discord.ButtonStyle.primary, emoji="")
        async def request_approval(self, interaction: discord.Interaction, button: discord.ui.Button):
            if interaction.user.id != self.lecturer.id:
                if hasattr(interaction, "response"):
                    await interaction.response.send_message(
                        "This button is for the lecturer who initiated the command.", ephemeral=True
                    )
                return

            button.disabled = True
            button.label = "Approval Requested"
            button.style = discord.ButtonStyle.secondary

            now = self.cog._now()
            date_str = now.strftime("%Y-%m-%d")
            req_key = f"{self.lecturer.id}_{self.subject.upper()}_{self.batch.upper()}_{date_str}"

            # Dispatch notification to bot owner
            app_view = AdminApprovalActionView(self.cog, self.lecturer.id, self.subject, self.batch, req_key)
            owner_embed = discord.Embed(
                title="Attendance Late-Marking Approval Request",
                description=(
                    f"**Lecturer:** {self.lecturer.mention} (`{self.lecturer.display_name}`)\n"
                    f"**Subject:** **{self.subject}**\n"
                    f"**Batch:** **Batch {self.batch.upper()}**\n"
                    f"**Class Slot:** `{self.slot_span}`\n"
                    f"**Current Time:** `{now:%H:%M %Z}`\n\n"
                    "The class window has closed. The lecturer is requesting a **60-minute window** to record attendance."
                ),
                color=COLOR_WARN,
                timestamp=now,
            )
            owner_embed.set_author(name="Timetable Attendance System")
            owner_embed.set_footer(text="Click Approve to unlock attendance for 60 minutes")

            sent_to_admin = False
            target_ids = []
            if hasattr(self.cog.bot, "get_primary_owner"):
                p_owner = await self.cog.bot.get_primary_owner()
                if p_owner:
                    target_ids.append(p_owner)
            elif getattr(self.cog.bot, "owner_id", None):
                target_ids.append(self.cog.bot.owner_id)

            if hasattr(self.cog.bot, "get_admin_ids"):
                for aid in self.cog.bot.get_admin_ids():
                    if aid not in target_ids:
                        target_ids.append(aid)

            for admin_id in target_ids:
                try:
                    admin_user = await self.cog.bot.fetch_user(admin_id)
                    if admin_user:
                        await admin_user.send(embed=owner_embed, view=app_view)
                        sent_to_admin = True
                except Exception as exc:
                    log.warning("Could not DM admin %s for attendance approval: %s", admin_id, exc)

            if not sent_to_admin and interaction.channel:
                mentions = " ".join(f"<@{aid}>" for aid in target_ids) or "@here"
                await interaction.channel.send(
                    content=f"{mentions} (Admin Attendance Approval Needed)",
                    embed=owner_embed,
                    view=app_view,
                )

            await interaction.response.edit_message(
                content="**Approval Request Sent!** The admin has been notified. You will be able to take attendance as soon as they approve.",
                view=self,
            )

    class AdminApprovalActionView(discord.ui.View):
        def __init__(self, cog: "AttendanceCog", lecturer_id: int, subject: str, batch: str, req_key: str):
            super().__init__(timeout=3600.0)  # 1 hour timeout
            self.cog = cog
            self.lecturer_id = lecturer_id
            self.subject = subject
            self.batch = batch
            self.req_key = req_key

        async def interaction_check(self, interaction: discord.Interaction) -> bool:
            is_adm = False
            if hasattr(self.cog.bot, "is_owner"):
                is_adm = await self.cog.bot.is_owner(interaction.user)
            if is_adm:
                return True
            await interaction.response.send_message(
                "🔒 Access Denied: Only Bot Administrators can approve or deny attendance requests.",
                ephemeral=True,
            )
            return False

        @discord.ui.button(label="Approve (60 Min Window)", style=discord.ButtonStyle.success, emoji="")
        async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
            now = self.cog._now()
            expiry = now + timedelta(minutes=60)
            self.cog.active_approvals[self.req_key] = expiry

            for child in self.children:
                setattr(child, "disabled", True)

            embed = discord.Embed(
                title="Attendance Request Approved",
                description=(
                    f"Approved 60-minute window for <@{self.lecturer_id}> to mark attendance "
                    f"for **{self.subject}** (Batch {self.batch.upper()}).\n"
                    f"Window expires at <t:{int(expiry.timestamp())}:t> (<t:{int(expiry.timestamp())}:R>)."
                ),
                color=COLOR_SUCCESS,
                timestamp=now,
            )
            embed.set_author(name="Timetable Attendance System")
            await interaction.response.edit_message(embed=embed, view=self)

            # Notify lecturer via DM
            with contextlib.suppress(Exception):
                user = await self.cog.bot.fetch_user(self.lecturer_id)
                if user:
                    await user.send(
                        f"**Your attendance request was approved!** You have 60 minutes to record attendance "
                        f"for **{self.subject}** (Batch {self.batch}). Run `/attendance take` to start."
                    )

        @discord.ui.button(label="Deny", style=discord.ButtonStyle.danger, emoji="")
        async def deny(self, interaction: discord.Interaction, button: discord.ui.Button):
            self.cog.active_approvals.pop(self.req_key, None)
            for child in self.children:
                setattr(child, "disabled", True)

            embed = discord.Embed(
                title="Attendance Request Denied",
                description=f"Request to mark late attendance for <@{self.lecturer_id}> was denied.",
                color=COLOR_ERROR,
            )
            embed.set_author(name="Timetable Attendance System")
            await interaction.response.edit_message(embed=embed, view=self)
else:
    AttendanceWorkflowView = None  # type: ignore[assignment, misc]
    ApprovalRequestView = None  # type: ignore[assignment, misc]
    AdminApprovalActionView = None  # type: ignore[assignment, misc]


# --------------------------------------------------------------------------- #
# Cog Implementation
# --------------------------------------------------------------------------- #
class AttendanceCog(commands.Cog):
    """Attendance management, access control, and student shortage tracking."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.active_approvals: dict[str, datetime] = {}  # req_key -> expiry

    def _timetable(self) -> Optional[Any]:
        return self.bot.get_cog("TimetableCog")

    def _now(self) -> datetime:
        tt = self._timetable()
        if tt is not None and hasattr(tt, "_now"):
            return tt._now()
        return datetime.now(timezone.utc)

    def _threshold(self) -> float:
        try:
            if CONFIG_FILE.is_file():
                conf = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
                return float(conf.get("attendance_threshold", DEFAULT_THRESHOLD))
        except Exception:
            pass
        return DEFAULT_THRESHOLD

    # ----- storage (atomic writes) ----------------------------------------- #
    def read_lecturers(self) -> dict[str, Lecturer]:
        lecturers: dict[str, Lecturer] = {}
        if LECTURERS_FILE.is_file():
            try:
                raw = json.loads(LECTURERS_FILE.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    for uid, ldata in raw.items():
                        if isinstance(ldata, dict):
                            lecturers[str(uid)] = Lecturer.from_dict(uid, ldata)
            except Exception as exc:
                log.warning("Could not read %s: %s", LECTURERS_FILE, exc)
        return lecturers

    def write_lecturers(self, lecturers: dict[str, Lecturer]) -> None:
        LECTURERS_FILE.parent.mkdir(parents=True, exist_ok=True)
        raw = {uid: lec.to_dict() for uid, lec in lecturers.items()}
        fd, tmp = tempfile.mkstemp(dir=LECTURERS_FILE.parent, prefix=".lecturers-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(raw, fh, indent=2, ensure_ascii=False)
                fh.write("\n")
            os.replace(tmp, LECTURERS_FILE)
        except Exception as exc:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise

    def get_lecturer(self, user_id: str | int) -> Optional[Lecturer]:
        return self.read_lecturers().get(str(user_id))

    def set_lecturer(
        self,
        user_id: str | int,
        name: str,
        subjects: list[str],
        batches: Optional[list[str]] = None,
    ) -> Lecturer:
        lecturers = self.read_lecturers()
        uid = str(user_id)
        now_iso = self._now().isoformat()
        lec = Lecturer(
            user_id=uid,
            name=name.strip(),
            subjects=[s.strip() for s in subjects if s.strip()],
            batches=[b.strip().upper() for b in (batches or ["ALL"]) if b.strip()] or ["ALL"],
            added_at=lecturers[uid].added_at if uid in lecturers and lecturers[uid].added_at else now_iso,
        )
        lecturers[uid] = lec
        self.write_lecturers(lecturers)
        return lec

    def delete_lecturer(self, user_id: str | int) -> bool:
        lecturers = self.read_lecturers()
        uid = str(user_id)
        if uid in lecturers:
            del lecturers[uid]
            self.write_lecturers(lecturers)
            return True
        return False

    def read_attendance(self) -> dict[str, AttendanceSession]:
        sessions: dict[str, AttendanceSession] = {}
        if ATTENDANCE_FILE.is_file():
            try:
                raw = json.loads(ATTENDANCE_FILE.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    for sid, sdata in raw.items():
                        if isinstance(sdata, dict):
                            sessions[str(sid)] = AttendanceSession.from_dict(sid, sdata)
            except Exception as exc:
                log.warning("Could not read %s: %s", ATTENDANCE_FILE, exc)
        return sessions

    def write_attendance(self, sessions: dict[str, AttendanceSession]) -> None:
        ATTENDANCE_FILE.parent.mkdir(parents=True, exist_ok=True)
        raw = {sid: sess.to_dict() for sid, sess in sessions.items()}
        fd, tmp = tempfile.mkstemp(dir=ATTENDANCE_FILE.parent, prefix=".attendance-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(raw, fh, indent=2, ensure_ascii=False)
                fh.write("\n")
            os.replace(tmp, ATTENDANCE_FILE)
        except Exception as exc:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise

    # ----- time window & access control checks ----------------------------- #
    def find_current_class_slot(
        self,
        lecturer: Lecturer,
        query_batch: Optional[str] = None,
        now: Optional[datetime] = None,
    ) -> tuple[Optional[Any], bool, str, bool]:
        """Check if lecturer has an active class slot right now or an active admin approval.

        Returns: (slot, is_authorized, reason, is_approval_override)
        """
        now = now or self._now()
        day_key = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"][now.weekday()]
        current_time = now.time()
        date_str = now.strftime("%Y-%m-%d")

        tt = self._timetable()
        if tt is None:
            return None, False, "Timetable cog is not loaded.", False

        all_slots = tt.read_all().get(day_key, [])
        lec_subs_lower = {s.lower() for s in lecturer.subjects}

        matching_slots = []
        for s in all_slots:
            if s.is_free:
                continue
            # Match subject
            if s.subject.lower() in lec_subs_lower or any(ls in s.subject.lower() for ls in lec_subs_lower):
                if query_batch is None or slot_matches_batch(s, query_batch):
                    matching_slots.append(s)

        if not matching_slots:
            return (
                None,
                False,
                f"You have no scheduled classes for **{', '.join(lecturer.subjects)}** today ({day_key.upper()}).",
                False,
            )

        # Check for window [slot.start - 5 mins, slot.end + 15 mins]
        for s in matching_slots:
            start_dt = datetime.combine(now.date(), s.start, tzinfo=now.tzinfo) - timedelta(minutes=5)
            end_dt = datetime.combine(now.date(), s.end, tzinfo=now.tzinfo) + timedelta(minutes=15)
            now_dt = datetime.combine(now.date(), current_time, tzinfo=now.tzinfo)

            if start_dt <= now_dt <= end_dt:
                return s, True, "Active class timeframe", False

        # If not in active window, check if admin has granted an override
        for s in matching_slots:
            # Check batches for this slot
            target_batches = [query_batch.upper()] if query_batch else list(s.batches)
            for b in target_batches:
                req_key = f"{lecturer.user_id}_{s.subject.upper()}_{b.upper()}_{date_str}"
                expiry = self.active_approvals.get(req_key)
                if expiry and now <= expiry:
                    return s, True, f"Admin approval active until {expiry:%H:%M}", True

        # Class slot exists today, but current time is outside the permitted window
        first_slot = matching_slots[0]
        return (
            first_slot,
            False,
            f"Class window closed. Scheduled time is `{first_slot.span}`, but current time is `{now:%H:%M}`.",
            False,
        )

    # ----- commands -------------------------------------------------------- #
    @commands.hybrid_group(name="attendance", description="Lecturer and student attendance management.")
    async def attendance_group(self, ctx: commands.Context) -> None:
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @attendance_group.command(
        name="take",
        aliases=["mark"],
        description="Mark attendance for your current class session. Lecturer only.",
    )
    @app_commands.describe(
        batch="Batch number (e.g. 1, 2). Required if multiple batches share the class.",
        subject="Subject name if you teach multiple subjects at this time.",
    )
    async def attendance_take(
        self,
        ctx: commands.Context,
        batch: Optional[str] = None,
        subject: Optional[str] = None,
    ) -> None:
        user_id = str(ctx.author.id)
        lecturer = self.get_lecturer(user_id)

        # Check if caller is registered as lecturer
        if not lecturer:
            embed = discord.Embed(
                title="Lecturer Access Required",
                description=(
                    "You are not registered as an authorized lecturer in the bot.\n\n"
                    "-# *Server administrators can authorize you using `/setlecturer member:@You subject:\"Subject\"`.*"
                ),
                color=COLOR_ERROR,
            )
            await _safe_send(ctx, embed=embed, ephemeral=True)
            return

        tt = self._timetable()
        actual_batch = tt.resolve_batch_input(batch) if (tt and batch) else batch
        now = self._now()
        slot, is_auth, reason, is_override = self.find_current_class_slot(
            lecturer=lecturer,
            query_batch=actual_batch,
            now=now,
        )

        if not slot:
            embed = discord.Embed(
                title="No Classes Scheduled Today",
                description=f"> {reason}\n\nCheck `/timetable` to view scheduled hours for your subjects.",
                color=COLOR_WARN,
            )
            embed.set_author(name="Timetable Attendance System")
            await _safe_send(ctx, embed=embed, ephemeral=True)
            return

        effective_subject = subject or slot.subject
        effective_batch = (batch.strip().upper() if batch else None) or (slot.batches[0] if slot.batches and slot.batches[0] != "ALL" else "1")

        # Check if outside permitted time window
        if not is_auth:
            embed = discord.Embed(
                title="Class Attendance Window Closed",
                description=(
                    f"You cannot take attendance right now for **{effective_subject}** (Batch {effective_batch}).\n\n"
                    f"> Scheduled Slot: **`{slot.span}`**\n"
                    f"> Current Time: **`{now:%H:%M %Z}`**\n\n"
                    f"**Reason:** {reason}\n\n"
                    "If you missed your class window, click **Request Admin Approval** below "
                    "to notify the administrator for a 60-minute late unlock."
                ),
                color=COLOR_WARN,
            )
            embed.set_author(name="Timetable Attendance System")
            embed.set_footer(text="Anti-fraud policy: Attendance must be marked during lecture hours")

            view = None
            if _HAS_UI and ApprovalRequestView is not None and isinstance(ctx.author, discord.Member):
                view = ApprovalRequestView(self, ctx.author, effective_subject, effective_batch, slot.span)

            await _safe_send(ctx, embed=embed, view=view, ephemeral=True)
            return

        # Fetch students for this batch from TimetableCog roster
        tt = self._timetable()
        batch_students = []
        if tt:
            roster_students = tt.get_batch_students(effective_batch)
            if roster_students:
                for s in roster_students:
                    uid = s.user_id if s.user_id else f"roll_{s.roll_no}"
                    s_name = s.name if s.name else f"Roll {s.roll_no}"
                    batch_students.append({
                        "user_id": uid,
                        "roll_no": s.roll_no,
                        "name": s_name,
                        "is_linked": bool(s.user_id),
                    })
            else:
                # Fallback to users.json profiles if roster not yet explicitly populated
                profiles = tt.read_profiles()
                for uid, p in profiles.items():
                    if p.batch.upper() == effective_batch.upper() or effective_batch.upper() == "ALL":
                        batch_students.append({
                            "user_id": uid,
                            "roll_no": p.roll_no or "999",
                            "name": p.name or f"User {uid[:5]}",
                            "is_linked": True,
                        })

        # Sort by roll number naturally, then name
        batch_students.sort(key=lambda s: (natural_sort_key(s["roll_no"]), s["name"]))

        if not batch_students:
            embed = discord.Embed(
                title="No Students in Batch Roster",
                description=(
                    f"No students or roll numbers are populated for **Batch {effective_batch}** yet!\n\n"
                    f"**How to populate student roll numbers:**\n"
                    f"• Generate range: `/populaterolls batch:{effective_batch} start:1 end:60`\n"
                    f"• Bulk paste list: `/addstudents batch:{effective_batch} roster_text:<list>`\n"
                    f"• Add individual: `/addstudent batch:{effective_batch} roll_no:01 name:Alice`\n"
                    f"• Upload CSV: `/importroster batch:{effective_batch}`\n"
                    f"• Or students can link: `/link batch:{effective_batch} roll_no:01`"
                ),
                color=COLOR_WARN,
            )
            embed.set_author(name="Timetable Attendance System")
            await _safe_send(ctx, embed=embed, ephemeral=True)
            return

        if not _HAS_UI or AttendanceWorkflowView is None:
            # Fallback for environments without UI
            embed = discord.Embed(
                title="Attendance Marking (Plain Mode)",
                description=(
                    f"Ready to record attendance for **{effective_subject}** (Batch {effective_batch}).\n"
                    f"Found **{len(batch_students)}** registered students."
                ),
                color=COLOR_PRIMARY,
            )
            await _safe_send(ctx, embed=embed, ephemeral=True)
            return

        # Launch interactive UI
        view = AttendanceWorkflowView(
            cog=self,
            lecturer_id=ctx.author.id,
            subject=effective_subject,
            batch=effective_batch,
            slot_span=slot.span,
            students=batch_students,
            approval_override=is_override,
        )
        await _safe_send(ctx, embed=view.build_preview_embed(), view=view, ephemeral=True)

    @attendance_group.command(
        name="summary",
        description="View recent attendance sessions for a batch and subject.",
    )
    @app_commands.describe(
        batch="Batch number (e.g. 1, 2)",
        subject="Optional subject filter",
    )
    async def attendance_summary(
        self,
        ctx: commands.Context,
        batch: str,
        subject: Optional[str] = None,
    ) -> None:
        target_batch = batch.strip().upper()
        target_sub = subject.strip().lower() if subject else None

        sessions = self.read_attendance()
        matching = [
            s for s in sessions.values()
            if s.batch == target_batch and (target_sub is None or target_sub in s.subject.lower())
        ]
        matching.sort(key=lambda s: s.date, reverse=True)

        if not matching:
            embed = discord.Embed(
                title="No Attendance Records Found",
                description=f"No sessions recorded yet for **Batch {target_batch}**" + (f" in *{subject}*" if subject else "") + ".",
                color=COLOR_MUTED,
            )
            embed.set_author(name="Timetable Attendance System")
            await _safe_send(ctx, embed=embed, ephemeral=True)
            return

        total_sessions = len(matching)
        avg_pct = sum(s.percentage for s in matching) / total_sessions

        embed = discord.Embed(
            title=f"Attendance Summary — Batch {target_batch}",
            description=(
                f"> Total Sessions Conducted: **{total_sessions}**\n"
                f"> Batch Average Attendance: **{avg_pct:.1f}%**\n\n"
                "### ️ Recent Sessions"
            ),
            color=COLOR_PRIMARY,
        )
        embed.set_author(name="Timetable Attendance System")

        lines = []
        for s in matching[:10]:
            icon = "" if s.percentage >= 75.0 else ""
            override_tag = " `[Override]`" if s.approval_override else ""
            lines.append(
                f"{icon} **{s.date}** (`{s.slot_start}`) — **{s.subject}**{override_tag}\n"
                f"> Present: `{s.present_count}/{s.total_students}` (**{s.percentage:.1f}%**)   •   Absent: `{s.absent_count}`"
            )

        embed.add_field(name="Recent Classes", value="\n\n".join(lines), inline=False)
        embed.set_footer(text=f"Total {total_sessions} sessions recorded • Export full data with /attendance export")
        await _safe_send(ctx, embed=embed, ephemeral=True)

    @attendance_group.command(
        name="export",
        description="Export attendance records to a downloadable CSV spreadsheet. Admin/Lecturer.",
    )
    @app_commands.describe(
        batch="Batch number to export",
        subject="Optional subject filter",
    )
    async def attendance_export(
        self,
        ctx: commands.Context,
        batch: str,
        subject: Optional[str] = None,
    ) -> None:
        target_batch = batch.strip().upper()
        target_sub = subject.strip().lower() if subject else None

        sessions = self.read_attendance()
        matching = [
            s for s in sessions.values()
            if s.batch == target_batch and (target_sub is None or target_sub in s.subject.lower())
        ]
        matching.sort(key=lambda s: (s.date, s.slot_start))

        if not matching:
            embed = discord.Embed(
                title="No Attendance Data To Export",
                description=f"No recorded attendance sessions found for **Batch {target_batch}**.",
                color=COLOR_WARN,
            )
            await _safe_send(ctx, embed=embed, ephemeral=True)
            return

        # Collect unique students across matching sessions
        student_map: dict[str, tuple[str, str]] = {}  # uid -> (roll_no, name)
        for s in matching:
            for uid, r in s.records.items():
                student_map[uid] = (r.roll_no, r.name)

        sorted_students = sorted(student_map.items(), key=lambda item: (natural_sort_key(item[1][0]), item[1][1]))

        # Generate CSV buffer
        output = io.StringIO()
        writer = csv.writer(output)

        # Header row: Roll No, Student Name, [Session Dates...], Total Attended, Total Conducted, Percentage
        date_headers = [f"{s.date} ({s.slot_start} {s.subject[:10]})" for s in matching]
        header = ["Roll No", "Student Name", "Discord ID"] + date_headers + ["Attended", "Total Classes", "Attendance %"]
        writer.writerow(header)

        for uid, (roll, name) in sorted_students:
            row = [roll, name, uid]
            attended_cnt = 0
            for s in matching:
                rec = s.records.get(uid)
                status = rec.status if rec else "N/A"
                if status == "present":
                    row.append("P")
                    attended_cnt += 1
                elif status == "absent":
                    row.append("A")
                else:
                    row.append("-")
            total_cls = len(matching)
            pct = (attended_cnt / total_cls * 100.0) if total_cls else 0.0
            row.extend([str(attended_cnt), str(total_cls), f"{pct:.1f}%"])
            writer.writerow(row)

        output.seek(0)
        filename = f"Attendance_Batch_{target_batch}_{self._now():%Y%m%d}.csv"
        discord_file = discord.File(fp=io.BytesIO(output.getvalue().encode("utf-8")), filename=filename)

        embed = discord.Embed(
            title="Attendance Export Complete",
            description=(
                f"Generated spreadsheet for **Batch {target_batch}**.\n\n"
                f"> Sessions: **{len(matching)}**\n"
                f"> Students: **{len(sorted_students)}**"
            ),
            color=COLOR_SUCCESS,
        )
        embed.set_author(name="Timetable Attendance System")
        embed.set_footer(text=f"Exported on {self._now():%Y-%m-%d %H:%M} • CSV Format")

        await ctx.send(embed=embed, file=discord_file)

    @commands.hybrid_command(
        name="myattendance",
        aliases=["attendance_me", "bunk", "att"],
        description="Check your attendance percentage and 75% safe-bunk / shortage calculator.",
    )
    @app_commands.describe(subject="Optional: Filter breakdown for a specific subject")
    async def myattendance(self, ctx: commands.Context, subject: Optional[str] = None) -> None:
        user_id = str(ctx.author.id)
        sessions = self.read_attendance()
        threshold = self._threshold()

        # Find sessions where this user was on the roster (by user_id or linked roll number)
        tt = self._timetable()
        profile = tt.get_user_profile(user_id) if tt else None
        linked_roll = profile.roll_no if profile else None
        linked_batch = profile.batch.upper() if profile else None

        user_sessions: list[tuple[AttendanceSession, str]] = []  # (session, status)
        for s in sessions.values():
            if subject is not None and subject.strip().lower() not in s.subject.lower():
                continue

            status = None
            if user_id in s.records:
                status = s.records[user_id].status
            elif linked_roll and linked_batch and s.batch.upper() == linked_batch:
                # Fallback to roll number matching for sessions recorded before account linking
                for r_uid, r in s.records.items():
                    if r.roll_no == linked_roll:
                        status = r.status
                        break

            if status is not None:
                user_sessions.append((s, status))

        if not user_sessions:
            batch_str = f"Batch {profile.batch}" if profile else "No batch"
            roll_str = f" (Roll `{profile.roll_no}`)" if profile and profile.roll_no else ""

            embed = discord.Embed(
                title="No Attendance Records Found",
                description=(
                    f"No attendance records were found for your account ({ctx.author.mention}).\n\n"
                    f"**Current Status:** {batch_str}{roll_str}\n"
                    "Make sure your account is linked with `/link batch:<your_batch> roll_no:<your_roll>` "
                    "and that your lecturers have marked attendance for your classes."
                ),
                color=COLOR_MUTED,
            )
            embed.set_author(name="Student Attendance Portal")
            await _safe_send(ctx, embed=embed, ephemeral=True)
            return

        total_classes = len(user_sessions)
        attended_classes = sum(1 for _, st in user_sessions if st == "present")
        overall_pct = (attended_classes / total_classes * 100.0) if total_classes else 0.0

        # Group by subject
        subject_stats: dict[str, tuple[int, int]] = {}  # sub -> (attended, total)
        for s, st in user_sessions:
            sub = s.subject
            att, tot = subject_stats.get(sub, (0, 0))
            is_p = 1 if st == "present" else 0
            subject_stats[sub] = (att + is_p, tot + 1)

        is_safe = overall_pct >= threshold
        color = COLOR_SUCCESS if is_safe else (COLOR_WARN if overall_pct >= 65.0 else COLOR_ERROR)

        embed = discord.Embed(
            title=f"Attendance Portal — {ctx.author.display_name}",
            description=(
                f"Overall Attendance: {_fmt_pct_bar(overall_pct / 100.0)}\n\n"
                f"> Total Classes Conducted: **{total_classes}**\n"
                f"> Attended: **{attended_classes}**   •   Missed: **{total_classes - attended_classes}**\n"
                f"> Required Minimum: **{threshold:.0f}%**"
            ),
            color=color,
        )
        embed.set_author(name="Student Attendance Portal")
        if hasattr(ctx.author, "display_avatar") and ctx.author.display_avatar:
            embed.set_thumbnail(url=ctx.author.display_avatar.url)

        # Subject breakdown
        sub_lines = []
        for sub_name, (sub_att, sub_tot) in sorted(subject_stats.items()):
            sub_pct = (sub_att / sub_tot * 100.0) if sub_tot else 0.0
            icon = "" if sub_pct >= threshold else ""
            sub_lines.append(f"{icon} **{sub_name}**: `{sub_att}/{sub_tot}` (**{sub_pct:.1f}%**)")

        embed.add_field(name="Subject Breakdown", value="\n".join(sub_lines), inline=False)

        # 75% Safe Bunk & Shortage Calculator
        if is_safe:
            # Safe bunk formula: floor((A - R * T) / R)
            req_ratio = threshold / 100.0
            max_bunks = math.floor((attended_classes - req_ratio * total_classes) / req_ratio)
            if max_bunks > 0:
                calc_text = (
                    f"**You are in the Safe Zone!**\n"
                    f"You can safely skip **{max_bunks} more class{'es' if max_bunks != 1 else ''}** "
                    f"and your attendance will still stay above **{threshold:.0f}%**."
                )
            else:
                calc_text = (
                    f"**On the Margin!**\n"
                    f"Your attendance is exactly around {threshold:.0f}%. If you miss your next class, you will drop into shortage."
                )
        else:
            # Shortage recovery formula: ceil((R * T - A) / (1 - R))
            req_ratio = threshold / 100.0
            needed = math.ceil((req_ratio * total_classes - attended_classes) / (1.0 - req_ratio))
            calc_text = (
                f"**Attendance Shortage Alert!**\n"
                f"You are currently below the required {threshold:.0f}% threshold.\n"
                f"You must attend the next **{needed} consecutive class{'es' if needed != 1 else ''}** "
                f"without missing to recover your attendance to **{threshold:.0f}%**."
            )

        embed.add_field(name="75% Attendance Advisor", value=calc_text, inline=False)
        embed.set_footer(text=f"Threshold: {threshold:.0f}% • Keep your attendance high to avoid exam disqualification")
        await _safe_send(ctx, embed=embed, ephemeral=True)

    # ----- admin management commands --------------------------------------- #
    @commands.hybrid_command(
        name="setlecturer",
        description="Authorize a member as lecturer for a subject. Owner only.",
    )
    @commands.is_owner()
    @app_commands.describe(
        member="The server member to authorize as lecturer",
        subject="Subject name(s) they teach (comma-separated)",
        batch="Optional batch filter (e.g. 1 or ALL)",
    )
    async def setlecturer(
        self,
        ctx: commands.Context,
        member: discord.Member,
        subject: str,
        batch: str = "ALL",
    ) -> None:
        subjects = [s.strip() for s in subject.split(",") if s.strip()]
        batches = [b.strip().upper() for b in batch.split(",") if b.strip()] or ["ALL"]

        lec = self.set_lecturer(
            user_id=member.id,
            name=member.display_name,
            subjects=subjects,
            batches=batches,
        )

        embed = discord.Embed(
            title="Lecturer Authorized",
            description=f"Successfully registered {member.mention} as an authorized lecturer.",
            color=COLOR_SUCCESS,
        )
        embed.set_author(name="Timetable Attendance Admin")
        embed.add_field(name="Lecturer", value=member.mention, inline=True)
        embed.add_field(name="Subjects", value=", ".join(lec.subjects), inline=True)
        embed.add_field(name="Batches", value=", ".join(lec.batches), inline=True)
        embed.set_footer(text="Saved in data/lecturers.json • Lecturer can now use /attendance take")
        await _safe_send(ctx, embed=embed, ephemeral=True)

    @commands.hybrid_command(
        name="dellecturer",
        description="Remove a member's lecturer authorization. Owner only.",
    )
    @commands.is_owner()
    @app_commands.describe(member="The server member to remove")
    async def dellecturer(self, ctx: commands.Context, member: discord.Member) -> None:
        removed = self.delete_lecturer(member.id)
        if removed:
            embed = discord.Embed(
                title="Lecturer Removed",
                description=f"Revoked lecturer privileges from {member.mention}.",
                color=COLOR_WARN,
            )
        else:
            embed = discord.Embed(
                title="ℹ️ Lecturer Not Found",
                description=f"{member.mention} was not registered as a lecturer.",
                color=COLOR_PRIMARY,
            )
        embed.set_author(name="Timetable Attendance Admin")
        await _safe_send(ctx, embed=embed, ephemeral=True)

    @commands.hybrid_command(
        name="lecturers",
        description="List all authorized lecturers and their assigned subjects.",
    )
    async def lecturers_list(self, ctx: commands.Context) -> None:
        lecturers = self.read_lecturers()
        if not lecturers:
            embed = discord.Embed(
                title="Registered Lecturers",
                description="No lecturers are currently registered in the system.\nAdmins can add lecturers with `/setlecturer`.",
                color=COLOR_MUTED,
            )
            embed.set_author(name="Timetable Attendance System")
            await _safe_send(ctx, embed=embed, ephemeral=True)
            return

        embed = discord.Embed(
            title=f"Authorized Lecturers ({len(lecturers)})",
            description="Members authorized to record attendance for their respective subjects:",
            color=COLOR_PRIMARY,
        )
        embed.set_author(name="Timetable Attendance System")

        for uid, lec in lecturers.items():
            sub_str = ", ".join(lec.subjects) if lec.subjects else "All"
            batch_str = ", ".join(lec.batches) if lec.batches else "ALL"
            embed.add_field(
                name=f"{lec.name} (<@{uid}>)",
                value=f"> Subjects: **{sub_str}**\n> Batches: **Batch {batch_str}**",
                inline=False,
            )

        embed.set_footer(text="Use /setlecturer or /dellecturer to manage")
        await _safe_send(ctx, embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AttendanceCog(bot))
