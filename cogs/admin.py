"""Admin cog: owner-only controls (reload cogs, edit the schedule, prefix, sync)."""
from __future__ import annotations

import logging
from typing import Optional

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

from cogs.timetable import DAY_NAMES, resolve_day, parse_batches

log = logging.getLogger("timetable_bot.admin")

COLOR_SUCCESS = 0x57F287
COLOR_WARN = 0xFEE75C
COLOR_ERROR = 0xED4245
COLOR_PRIMARY = 0x5865F2

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


class AdminCog(commands.Cog):
    """Owner-only management commands."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    def _timetable(self):
        return self.bot.get_cog("TimetableCog")

    async def _reload_one(self, ext: str) -> tuple[bool, str]:
        try:
            await self.bot.reload_extension(ext)
            return True, f"✅ `{ext}` reloaded"
        except commands.ExtensionNotLoaded:
            try:  # not loaded yet? load it instead
                await self.bot.load_extension(ext)
                return True, f"✅ `{ext}` loaded"
            except commands.ExtensionNotFound:
                return False, f"❌ `{ext}` — no such module"
            except commands.ExtensionError as exc:
                log.exception("Failed to load %s", ext)
                return False, f"❌ `{ext}` — {type(exc).__name__}: {getattr(exc, 'original', exc)}"
        except commands.ExtensionNotFound:
            return False, f"❌ `{ext}` — no such module"
        except commands.ExtensionError as exc:
            log.exception("Failed to reload %s", ext)
            return False, f"❌ `{ext}` — {type(exc).__name__}: {getattr(exc, 'original', exc)}"

    # ----- reload ---------------------------------------------------------- #
    @commands.hybrid_command(
        name="reload", description="Reload a cog (or all cogs). Owner only."
    )
    @commands.is_owner()
    @app_commands.describe(cog_path="e.g. cogs.timetable — leave empty to reload everything")
    async def reload(self, ctx: commands.Context, cog_path: Optional[str] = None) -> None:
        if cog_path:
            targets = [_normalize_ext(cog_path)]
        else:
            # reload this cog last so the reply path stays intact
            targets = sorted(self.bot.extensions, key=lambda e: (e == "cogs.admin", e))

        results = [await self._reload_one(ext) for ext in targets]
        ok = all(success for success, _ in results)
        embed = discord.Embed(
            title="🔄 Reload complete" if ok else "🔄 Reload finished with errors",
            description="\n".join(line for _, line in results)[:4000] or "Nothing to reload.",
            color=COLOR_SUCCESS if ok else COLOR_WARN,
        )
        embed.set_footer(text="Changed slash-command options? Run the sync command too.")
        await ctx.send(embed=embed, ephemeral=True)

    # ----- schedule editing ------------------------------------------------ #
    @commands.hybrid_command(
        name="setclass",
        description="Add or replace a slot in the timetable. Owner only.",
    )
    @commands.is_owner()
    @app_commands.describe(
        day="Day of the week",
        time_slot="e.g. 09:30-10:30 (24-hour)",
        subject='e.g. "Applied Physics (Lecture)" — or FREE / LUNCH for a break',
        batch='Batch number(s), e.g. "1" or "1, 2" or "ALL" (default: ALL)',
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
        batch: str = "ALL",
        room: str = "",
        faculty: str = "",
    ) -> None:
        tt = self._timetable()
        if tt is None:
            await ctx.send(
                embed=discord.Embed(
                    title="⚠️ Extension Error",
                    description="The `cogs.timetable` module is not currently loaded.\nRun `reload cogs.timetable` to load it.",
                    color=COLOR_ERROR,
                ),
                ephemeral=True,
            )
            return

        action, slot = await tt.set_slot(day, time_slot, subject, batch, room, faculty)
        key = resolve_day(day) or day
        day_display = DAY_NAMES.get(key, day.title())

        embed = discord.Embed(
            title=f"✅ Class Slot {action.capitalize()}",
            description=f"The schedule for **{day_display}** has been updated.",
            color=COLOR_SUCCESS,
        )
        embed.set_author(name="Timetable Admin")
        embed.add_field(name="📅 Day", value=f"**{day_display}**", inline=True)
        embed.add_field(name="⏰ Time", value=f"`{slot.span}`", inline=True)
        embed.add_field(
            name="👥 Batch",
            value=f"**Batch {slot.batch_str}**" if not slot.is_all_batches else "*All Batches*",
            inline=True,
        )
        if slot.is_free:
            embed.add_field(name="☕ Type", value=f"*{slot.title}*", inline=True)
        else:
            kind_str = f" `[{slot.kind}]`" if slot.kind else ""
            embed.add_field(name="📚 Subject", value=f"**{slot.subject}**{kind_str}", inline=True)
            if slot.room:
                embed.add_field(name="📍 Room", value=f"`{slot.room}`", inline=True)
            if slot.faculty:
                embed.add_field(name="👨‍🏫 Faculty", value=f"**{slot.faculty}**", inline=True)

        embed.set_footer(text="Saved to data/schedule.json • Live immediately")
        await ctx.send(embed=embed)

    @commands.hybrid_command(
        name="delclass", description="Remove a slot by start time and optional batch. Owner only."
    )
    @commands.is_owner()
    @app_commands.describe(
        day="Day of the week",
        start="Start time of the slot, e.g. 09:30",
        batch="Batch filter (e.g. 1) if multiple batches have a class at this time",
    )
    @app_commands.choices(day=DAY_CHOICES)
    async def delclass(
        self, ctx: commands.Context, day: str, start: str, batch: Optional[str] = None
    ) -> None:
        tt = self._timetable()
        if tt is None:
            await ctx.send("The timetable cog isn't loaded.", ephemeral=True)
            return
        slot = await tt.delete_slot(day, start, batch)
        key = resolve_day(day) or day
        day_display = DAY_NAMES.get(key, day.title())

        embed = discord.Embed(
            title="🗑️ Class Slot Removed",
            description=f"Slot removed from **{day_display}**'s timetable.",
            color=COLOR_WARN,
        )
        embed.set_author(name="Timetable Admin")
        embed.add_field(name="📅 Day", value=f"**{day_display}**", inline=True)
        embed.add_field(name="⏰ Time", value=f"`{slot.span}`", inline=True)
        embed.add_field(name="📚 Subject", value=f"**{slot.title}**", inline=True)
        if not slot.is_all_batches:
            embed.add_field(name="👥 Batch", value=f"**Batch {slot.batch_str}**", inline=True)
        embed.set_footer(text="Updated data/schedule.json")
        await ctx.send(embed=embed)

    # ----- prefix & slash sync --------------------------------------------- #
    @commands.hybrid_command(
        name="setprefix", description="Change the bot's command prefix. Owner only."
    )
    @commands.is_owner()
    @app_commands.describe(prefix="New prefix (1–5 characters, no spaces)")
    async def setprefix(self, ctx: commands.Context, prefix: str) -> None:
        try:
            await self.bot.change_prefix(prefix)  # type: ignore[attr-defined]
        except ValueError as exc:
            await ctx.send(
                embed=discord.Embed(title="⚠️ Invalid Prefix", description=str(exc), color=COLOR_ERROR),
                ephemeral=True,
            )
            return
        embed = discord.Embed(
            title="⚙️ Prefix Updated",
            description=f"Bot command prefix is now set to `{prefix}`",
            color=COLOR_SUCCESS,
        )
        embed.set_author(name="Timetable Admin")
        embed.add_field(
            name="💡 Try It Out",
            value=f"`{prefix}timetable` — Today's timetable\n`{prefix}next` — What's on right now\n`{prefix}week` — Full weekly schedule",
            inline=False,
        )
        embed.set_footer(text="Saved to data/config.json")
        await ctx.send(embed=embed)

    @commands.hybrid_command(
        name="sync", description="Sync slash commands with Discord. Owner only."
    )
    @commands.is_owner()
    async def sync(self, ctx: commands.Context) -> None:
        await ctx.defer(ephemeral=True)
        try:
            synced = await self.bot.tree.sync()
        except discord.HTTPException as exc:
            log.exception("Slash sync failed")
            await ctx.send(
                embed=discord.Embed(
                    title="❌ Slash Sync Failed",
                    description=f"`HTTP {exc.status}`: {exc.text[:300]}",
                    color=COLOR_ERROR,
                ),
                ephemeral=True,
            )
            return
        embed = discord.Embed(
            title="⚡ Slash Commands Synced",
            description=f"Successfully registered **{len(synced)}** slash command(s) globally with Discord.",
            color=COLOR_PRIMARY,
        )
        embed.set_author(name="Timetable Admin")
        embed.set_footer(text="Discord may take up to a minute to refresh commands in your client.")
        await ctx.send(embed=embed, ephemeral=True)

    # ----- student account management -------------------------------------- #
    @commands.hybrid_command(
        name="linkuser",
        description="Link another server member to a batch. Owner only.",
    )
    @commands.is_owner()
    @app_commands.describe(
        member="The server member to link",
        batch="Batch number or name (e.g. 1, 2)",
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

        clean_batches = parse_batches(batch)
        clean_batch = clean_batches[0] if clean_batches else batch.strip().upper()

        prof = tt.set_user_profile(
            user_id=member.id,
            batch=clean_batch,
            name=name.strip() or getattr(member, "display_name", ""),
            roll_no=roll_no.strip(),
        )

        embed = discord.Embed(
            title="✅ Student Account Linked",
            description=f"Successfully linked {member.mention} to **Batch {prof.batch}**.",
            color=COLOR_SUCCESS,
        )
        embed.set_author(name="Timetable Admin")
        embed.add_field(name="👤 Student", value=member.mention, inline=True)
        embed.add_field(name="👥 Batch", value=f"**Batch {prof.batch}**", inline=True)
        if prof.name:
            embed.add_field(name="📝 Name", value=prof.name, inline=True)
        if prof.roll_no:
            embed.add_field(name="🆔 Roll / ID", value=f"`{prof.roll_no}`", inline=True)
        embed.set_footer(text="Saved to data/users.json • User's /timetable commands will now reflect their batch")
        await ctx.send(embed=embed, ephemeral=True)

    @commands.hybrid_command(
        name="unlinkuser",
        description="Unlink a member's batch profile. Owner only.",
    )
    @commands.is_owner()
    @app_commands.describe(member="The server member to unlink")
    async def unlinkuser(self, ctx: commands.Context, member: discord.Member) -> None:
        tt = self._timetable()
        if tt is None:
            await ctx.send("The timetable cog is not loaded.", ephemeral=True)
            return
        removed = tt.delete_user_profile(member.id)
        if removed:
            embed = discord.Embed(
                title="🗑️ Student Account Unlinked",
                description=f"Unlinked {member.mention} from their batch.",
                color=COLOR_WARN,
            )
        else:
            embed = discord.Embed(
                title="ℹ️ Student Not Linked",
                description=f"{member.mention} was not linked to any batch.",
                color=COLOR_PRIMARY,
            )
        embed.set_author(name="Timetable Admin")
        embed.set_footer(text="Updated data/users.json")
        await ctx.send(embed=embed, ephemeral=True)

    @commands.hybrid_command(
        name="students",
        aliases=["batchlist", "roster"],
        description="List all linked students, optionally filtered by batch. Owner only.",
    )
    @commands.is_owner()
    @app_commands.describe(batch="Optional batch filter (e.g. 1, 2)")
    async def students(self, ctx: commands.Context, batch: Optional[str] = None) -> None:
        tt = self._timetable()
        if tt is None:
            await ctx.send("The timetable cog is not loaded.", ephemeral=True)
            return
        profiles = tt.read_profiles()
        if not profiles:
            embed = discord.Embed(
                title="👥 Registered Students",
                description="No students are currently linked to any batch.\nUsers can link their accounts using `/link <batch>`.",
                color=COLOR_MUTED,
            )
            embed.set_author(name="Timetable Admin")
            await ctx.send(embed=embed, ephemeral=True)
            return

        target_batch = batch.strip().upper() if batch else None

        grouped: dict[str, list] = {}
        for prof in profiles.values():
            if target_batch and prof.batch != target_batch:
                continue
            grouped.setdefault(prof.batch, []).append(prof)

        if not grouped:
            embed = discord.Embed(
                title="👥 Registered Students",
                description=f"No students found linked to **Batch {target_batch}**.",
                color=COLOR_MUTED,
            )
            embed.set_author(name="Timetable Admin")
            await ctx.send(embed=embed, ephemeral=True)
            return

        total_count = sum(len(lst) for lst in grouped.values())
        b_info = f" in Batch {target_batch}" if target_batch else ""
        embed = discord.Embed(
            title="👥 Registered Students",
            description=f"> 📋 **{total_count} student{'s' if total_count != 1 else ''} linked**{b_info}",
            color=COLOR_PRIMARY,
        )
        embed.set_author(name="Timetable Admin")

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
                name=f"👥 Batch {b_name} ({len(s_list)} student{'s' if len(s_list) != 1 else ''})",
                value=field_content,
                inline=False,
            )

        embed.set_footer(text=f"Total: {total_count} linked accounts • Saved in data/users.json")
        await ctx.send(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AdminCog(bot))
