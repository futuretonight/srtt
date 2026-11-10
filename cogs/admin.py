"""Admin cog: system administration, timetable modification, and permission controls."""
from __future__ import annotations

import logging
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

from cogs.timetable import (
    DAY_NAMES,
    parse_batches,
    parse_date_input,
    resolve_day,
    send_component_v2,
)

log = logging.getLogger("timetable_bot.admin")

COLOR_SUCCESS = 0x57F287
COLOR_WARN = 0xFEE75C
COLOR_ERROR = 0xED4245
COLOR_PRIMARY = 0x5865F2
COLOR_MUTED = 0x4F545C

DAY_CHOICES = [
    app_commands.Choice(name=name, value=value)
    for name, value in (
        ("Monday", "mon"),
        ("Tuesday", "tue"),
        ("Wednesday", "wed"),
        ("Thursday", "thu"),
        ("Friday", "fri"),
        ("Saturday", "sat"),
        ("Sunday", "sun"),
    )
]


def _normalize_ext(name: str) -> str:
    """'timetable', 'cogs/timetable.py' and 'cogs.timetable' all -> 'cogs.timetable'."""
    name = name.strip().replace("/", ".").replace("\\", ".")
    if name.endswith(".py"):
        name = name[:-3]
    return name if name.startswith("cogs.") else f"cogs.{name}"


def is_admin():
    """Check that user is the bot application owner, bot.owner_id, or listed in config['admins']."""
    async def predicate(ctx: commands.Context) -> bool:
        if hasattr(ctx.bot, "is_owner") and await ctx.bot.is_owner(ctx.author):
            return True
        raise commands.CheckFailure("This command is restricted to bot administrators.")
    return commands.check(predicate)


def is_admin_or_cr():
    """Check that user is a bot administrator OR an authorized Class Representative."""
    async def predicate(ctx: commands.Context) -> bool:
        if hasattr(ctx.bot, "is_owner") and await ctx.bot.is_owner(ctx.author):
            return True
        tt = ctx.bot.get_cog("TimetableCog")
        if tt and hasattr(tt, "is_representative") and tt.is_representative(ctx.author.id):
            return True
        raise commands.CheckFailure(
            "This command is restricted to bot administrators or authorized Class Representatives."
        )
    return commands.check(predicate)


class AdminCog(commands.Cog):
    """System administration, timetable modification, and permission controls."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    def _timetable(self):
        return self.bot.get_cog("TimetableCog")

    async def _reload_one(self, ext: str) -> tuple[bool, str]:
        try:
            await self.bot.reload_extension(ext)
            return True, f"`{ext}` reloaded"
        except commands.ExtensionNotLoaded:
            try:
                await self.bot.load_extension(ext)
                return True, f"`{ext}` loaded"
            except commands.ExtensionNotFound:
                return False, f"`{ext}` — no such module"
            except commands.ExtensionError as exc:
                log.exception("Failed to load %s", ext)
                return False, f"`{ext}` — {type(exc).__name__}: {getattr(exc, 'original', exc)}"
        except commands.ExtensionNotFound:
            return False, f"`{ext}` — no such module"
        except commands.ExtensionError as exc:
            log.exception("Failed to reload %s", ext)
            return False, f"`{ext}` — {type(exc).__name__}: {getattr(exc, 'original', exc)}"

    # ========================================================================= #
    # Administrator Management (Owner & Admins)
    # ========================================================================= #
    @commands.hybrid_command(
        name="setadmin",
        aliases=["addadmin", "makeadmin"],
        description="Grant a user full administrator privileges (same powers as owner).",
    )
    @is_admin()
    @app_commands.describe(member="The server member to grant full administrator powers")
    async def setadmin(self, ctx: commands.Context, member: discord.Member) -> None:
        if not hasattr(self.bot, "add_admin"):
            await ctx.send("Bot does not support dynamic admin assignment.", ephemeral=True)
            return

        primary_owner = await self.bot.get_primary_owner()
        if member.id == primary_owner:
            await ctx.send(
                embed=discord.Embed(
                    title="ℹ️ Already Primary Owner",
                    description=f"{member.mention} is already the primary bot owner.",
                    color=COLOR_PRIMARY,
                ),
                ephemeral=True,
            )
            return

        success = self.bot.add_admin(member.id)
        if not success:
            await ctx.send(
                embed=discord.Embed(
                    title="ℹ️ Already Administrator",
                    description=f"{member.mention} is already an authorized administrator.",
                    color=COLOR_PRIMARY,
                ),
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title="Administrator Privileges Granted",
            description=(
                f"Successfully promoted {member.mention} to **Bot Administrator**!\n"
                "They now possess full administrative authority (same powers as owner):\n"
                "• Master schedule and timetable configuration (`/setclass`, `/delclass`)\n"
                "• Date-specific overrides and cancellations (`/reschedule`, `/cancelclass`)\n"
                "• Class representative assignments (`/setcr`, `/delcr`)\n"
                "• Faculty authorization (`/setlecturer`, `/dellecturer`)\n"
                "• Bot maintenance and live synchronization (`/reload`, `/sync`, `/setprefix`)"
            ),
            color=COLOR_SUCCESS,
        )
        embed.set_author(name="Timetable Administration")
        embed.add_field(name="Administrator", value=f"{member.mention} (`{member.id}`)", inline=True)
        embed.add_field(name="Promoted By", value=ctx.author.mention, inline=True)
        embed.set_footer(text="Saved in data/config.json • Live immediately")

        c_v2 = {
            "type": 17,
            "spoiler": False,
            "accent_color": COLOR_SUCCESS,
            "components": [
                {
                    "type": 10,
                    "content": f"## Administrator Privileges Granted\n{member.mention} now has **full administrator powers** identical to the owner.",
                },
                {"type": 14, "divider": True, "spacing": 1},
                {
                    "type": 10,
                    "content": (
                        f"**Admin Member:** {member.mention} (`{member.id}`)\n"
                        f"**Promoted By:** {ctx.author.mention}\n"
                        "**Scope:** Full Global Authority (Timetables, Overrides, CRs, Faculty, Config, Hot-Reload)"
                    ),
                },
                {"type": 14, "divider": True, "spacing": 1},
                {"type": 10, "content": "-# Saved in data/config.json • Active immediately"},
            ],
        }
        await send_component_v2(ctx, c_v2, fallback_embed=embed)

    @commands.hybrid_command(
        name="deladmin",
        aliases=["removeadmin", "rmadmin"],
        description="Revoke administrator privileges from a user.",
    )
    @is_admin()
    @app_commands.describe(member="The administrator member to revoke privileges from")
    async def deladmin(self, ctx: commands.Context, member: discord.Member) -> None:
        if not hasattr(self.bot, "remove_admin"):
            await ctx.send("Bot does not support dynamic admin removal.", ephemeral=True)
            return

        primary_owner = await self.bot.get_primary_owner()
        if member.id == primary_owner:
            await ctx.send(
                embed=discord.Embed(
                    title="🔒 Action Denied",
                    description="The primary bot owner cannot be removed from administrators.",
                    color=COLOR_ERROR,
                ),
                ephemeral=True,
            )
            return

        if member.id == ctx.author.id:
            await ctx.send(
                embed=discord.Embed(
                    title="⚠️ Action Denied",
                    description="You cannot remove your own administrator status.",
                    color=COLOR_WARN,
                ),
                ephemeral=True,
            )
            return

        removed = self.bot.remove_admin(member.id)
        if not removed:
            await ctx.send(
                embed=discord.Embed(
                    title="ℹ️ Not Found",
                    description=f"{member.mention} is not in the co-administrators list.",
                    color=COLOR_PRIMARY,
                ),
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title="Administrator Privileges Revoked",
            description=f"Revoked administrator privileges from {member.mention}.",
            color=COLOR_WARN,
        )
        embed.set_author(name="Timetable Administration")
        embed.add_field(name="User", value=f"{member.mention} (`{member.id}`)", inline=True)
        embed.add_field(name="Revoked By", value=ctx.author.mention, inline=True)
        embed.set_footer(text="Saved in data/config.json • Live immediately")

        c_v2 = {
            "type": 17,
            "spoiler": False,
            "accent_color": COLOR_WARN,
            "components": [
                {
                    "type": 10,
                    "content": f"## Administrator Privileges Revoked\nAdministrator status for {member.mention} has been removed.",
                },
                {"type": 14, "divider": True, "spacing": 1},
                {
                    "type": 10,
                    "content": f"**Member:** {member.mention} (`{member.id}`)\n**Revoked By:** {ctx.author.mention}",
                },
                {"type": 14, "divider": True, "spacing": 1},
                {"type": 10, "content": "-# Removed from data/config.json"},
            ],
        }
        await send_component_v2(ctx, c_v2, fallback_embed=embed)

    @commands.hybrid_command(
        name="admins",
        aliases=["adminlist"],
        description="List the primary owner and all authorized bot administrators.",
    )
    @is_admin()
    async def admins_list(self, ctx: commands.Context) -> None:
        primary_owner = await self.bot.get_primary_owner()
        admin_ids = self.bot.get_admin_ids() if hasattr(self.bot, "get_admin_ids") else set()

        lines = []
        if primary_owner:
            lines.append(f"• <@{primary_owner}> (`{primary_owner}`) — **Primary Owner** 👑")
        for aid in sorted(admin_ids):
            if aid == primary_owner:
                continue
            lines.append(f"• <@{aid}> (`{aid}`) — **Administrator** 🛡️")

        if not lines:
            lines.append("*No administrators configured.*")

        embed = discord.Embed(
            title="Bot Administrators",
            description="\n".join(lines),
            color=COLOR_PRIMARY,
        )
        embed.set_author(name="Timetable Administration")
        embed.set_footer(text=f"Total: {len(lines)} authorized administrator(s) • Saved in data/config.json")

        c_v2 = {
            "type": 17,
            "spoiler": False,
            "accent_color": COLOR_PRIMARY,
            "components": [
                {
                    "type": 10,
                    "content": "## Bot Administrators\nAuthorized administrators with full owner-level powers:",
                },
                {"type": 14, "divider": True, "spacing": 1},
                {"type": 10, "content": "\n".join(lines)},
                {"type": 14, "divider": True, "spacing": 1},
                {"type": 10, "content": "-# Use /setadmin or /deladmin to manage administrator accounts"},
            ],
        }
        await send_component_v2(ctx, c_v2, fallback_embed=embed)

    # ========================================================================= #
    # Class Representative Management
    # ========================================================================= #
    @commands.hybrid_command(
        name="setcr",
        aliases=["addcr", "assigncr"],
        description="Authorize a member as Class Representative for a specific batch/class.",
    )
    @is_admin()
    @app_commands.describe(
        member="The server member to authorize as Class Representative",
        batch="Batch code or class they represent (e.g. 26CAIBTAIB16 or 1)",
    )
    async def setcr(self, ctx: commands.Context, member: discord.Member, batch: str) -> None:
        tt = self._timetable()
        if tt is None:
            await ctx.send("The timetable cog is not loaded.", ephemeral=True)
            return

        resolved_batch = tt.resolve_batch_input(batch)
        if not resolved_batch or resolved_batch.upper() in ("ALL", "*", "ANY"):
            await ctx.send(
                embed=discord.Embed(
                    title="⚠️ Specific Batch Required",
                    description="Class Representatives must be assigned to a specific batch/class code (not 'ALL').\nExample: `/setcr member:@User batch:26CAIBTAIB16`",
                    color=COLOR_WARN,
                ),
                ephemeral=True,
            )
            return

        rep = tt.set_representative(
            user_id=member.id,
            name=member.display_name,
            batches=[resolved_batch],
            assigned_by=ctx.author.id,
        )

        existing_prof = tt.get_user_profile(member.id)
        if not existing_prof:
            tt.set_user_profile(
                user_id=member.id,
                batch=resolved_batch,
                name=member.display_name,
            )

        embed = discord.Embed(
            title="Class Representative Authorized",
            description=(
                f"Successfully authorized {member.mention} as **Class Representative** for **Batch {resolved_batch}**!\n\n"
                "**Permissions Granted:**\n"
                f"• Edit class schedule for **Batch {resolved_batch}** (`/setclass`, `/delclass`)\n"
                f"• Reschedule or cancel lectures for **Batch {resolved_batch}** (`/reschedule`, `/cancelclass`, `/deloverride`)\n"
                f"• Manage student roster for **Batch {resolved_batch}** (`/students`, `/linkuser`)\n"
                "*(Global changes to master schedules remain restricted to Global Administrators)*"
            ),
            color=COLOR_SUCCESS,
        )
        embed.set_author(name="Timetable Administration")
        embed.add_field(name="Representative", value=f"{member.mention} (`{member.id}`)", inline=True)
        embed.add_field(name="Assigned Class", value=f"**Batch {resolved_batch}**", inline=True)
        embed.add_field(name="Authorized By", value=ctx.author.mention, inline=True)
        embed.set_footer(text="Saved in data/representatives.json • Live immediately")

        c_v2 = {
            "type": 17,
            "spoiler": False,
            "accent_color": COLOR_SUCCESS,
            "components": [
                {
                    "type": 10,
                    "content": f"## Class Representative Authorized\n{member.mention} is now authorized to manage the timetable for **Batch {resolved_batch}**.",
                },
                {"type": 14, "divider": True, "spacing": 1},
                {
                    "type": 10,
                    "content": (
                        f"**Representative:** {member.mention}\n"
                        f"**Assigned Batch:** **Batch {resolved_batch}**\n"
                        f"**Scope:** Timetable editing and overrides for Batch {resolved_batch} only\n"
                        f"**Authorized By:** {ctx.author.mention}"
                    ),
                },
                {"type": 14, "divider": True, "spacing": 1},
                {"type": 10, "content": "-# Saved in data/representatives.json • Live immediately"},
            ],
        }
        await send_component_v2(ctx, c_v2, fallback_embed=embed)

    @commands.hybrid_command(
        name="delcr",
        aliases=["removecr", "rmcr"],
        description="Revoke Class Representative authorization from a member.",
    )
    @is_admin()
    @app_commands.describe(
        member="The Class Representative to remove",
        batch="Optional batch filter if user represents multiple batches",
    )
    async def delcr(self, ctx: commands.Context, member: discord.Member, batch: Optional[str] = None) -> None:
        tt = self._timetable()
        if tt is None:
            await ctx.send("The timetable cog is not loaded.", ephemeral=True)
            return

        resolved_batch = tt.resolve_batch_input(batch) if batch else None
        removed = tt.delete_representative(member.id, batch=resolved_batch)
        if not removed:
            b_info = f" for Batch {resolved_batch}" if resolved_batch else ""
            await ctx.send(
                embed=discord.Embed(
                    title="ℹ️ Not Found",
                    description=f"{member.mention} was not registered as Class Representative{b_info}.",
                    color=COLOR_PRIMARY,
                ),
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title="Class Representative Revoked",
            description=f"Revoked Class Representative privileges from {member.mention}.",
            color=COLOR_WARN,
        )
        embed.set_author(name="Timetable Administration")
        embed.add_field(name="User", value=member.mention, inline=True)
        embed.add_field(name="Revoked By", value=ctx.author.mention, inline=True)
        embed.set_footer(text="Updated data/representatives.json")

        c_v2 = {
            "type": 17,
            "spoiler": False,
            "accent_color": COLOR_WARN,
            "components": [
                {
                    "type": 10,
                    "content": f"## Class Representative Revoked\nClass Representative status removed for {member.mention}.",
                },
                {"type": 14, "divider": True, "spacing": 1},
                {"type": 10, "content": f"**User:** {member.mention}\n**Revoked By:** {ctx.author.mention}"},
                {"type": 14, "divider": True, "spacing": 1},
                {"type": 10, "content": "-# Updated data/representatives.json"},
            ],
        }
        await send_component_v2(ctx, c_v2, fallback_embed=embed)

    @commands.hybrid_command(
        name="representatives",
        aliases=["crlist", "crs"],
        description="List all authorized Class Representatives and their assigned classes.",
    )
    @app_commands.describe(batch="Optional batch filter")
    async def representatives_list(self, ctx: commands.Context, batch: Optional[str] = None) -> None:
        tt = self._timetable()
        if tt is None:
            await ctx.send("The timetable cog is not loaded.", ephemeral=True)
            return

        all_reps = tt.read_representatives()
        if not all_reps:
            embed = discord.Embed(
                title="Class Representatives",
                description="No Class Representatives are currently authorized.\nAdministrators can add representatives using `/setcr`.",
                color=COLOR_MUTED,
            )
            embed.set_author(name="Timetable Administration")
            await ctx.send(embed=embed, ephemeral=True)
            return

        target_batch = tt.resolve_batch_input(batch) if batch else None

        grouped: dict[str, list] = {}
        for rep in all_reps.values():
            for b in rep.batches:
                if target_batch and target_batch != "ALL" and b != target_batch:
                    continue
                grouped.setdefault(b, []).append(rep)

        if not grouped:
            embed = discord.Embed(
                title="Class Representatives",
                description=f"No Class Representatives found for **Batch {target_batch}**.",
                color=COLOR_MUTED,
            )
            embed.set_author(name="Timetable Administration")
            await ctx.send(embed=embed, ephemeral=True)
            return

        total = sum(len(lst) for lst in grouped.values())
        b_info = f" for Batch {target_batch}" if target_batch and target_batch != "ALL" else ""
        embed = discord.Embed(
            title="Class Representatives Directory",
            description=f"> **{total} representative{'s' if total != 1 else ''} authorized**{b_info}\nClass Representatives manage timetable modifications and student rosters for their section.",
            color=COLOR_PRIMARY,
        )
        embed.set_author(name="Timetable Administration")

        lines = []
        for b_code in sorted(grouped.keys()):
            reps_in_b = grouped[b_code]
            rep_str = ", ".join(f"<@{r.user_id}> ({r.name})" if r.name else f"<@{r.user_id}>" for r in reps_in_b)
            embed.add_field(name=f"Batch {b_code}", value=f"• {rep_str}", inline=False)
            lines.append(f"**Batch {b_code}:** {rep_str}")

        embed.set_footer(text=f"Total: {total} representative(s) • Saved in data/representatives.json")

        c_v2 = {
            "type": 17,
            "spoiler": False,
            "accent_color": COLOR_PRIMARY,
            "components": [
                {
                    "type": 10,
                    "content": f"## Class Representatives Directory\n**{total} representative{'s' if total != 1 else ''} authorized**{b_info}",
                },
                {"type": 14, "divider": True, "spacing": 1},
                {"type": 10, "content": "\n".join(lines)[:3900]},
                {"type": 14, "divider": True, "spacing": 1},
                {"type": 10, "content": "-# Use /setcr or /delcr to manage representative authorizations"},
            ],
        }
        await send_component_v2(ctx, c_v2, fallback_embed=embed)

    # ========================================================================= #
    # Timetable Slot & Schedule Editing (Role-Scoped)
    # ========================================================================= #
    @commands.hybrid_command(
        name="setclass",
        aliases=["addclass"],
        description="Add or replace a slot in the timetable. Admins and Class Representatives.",
    )
    @is_admin_or_cr()
    @app_commands.describe(
        day="Day of the week (or date)",
        time_slot="e.g. 09:30-10:30 (24-hour)",
        subject='e.g. "Applied Physics (Lecture)" — or FREE / LUNCH for a break',
        batch='Batch code (e.g. 26CAIBTAIB16). Default: CR batch, or ALL for Admin',
        room="Room, e.g. 8105-BL8-FF (optional for free slots)",
        faculty="Faculty name (optional for free slots)",
    )
    @app_commands.choices(day=DAY_CHOICES)
    async def setclass(
        self,
        ctx: commands.Context,
        day: str,
        time_slot: str,
        subject: str,
        batch: str = "",
        room: str = "",
        faculty: str = "",
    ) -> None:
        tt = self._timetable()
        if tt is None:
            await ctx.send("The `cogs.timetable` module is not loaded.", ephemeral=True)
            return

        is_admin = await self.bot.is_owner(ctx.author)
        cr_batches = tt.get_representative_batches(ctx.author.id) if not is_admin else []

        if not is_admin:
            if not cr_batches:
                await ctx.send(
                    embed=discord.Embed(
                        title="🔒 Access Denied",
                        description="You do not have permission to execute this command. Only Administrators and Class Representatives can edit timetables.",
                        color=COLOR_ERROR,
                    ),
                    ephemeral=True,
                )
                return
            if not batch or batch.strip().upper() in ("ALL", "*", "ANY"):
                if batch and batch.strip().upper() in ("ALL", "*", "ANY"):
                    await ctx.send(
                        embed=discord.Embed(
                            title="🔒 Global Schedule Restricted",
                            description=(
                                f"As Class Representative for **Batch {', '.join(cr_batches)}**, you can only modify schedules for your assigned class.\n"
                                "Editing the global fallback schedule (`ALL`) is restricted to Global Administrators."
                            ),
                            color=COLOR_ERROR,
                        ),
                        ephemeral=True,
                    )
                    return
                actual_batch = cr_batches[0]
            else:
                resolved = tt.resolve_batch_input(batch)
                if resolved not in cr_batches and "ALL" not in cr_batches:
                    await ctx.send(
                        embed=discord.Embed(
                            title="🔒 Batch Scope Denied",
                            description=(
                                f"You are authorized as Class Representative for **Batch {', '.join(cr_batches)}**, not **Batch {resolved}**.\n"
                                "You can only edit timetable slots for your own assigned class."
                            ),
                            color=COLOR_ERROR,
                        ),
                        ephemeral=True,
                    )
                    return
                actual_batch = resolved
        else:
            actual_batch = tt.resolve_batch_input(batch) if batch else "ALL"

        role_str = "Global Administrator" if is_admin else f"Class Representative ({ctx.author.display_name})"

        # Check if day is an explicit calendar date / today / tomorrow for single-date override
        now = tt._now()
        target_date = parse_date_input(day, now)
        is_explicit_date = (
            day.lower() in ("today", "tomorrow", "tmr", "tmrw", "yesterday")
            or any(sep in day for sep in ("-", "/", "."))
        )
        if target_date is not None and is_explicit_date:
            action, slot = await tt.set_override(
                target_date=target_date,
                time_slot=time_slot,
                subject=subject,
                batch=actual_batch,
                room=room,
                faculty=faculty,
            )
            batch_info = f"Batch {slot.batch_str}" if not slot.is_all_batches else "All Batches"
            kind_str = f" `[{slot.kind}]`" if getattr(slot, "kind", "") else ""
            details_lines = [
                f"**Date:** `{target_date:%A, %d %B %Y}`  •  **Time:** `{slot.span}`",
                f"**Batch:** **{batch_info}**",
            ]
            if slot.is_free:
                details_lines.append(f"**Status:** *{slot.title}*")
            else:
                details_lines.append(f"**Subject:** **{slot.subject}**{kind_str}")
                room_fac = []
                if getattr(slot, "room", ""):
                    room_fac.append(f"**Room:** `{slot.room}`")
                if getattr(slot, "faculty", ""):
                    room_fac.append(f"**Faculty:** **{slot.faculty}**")
                if room_fac:
                    details_lines.append("  •  ".join(room_fac))

            embed = discord.Embed(
                title=f"Single-Date Override {action.capitalize()}",
                description=f"Class schedule for **{target_date:%A, %d %B %Y}** has been updated for `{slot.span}`.",
                color=COLOR_SUCCESS,
            )
            embed.set_footer(text=f"Stored in data/overrides.json • Updated by {role_str}")
            c_v2 = {
                "type": 17,
                "spoiler": False,
                "accent_color": COLOR_SUCCESS,
                "components": [
                    {
                        "type": 10,
                        "content": f"## Single-Date Override {action.capitalize()}\nThe schedule for **{target_date:%A, %d %B %Y}** has been updated for `{slot.span}`.",
                    },
                    {"type": 14, "divider": True, "spacing": 1},
                    {"type": 10, "content": "\n".join(details_lines)},
                    {"type": 14, "divider": True, "spacing": 1},
                    {"type": 10, "content": f"-# Stored in data/overrides.json • Updated by {role_str} • Affects only this date"},
                ],
            }
            await send_component_v2(ctx, c_v2, fallback_embed=embed)
            return

        action, slot = await tt.set_slot(day, time_slot, subject, actual_batch, room, faculty)
        key = resolve_day(day) or day
        day_display = DAY_NAMES.get(key, day.title())

        batch_target = f"data/timetables/converted/{actual_batch.upper()}.json" if actual_batch != "ALL" else "data/schedule.json"
        embed = discord.Embed(
            title=f"Class Slot {action.capitalize()}",
            description=f"The schedule for **{day_display}** has been updated.",
            color=COLOR_SUCCESS,
        )
        embed.set_author(name="Timetable Administration")
        embed.add_field(name="Day", value=f"**{day_display}**", inline=True)
        embed.add_field(name="Time", value=f"`{slot.span}`", inline=True)
        embed.add_field(
            name="Batch",
            value=f"**Batch {slot.batch_str}**" if not slot.is_all_batches else "*All Batches*",
            inline=True,
        )
        if slot.is_free:
            embed.add_field(name="Type", value=f"*{slot.title}*", inline=True)
        else:
            kind_str = f" `[{slot.kind}]`" if slot.kind else ""
            embed.add_field(name="Subject", value=f"**{slot.subject}**{kind_str}", inline=True)
            if slot.room:
                embed.add_field(name="Room", value=f"`{slot.room}`", inline=True)
            if slot.faculty:
                embed.add_field(name="Faculty", value=f"**{slot.faculty}**", inline=True)

        embed.set_footer(text=f"Saved to {batch_target} • Updated by {role_str} • Live immediately")

        batch_info = f"Batch {slot.batch_str}" if not slot.is_all_batches else "All Batches"
        kind_str = f" `[{slot.kind}]`" if getattr(slot, "kind", "") else ""
        details_lines = [
            f"**Day:** {day_display}  •  **Time:** `{slot.span}`",
            f"**Batch:** **{batch_info}**",
        ]
        if slot.is_free:
            details_lines.append(f"**Type:** *{slot.title}*")
        else:
            details_lines.append(f"**Subject:** **{slot.subject}**{kind_str}")
            room_fac = []
            if getattr(slot, "room", ""):
                room_fac.append(f"**Room:** `{slot.room}`")
            if getattr(slot, "faculty", ""):
                room_fac.append(f"**Faculty:** **{slot.faculty}**")
            if room_fac:
                details_lines.append("  •  ".join(room_fac))

        c_v2 = {
            "type": 17,
            "spoiler": False,
            "accent_color": COLOR_SUCCESS,
            "components": [
                {
                    "type": 10,
                    "content": f"## Class Slot {action.capitalize()}\nThe schedule for **{day_display}** has been updated successfully.",
                },
                {"type": 14, "divider": True, "spacing": 1},
                {"type": 10, "content": "\n".join(details_lines)},
                {"type": 14, "divider": True, "spacing": 1},
                {"type": 10, "content": f"-# Saved to {batch_target} • Updated by {role_str} • Live immediately"},
            ],
        }
        await send_component_v2(ctx, c_v2, fallback_embed=embed)

    @commands.hybrid_command(
        name="delclass",
        aliases=["removeclass", "rmclass"],
        description="Remove a slot by start time and optional batch. Admins and Class Representatives.",
    )
    @is_admin_or_cr()
    @app_commands.describe(
        day="Day of the week",
        start="Start time of the slot, e.g. 09:30",
        batch="Batch filter (e.g. 26CAIBTAIB16). Default: CR batch, or ALL for Admin",
    )
    @app_commands.choices(day=DAY_CHOICES)
    async def delclass(
        self, ctx: commands.Context, day: str, start: str, batch: Optional[str] = None
    ) -> None:
        tt = self._timetable()
        if tt is None:
            await ctx.send("The timetable cog isn't loaded.", ephemeral=True)
            return

        is_admin = await self.bot.is_owner(ctx.author)
        cr_batches = tt.get_representative_batches(ctx.author.id) if not is_admin else []

        if not is_admin:
            if not cr_batches:
                await ctx.send(
                    embed=discord.Embed(
                        title="🔒 Access Denied",
                        description="You do not have permission to execute this command. Only Administrators and Class Representatives can remove class slots.",
                        color=COLOR_ERROR,
                    ),
                    ephemeral=True,
                )
                return
            if not batch:
                actual_batch = cr_batches[0]
            else:
                resolved = tt.resolve_batch_input(batch)
                if resolved.upper() in ("ALL", "*", "ANY"):
                    await ctx.send(
                        embed=discord.Embed(
                            title="🔒 Global Schedule Restricted",
                            description="Class Representatives cannot delete slots from the global fallback schedule (`ALL`).",
                            color=COLOR_ERROR,
                        ),
                        ephemeral=True,
                    )
                    return
                if resolved not in cr_batches and "ALL" not in cr_batches:
                    await ctx.send(
                        embed=discord.Embed(
                            title="🔒 Batch Scope Denied",
                            description=f"You can only remove class slots from your assigned batch (**{', '.join(cr_batches)}**).",
                            color=COLOR_ERROR,
                        ),
                        ephemeral=True,
                    )
                    return
                actual_batch = resolved
        else:
            actual_batch = tt.resolve_batch_input(batch) if batch else None

        slot = await tt.delete_slot(day, start, actual_batch)
        key = resolve_day(day) or day
        day_display = DAY_NAMES.get(key, day.title())

        embed = discord.Embed(
            title="Class Slot Removed",
            description=f"Slot removed from **{day_display}**'s timetable.",
            color=COLOR_WARN,
        )
        embed.set_author(name="Timetable Administration")
        embed.add_field(name="Day", value=f"**{day_display}**", inline=True)
        embed.add_field(name="Time", value=f"`{slot.span}`", inline=True)
        embed.add_field(name="Subject", value=f"**{slot.title}**", inline=True)
        if not slot.is_all_batches:
            embed.add_field(name="Batch", value=f"**Batch {slot.batch_str}**", inline=True)
        embed.set_footer(text="Updated timetable • Live immediately")

        del_lines = [
            f"**Day:** {day_display}  •  **Time:** `{slot.span}`",
            f"**Subject:** **{slot.title}**",
        ]
        if not slot.is_all_batches:
            del_lines.append(f"**Batch:** **Batch {slot.batch_str}**")

        del_v2 = {
            "type": 17,
            "spoiler": False,
            "accent_color": COLOR_WARN,
            "components": [
                {
                    "type": 10,
                    "content": f"## Class Slot Removed\nSlot removed from **{day_display}**'s timetable.",
                },
                {"type": 14, "divider": True, "spacing": 1},
                {"type": 10, "content": "\n".join(del_lines)},
                {"type": 14, "divider": True, "spacing": 1},
                {"type": 10, "content": "-# Timetable file updated • Live immediately"},
            ],
        }
        await send_component_v2(ctx, del_v2, fallback_embed=embed)

    @commands.hybrid_command(
        name="reschedule",
        aliases=["override", "setoverride"],
        description="Reschedule or substitute a class for a specific date and hour. Admins and Class Representatives.",
    )
    @is_admin_or_cr()
    @app_commands.describe(
        date="Date (e.g. today, tomorrow, 2026-11-06, 06-11-2026)",
        time_slot="Time slot, e.g. 10:30-11:30 or 10:30",
        subject="New subject name, or FREE / CANCELLED",
        batch="Batch code (e.g. 26CAIBTAIB16). Default: CR batch, or ALL for Admin",
        room="Room code (optional)",
        faculty="Faculty name (optional)",
        note="Note or reason (optional)",
    )
    async def reschedule(
        self,
        ctx: commands.Context,
        date: str,
        time_slot: str,
        subject: str,
        batch: str = "",
        room: str = "",
        faculty: str = "",
        note: str = "",
    ) -> None:
        tt = self._timetable()
        if tt is None:
            await ctx.send("The timetable cog isn't loaded.", ephemeral=True)
            return

        is_admin = await self.bot.is_owner(ctx.author)
        cr_batches = tt.get_representative_batches(ctx.author.id) if not is_admin else []

        if not is_admin:
            if not cr_batches:
                await ctx.send(
                    embed=discord.Embed(
                        title="🔒 Access Denied",
                        description="You do not have permission to execute this command. Only Administrators and Class Representatives can reschedule classes.",
                        color=COLOR_ERROR,
                    ),
                    ephemeral=True,
                )
                return
            if not batch or batch.strip().upper() in ("ALL", "*", "ANY"):
                if batch and batch.strip().upper() in ("ALL", "*", "ANY"):
                    await ctx.send(
                        embed=discord.Embed(
                            title="🔒 Global Schedule Restricted",
                            description="Class Representatives cannot reschedule global classes (`ALL`). Specify your assigned batch.",
                            color=COLOR_ERROR,
                        ),
                        ephemeral=True,
                    )
                    return
                actual_batch = cr_batches[0]
            else:
                resolved = tt.resolve_batch_input(batch)
                if resolved not in cr_batches and "ALL" not in cr_batches:
                    await ctx.send(
                        embed=discord.Embed(
                            title="🔒 Batch Scope Denied",
                            description=f"You can only reschedule classes for your assigned batch (**{', '.join(cr_batches)}**).",
                            color=COLOR_ERROR,
                        ),
                        ephemeral=True,
                    )
                    return
                actual_batch = resolved
        else:
            actual_batch = tt.resolve_batch_input(batch) if batch else "ALL"

        target_date = parse_date_input(date, tt._now())
        if target_date is None:
            await ctx.send(f"Could not parse date `{date}`. Try `today`, `tomorrow`, or `YYYY-MM-DD`.", ephemeral=True)
            return

        action, slot = await tt.set_override(
            target_date=target_date,
            time_slot=time_slot,
            subject=subject,
            batch=actual_batch,
            room=room,
            faculty=faculty,
            note=note,
            status="rescheduled",
        )
        batch_info = f"Batch {slot.batch_str}" if not slot.is_all_batches else "All Batches"
        kind_str = f" `[{slot.kind}]`" if getattr(slot, "kind", "") else ""
        details_lines = [
            f"**Date:** `{target_date:%A, %d %B %Y}`  •  **Time:** `{slot.span}`",
            f"**Batch:** **{batch_info}**",
        ]
        if slot.is_free:
            details_lines.append(f"**Status:** *{slot.title}*")
        else:
            details_lines.append(f"**Subject:** **{slot.subject}**{kind_str}")
            room_fac = []
            if getattr(slot, "room", ""):
                room_fac.append(f"**Room:** `{slot.room}`")
            if getattr(slot, "faculty", ""):
                room_fac.append(f"**Faculty:** **{slot.faculty}**")
            if room_fac:
                details_lines.append("  •  ".join(room_fac))
        if note:
            details_lines.append(f"**Note:** *{note}*")

        c_v2 = {
            "type": 17,
            "spoiler": False,
            "accent_color": COLOR_SUCCESS if not slot.is_free else COLOR_WARN,
            "components": [
                {
                    "type": 10,
                    "content": f"## Class Rescheduled for {target_date:%d %b %Y}\nSingle-date timetable override active for `{slot.span}`.",
                },
                {"type": 14, "divider": True, "spacing": 1},
                {"type": 10, "content": "\n".join(details_lines)},
                {"type": 14, "divider": True, "spacing": 1},
                {"type": 10, "content": "-# Stored in data/overrides.json • Affects only this date"},
            ],
        }
        embed = discord.Embed(
            title="Class Rescheduled",
            description=f"Class on **{target_date:%A, %d %B %Y}** at `{slot.span}` has been rescheduled.",
            color=COLOR_SUCCESS,
        )
        await send_component_v2(ctx, c_v2, fallback_embed=embed)

    @commands.hybrid_command(
        name="cancelclass",
        description="Cancel a class slot for a specific date and hour. Admins and Class Representatives.",
    )
    @is_admin_or_cr()
    @app_commands.describe(
        date="Date of cancellation (e.g. today, tomorrow, 2026-11-06)",
        time_slot="Start time or span, e.g. 09:30 or 09:30-10:30",
        batch="Batch filter (e.g. 26CAIBTAIB16). Default: CR batch, or ALL for Admin",
        reason="Reason for cancellation (optional)",
    )
    async def cancelclass(
        self,
        ctx: commands.Context,
        date: str,
        time_slot: str,
        batch: str = "",
        reason: str = "Class cancelled",
    ) -> None:
        tt = self._timetable()
        if tt is None:
            await ctx.send("The timetable cog isn't loaded.", ephemeral=True)
            return

        is_admin = await self.bot.is_owner(ctx.author)
        cr_batches = tt.get_representative_batches(ctx.author.id) if not is_admin else []

        if not is_admin:
            if not cr_batches:
                await ctx.send(
                    embed=discord.Embed(
                        title="🔒 Access Denied",
                        description="You do not have permission to execute this command. Only Administrators and Class Representatives can cancel classes.",
                        color=COLOR_ERROR,
                    ),
                    ephemeral=True,
                )
                return
            if not batch or batch.strip().upper() in ("ALL", "*", "ANY"):
                if batch and batch.strip().upper() in ("ALL", "*", "ANY"):
                    await ctx.send(
                        embed=discord.Embed(
                            title="🔒 Global Schedule Restricted",
                            description="Class Representatives cannot cancel classes for all batches (`ALL`).",
                            color=COLOR_ERROR,
                        ),
                        ephemeral=True,
                    )
                    return
                actual_batch = cr_batches[0]
            else:
                resolved = tt.resolve_batch_input(batch)
                if resolved not in cr_batches and "ALL" not in cr_batches:
                    await ctx.send(
                        embed=discord.Embed(
                            title="🔒 Batch Scope Denied",
                            description=f"You can only cancel classes for your assigned batch (**{', '.join(cr_batches)}**).",
                            color=COLOR_ERROR,
                        ),
                        ephemeral=True,
                    )
                    return
                actual_batch = resolved
        else:
            actual_batch = tt.resolve_batch_input(batch) if batch else "ALL"

        target_date = parse_date_input(date, tt._now())
        if target_date is None:
            await ctx.send(f"Could not parse date `{date}`. Try `today`, `tomorrow`, or `YYYY-MM-DD`.", ephemeral=True)
            return

        action, slot = await tt.set_override(
            target_date=target_date,
            time_slot=time_slot,
            subject="CANCELLED",
            batch=actual_batch,
            note=reason,
            status="cancelled",
        )
        batch_info = f"Batch {slot.batch_str}" if not slot.is_all_batches else "All Batches"
        details_lines = [
            f"**Date:** `{target_date:%A, %d %B %Y}`  •  **Time:** `{slot.span}`",
            f"**Batch:** **{batch_info}**",
            f"**Status:** **CANCELLED**",
        ]
        if reason:
            details_lines.append(f"**Reason:** *{reason}*")

        embed = discord.Embed(
            title="Class Cancelled",
            description=f"Class on **{target_date:%d %B %Y}** at `{slot.span}` has been cancelled.",
            color=COLOR_WARN,
        )
        c_v2 = {
            "type": 17,
            "spoiler": False,
            "accent_color": COLOR_WARN,
            "components": [
                {
                    "type": 10,
                    "content": f"## Class Slot Cancelled\nClass on **{target_date:%A, %d %B %Y}** at `{slot.span}` is marked cancelled.",
                },
                {"type": 14, "divider": True, "spacing": 1},
                {"type": 10, "content": "\n".join(details_lines)},
                {"type": 14, "divider": True, "spacing": 1},
                {"type": 10, "content": "-# Stored in data/overrides.json • Affects only this date"},
            ],
        }
        await send_component_v2(ctx, c_v2, fallback_embed=embed)

    @commands.hybrid_command(
        name="deloverride",
        description="Remove a date-specific override and restore original schedule. Admins and Class Representatives.",
    )
    @is_admin_or_cr()
    @app_commands.describe(
        date="Date of override (e.g. today, tomorrow, YYYY-MM-DD)",
        start="Start time, e.g. 09:30",
        batch="Batch code (e.g. 26CAIBTAIB16). Default: CR batch, or ALL for Admin",
    )
    async def deloverride(
        self, ctx: commands.Context, date: str, start: str, batch: Optional[str] = None
    ) -> None:
        tt = self._timetable()
        if tt is None:
            await ctx.send("The timetable cog isn't loaded.", ephemeral=True)
            return

        is_admin = await self.bot.is_owner(ctx.author)
        cr_batches = tt.get_representative_batches(ctx.author.id) if not is_admin else []

        if not is_admin:
            if not cr_batches:
                await ctx.send(
                    embed=discord.Embed(
                        title="🔒 Access Denied",
                        description="You do not have permission to execute this command. Only Administrators and Class Representatives can remove overrides.",
                        color=COLOR_ERROR,
                    ),
                    ephemeral=True,
                )
                return
            if not batch:
                actual_batch = cr_batches[0]
            else:
                resolved = tt.resolve_batch_input(batch)
                if resolved not in cr_batches and "ALL" not in cr_batches:
                    await ctx.send(
                        embed=discord.Embed(
                            title="🔒 Batch Scope Denied",
                            description=f"You can only remove overrides for your assigned batch (**{', '.join(cr_batches)}**).",
                            color=COLOR_ERROR,
                        ),
                        ephemeral=True,
                    )
                    return
                actual_batch = resolved
        else:
            actual_batch = tt.resolve_batch_input(batch) if batch else None

        target_date = parse_date_input(date, tt._now())
        if target_date is None:
            await ctx.send(f"Could not parse date `{date}`.", ephemeral=True)
            return

        removed = await tt.delete_override(target_date, start, actual_batch)
        if not removed:
            await ctx.send(f"No override found on **{target_date:%d %B %Y}** at `{start}`.", ephemeral=True)
            return

        details = f"**Date:** `{target_date:%A, %d %B %Y}` • **Time:** `{removed.span}`\n**Subject:** **{removed.title}**"
        c_v2 = {
            "type": 17,
            "spoiler": False,
            "accent_color": COLOR_WARN,
            "components": [
                {
                    "type": 10,
                    "content": f"## Override Removed\nOriginal timetable schedule restored for **{target_date:%A, %d %B %Y}**.",
                },
                {"type": 14, "divider": True, "spacing": 1},
                {"type": 10, "content": details},
                {"type": 14, "divider": True, "spacing": 1},
                {"type": 10, "content": "-# Removed from data/overrides.json"},
            ],
        }
        embed = discord.Embed(
            title="Override Removed",
            description=f"Original timetable restored for **{target_date:%d %B %Y}** at `{removed.span}`.",
            color=COLOR_WARN,
        )
        await send_component_v2(ctx, c_v2, fallback_embed=embed)

    @commands.hybrid_command(
        name="overrides",
        description="View all active date-specific timetable overrides.",
    )
    async def view_overrides(self, ctx: commands.Context) -> None:
        tt = self._timetable()
        if tt is None:
            await ctx.send("The timetable cog isn't loaded.", ephemeral=True)
            return
        all_ovs = tt.read_overrides()
        if not all_ovs:
            await ctx.send("No date-specific timetable overrides are currently scheduled.")
            return

        lines = []
        for d_str, raw_list in sorted(all_ovs.items()):
            lines.append(f"### {d_str}")
            for r in raw_list:
                span = f"`{r.get('start', '')} - {r.get('end', '')}`"
                b_str = ", ".join(r.get("batches", ["ALL"]))
                if r.get("category") == "free" or r.get("status") == "cancelled":
                    lbl = r.get("label", "Free / Cancelled")
                    lines.append(f"- {span} · **{lbl}** · `Batch {b_str}`")
                else:
                    sub = r.get("subject", "Class")
                    rm = f" ({r.get('room')})" if r.get("room") else ""
                    lines.append(f"- {span} · **{sub}**{rm} · `Batch {b_str}`")
                if r.get("note"):
                    lines.append(f"  > *{r.get('note')}*")

        c_v2 = {
            "type": 17,
            "spoiler": False,
            "accent_color": COLOR_PRIMARY,
            "components": [
                {"type": 10, "content": "## Active Date-Specific Overrides\nHere are all custom date modifications on file:"},
                {"type": 14, "divider": True, "spacing": 1},
                {"type": 10, "content": "\n".join(lines)},
                {"type": 14, "divider": True, "spacing": 1},
                {"type": 10, "content": "-# Use /reschedule to add overrides or /deloverride to remove them"},
            ],
        }
        embed = discord.Embed(
            title="Active Date Overrides",
            description="\n".join(lines),
            color=COLOR_PRIMARY,
        )
        await send_component_v2(ctx, c_v2, fallback_embed=embed)

    # ========================================================================= #
    # Server Prefix, Slash Sync, & Hot Reload (Admins Only)
    # ========================================================================= #
    @commands.hybrid_command(
        name="reload", description="Reload a cog (or all cogs). Administrator only."
    )
    @is_admin()
    @app_commands.describe(cog_path="e.g. cogs.timetable — leave empty to reload everything")
    async def reload(self, ctx: commands.Context, cog_path: Optional[str] = None) -> None:
        if cog_path:
            targets = [_normalize_ext(cog_path)]
        else:
            targets = sorted(self.bot.extensions, key=lambda e: (e == "cogs.admin", e))

        results = [await self._reload_one(ext) for ext in targets]
        ok = all(success for success, _ in results)
        embed = discord.Embed(
            title="Reload complete" if ok else "Reload finished with errors",
            description="\n".join(line for _, line in results)[:4000] or "Nothing to reload.",
            color=COLOR_SUCCESS if ok else COLOR_WARN,
        )
        embed.set_footer(text="Changed slash-command options? Run the sync command too.")
        await ctx.send(embed=embed, ephemeral=True)

    @commands.hybrid_command(
        name="setprefix", description="Change the bot's command prefix. Administrator only."
    )
    @is_admin()
    @app_commands.describe(prefix="New prefix (1–5 characters, no spaces)")
    async def setprefix(self, ctx: commands.Context, prefix: str) -> None:
        try:
            await self.bot.change_prefix(prefix)  # type: ignore[attr-defined]
        except ValueError as exc:
            await ctx.send(
                embed=discord.Embed(title="Invalid Prefix", description=str(exc), color=COLOR_ERROR),
                ephemeral=True,
            )
            return
        embed = discord.Embed(
            title="Prefix Updated",
            description=f"Bot command prefix is now set to `{prefix}`",
            color=COLOR_SUCCESS,
        )
        embed.set_author(name="Timetable Administration")
        embed.add_field(
            name="Try It Out",
            value=f"`{prefix}timetable` — Today's timetable\n`{prefix}next` — What's on right now\n`{prefix}week` — Full weekly schedule",
            inline=False,
        )
        embed.set_footer(text="Saved to data/config.json")
        await ctx.send(embed=embed)

    @commands.hybrid_command(
        name="sync", description="Sync slash commands with Discord. Administrator only."
    )
    @is_admin()
    async def sync(self, ctx: commands.Context) -> None:
        await ctx.defer(ephemeral=True)
        try:
            synced = await self.bot.tree.sync()
        except discord.HTTPException as exc:
            log.exception("Slash sync failed")
            await ctx.send(
                embed=discord.Embed(
                    title="Slash Sync Failed",
                    description=f"`HTTP {exc.status}`: {exc.text[:300]}",
                    color=COLOR_ERROR,
                ),
                ephemeral=True,
            )
            return
        embed = discord.Embed(
            title="Slash Commands Synced",
            description=f"Successfully registered **{len(synced)}** slash command(s) globally with Discord.",
            color=COLOR_PRIMARY,
        )
        embed.set_author(name="Timetable Administration")
        embed.set_footer(text="Discord may take up to a minute to refresh commands in your client.")
        await ctx.send(embed=embed, ephemeral=True)

    @commands.hybrid_command(
        name="loadbatch",
        aliases=["activatebatch", "importbatch"],
        description="Load an SRUX batch schedule into data/schedule.json. Administrator only.",
    )
    @is_admin()
    @app_commands.describe(batch="Batch code, e.g. 26CAIBTAIB01")
    async def loadbatch(self, ctx: commands.Context, batch: str) -> None:
        tt = self._timetable()
        if tt is None:
            await ctx.send("The timetable cog isn't loaded.", ephemeral=True)
            return

        b_code = tt.resolve_batch_input(batch)
        data_dir = Path(__file__).resolve().parent.parent / "data"
        conv_file = data_dir / "timetables" / "converted" / f"{b_code}.json"
        sched_file = data_dir / "schedule.json"

        if not conv_file.is_file():
            await ctx.send(
                embed=discord.Embed(
                    title="Batch Not Found",
                    description=f"Could not find converted timetable for `{b_code}`.\nMake sure the batch code exists in SRUX.",
                    color=COLOR_ERROR,
                ),
                ephemeral=True,
            )
            return

        try:
            import shutil
            shutil.copyfile(conv_file, sched_file)
        except Exception as exc:
            await ctx.send(
                embed=discord.Embed(
                    title="Copy Failed",
                    description=f"Error copying batch schedule: {exc}",
                    color=COLOR_ERROR,
                ),
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title="Batch Schedule Loaded",
            description=f"Primary master timetable updated to **Batch {b_code}**.",
            color=COLOR_SUCCESS,
        )
        embed.set_author(name="Timetable Administration")
        embed.add_field(name="Active Batch", value=f"`{b_code}`", inline=True)
        embed.set_footer(text="Saved to data/schedule.json • Live immediately")
        await ctx.send(embed=embed)

    # ========================================================================= #
    # Student Roster & Account Linking (Admins & Class Representatives)
    # ========================================================================= #
    @commands.hybrid_command(
        name="linkuser",
        description="Link another server member to a batch. Admins and Class Representatives.",
    )
    @is_admin_or_cr()
    @app_commands.describe(
        member="The server member to link",
        batch="Batch number or name (e.g. 26CAIBTAIB16 or 1)",
        name="Optional student full name",
        roll_no="Optional student roll number / ID",
    )
    async def linkuser(
        self,
        ctx: commands.Context,
        member: discord.Member,
        batch: str,
        name: str = "",
        roll_no: str = "",
    ) -> None:
        tt = self._timetable()
        if tt is None:
            await ctx.send("The timetable cog is not loaded.", ephemeral=True)
            return

        is_admin = await self.bot.is_owner(ctx.author)
        cr_batches = tt.get_representative_batches(ctx.author.id) if not is_admin else []

        clean_batches = parse_batches(batch)
        clean_batch = clean_batches[0] if clean_batches else batch.strip().upper()

        if not is_admin:
            if not cr_batches:
                await ctx.send(
                    embed=discord.Embed(
                        title="🔒 Access Denied",
                        description="You do not have permission to execute this command. Only Administrators and Class Representatives can link student accounts.",
                        color=COLOR_ERROR,
                    ),
                    ephemeral=True,
                )
                return
            if clean_batch not in cr_batches and "ALL" not in cr_batches:
                await ctx.send(
                    embed=discord.Embed(
                        title="🔒 Batch Scope Denied",
                        description=f"Class Representatives can only link students to their assigned batch (**{', '.join(cr_batches)}**).",
                        color=COLOR_ERROR,
                    ),
                    ephemeral=True,
                )
                return

        prof = tt.set_user_profile(
            user_id=member.id,
            batch=clean_batch,
            name=name.strip() or getattr(member, "display_name", ""),
            roll_no=roll_no.strip(),
        )

        embed = discord.Embed(
            title="Student Account Linked",
            description=f"Successfully linked {member.mention} to **Batch {prof.batch}**.",
            color=COLOR_SUCCESS,
        )
        embed.set_author(name="Timetable Administration")
        embed.add_field(name="Student", value=member.mention, inline=True)
        embed.add_field(name="Batch", value=f"**Batch {prof.batch}**", inline=True)
        if prof.name:
            embed.add_field(name="Name", value=prof.name, inline=True)
        if prof.roll_no:
            embed.add_field(name="Roll / ID", value=f"`{prof.roll_no}`", inline=True)
        embed.set_footer(text="Saved to data/users.json • User's /timetable commands will now reflect their batch")
        await ctx.send(embed=embed, ephemeral=True)

    @commands.hybrid_command(
        name="unlinkuser",
        description="Unlink a member's batch profile. Admins and Class Representatives.",
    )
    @is_admin_or_cr()
    @app_commands.describe(member="The server member to unlink")
    async def unlinkuser(self, ctx: commands.Context, member: discord.Member) -> None:
        tt = self._timetable()
        if tt is None:
            await ctx.send("The timetable cog is not loaded.", ephemeral=True)
            return

        is_admin = await self.bot.is_owner(ctx.author)
        cr_batches = tt.get_representative_batches(ctx.author.id) if not is_admin else []

        if not is_admin:
            if not cr_batches:
                await ctx.send(
                    embed=discord.Embed(
                        title="🔒 Access Denied",
                        description="You do not have permission to execute this command. Only Administrators and Class Representatives can unlink students.",
                        color=COLOR_ERROR,
                    ),
                    ephemeral=True,
                )
                return
            existing = tt.get_user_profile(member.id)
            if existing and existing.batch not in cr_batches and "ALL" not in cr_batches:
                await ctx.send(
                    embed=discord.Embed(
                        title="🔒 Batch Scope Denied",
                        description=f"You can only unlink students in your assigned batch (**{', '.join(cr_batches)}**).",
                        color=COLOR_ERROR,
                    ),
                    ephemeral=True,
                )
                return

        removed = tt.delete_user_profile(member.id)
        if removed:
            embed = discord.Embed(
                title="Student Account Unlinked",
                description=f"Unlinked {member.mention} from their batch.",
                color=COLOR_WARN,
            )
        else:
            embed = discord.Embed(
                title="ℹ️ Student Not Linked",
                description=f"{member.mention} was not linked to any batch.",
                color=COLOR_PRIMARY,
            )
        embed.set_author(name="Timetable Administration")
        embed.set_footer(text="Updated data/users.json")
        await ctx.send(embed=embed, ephemeral=True)

    @commands.hybrid_command(
        name="students",
        aliases=["batchlist", "roster"],
        description="List linked students, optionally filtered by batch. Admins and Class Representatives.",
    )
    @is_admin_or_cr()
    @app_commands.describe(batch="Optional batch filter (e.g. 26CAIBTAIB16 or 1)")
    async def students(self, ctx: commands.Context, batch: Optional[str] = None) -> None:
        tt = self._timetable()
        if tt is None:
            await ctx.send("The timetable cog is not loaded.", ephemeral=True)
            return

        is_admin = await self.bot.is_owner(ctx.author)
        cr_batches = tt.get_representative_batches(ctx.author.id) if not is_admin else []

        if not is_admin:
            if not cr_batches:
                await ctx.send(
                    embed=discord.Embed(
                        title="🔒 Access Denied",
                        description="You do not have permission to execute this command. Only Administrators and Class Representatives can view the student roster.",
                        color=COLOR_ERROR,
                    ),
                    ephemeral=True,
                )
                return
            if not batch:
                target_batch = cr_batches[0]
            else:
                resolved = tt.resolve_batch_input(batch)
                if resolved not in cr_batches and "ALL" not in cr_batches:
                    await ctx.send(
                        embed=discord.Embed(
                            title="🔒 Batch Scope Denied",
                            description=f"You can only view the student roster for your assigned batch (**{', '.join(cr_batches)}**).",
                            color=COLOR_ERROR,
                        ),
                        ephemeral=True,
                    )
                    return
                target_batch = resolved
        else:
            target_batch = tt.resolve_batch_input(batch) if batch else None
            if target_batch == "ALL":
                target_batch = None

        profiles = tt.read_profiles()
        if not profiles:
            embed = discord.Embed(
                title="Registered Students",
                description="No students are currently linked to any batch.\nUsers can link their accounts using `/link <batch>`.",
                color=COLOR_MUTED,
            )
            embed.set_author(name="Timetable Administration")
            await ctx.send(embed=embed, ephemeral=True)
            return

        grouped: dict[str, list] = {}
        for prof in profiles.values():
            if target_batch and prof.batch != target_batch:
                continue
            grouped.setdefault(prof.batch, []).append(prof)

        if not grouped:
            embed = discord.Embed(
                title="Registered Students",
                description=f"No students found linked to **Batch {target_batch}**.",
                color=COLOR_MUTED,
            )
            embed.set_author(name="Timetable Administration")
            await ctx.send(embed=embed, ephemeral=True)
            return

        total_count = sum(len(lst) for lst in grouped.values())
        b_info = f" in Batch {target_batch}" if target_batch else ""
        embed = discord.Embed(
            title="Registered Students",
            description=f"> **{total_count} student{'s' if total_count != 1 else ''} linked**{b_info}",
            color=COLOR_PRIMARY,
        )
        embed.set_author(name="Timetable Administration")

        for b_name in sorted(grouped.keys()):
            s_list = grouped[b_name]
            lines = []
            for p in s_list:
                info_parts = []
                if p.name:
                    info_parts.append(p.name)
                if p.roll_no:
                    info_parts.append(f"Roll: {p.roll_no}")
                info_str = f" ({' • '.join(info_parts)})" if info_parts else ""
                lines.append(f"• <@{p.user_id}>{info_str}")

            field_content = "\n".join(lines)
            if len(field_content) > 1024:
                field_content = field_content[:1015] + "\n…"
            embed.add_field(
                name=f"Batch {b_name} ({len(s_list)} student{'s' if len(s_list) != 1 else ''})",
                value=field_content,
                inline=False,
            )

        embed.set_footer(text=f"Total: {total_count} linked accounts • Saved in data/users.json")
        await ctx.send(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AdminCog(bot))
