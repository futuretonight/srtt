"""Help cog: modern, interactive command explorer and feature guide.

Supports both interactive category browsing (via dropdown menus) and
detailed per-command lookups (/help <command>).
"""
from __future__ import annotations

import difflib
import logging
import os
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

_HAS_UI = False
try:
    if hasattr(discord, "ui") and hasattr(discord.ui, "View") and hasattr(discord.ui, "Select"):
        _HAS_UI = True
except Exception:
    _HAS_UI = False

log = logging.getLogger("timetable_bot.help")

COLOR_PRIMARY = 0x5865F2
COLOR_SUCCESS = 0x57F287
COLOR_WARN = 0xFEE75C
COLOR_ERROR = 0xED4245
COLOR_MUTED = 0x4F545C
DEFAULT_TZ_NAME = "Asia/Kolkata"


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


# --------------------------------------------------------------------------- #
# Command Metadata Registry
# --------------------------------------------------------------------------- #
COMMANDS_DATA: dict[str, dict[str, Any]] = {
    "timetable": {
        "name": "timetable",
        "aliases": ["tt", "day"],
        "category": "timetable",
        "category_name": "Timetable & Schedule",
        "emoji": "📅",
        "summary": "Show your class timetable for a day (auto-filtered to your batch).",
        "description": (
            "Displays scheduled classes and breaks for a specified day.\n"
            "If your account is linked with `/link`, the bot automatically filters "
            "and displays only your batch's classes!"
        ),
        "usage": "[day] [batch]",
        "parameters": [
            ("day", False, "mon–sun, 'today', or 'tomorrow'. Defaults to today."),
            ("batch", False, "Batch filter (e.g. 1, 2). Overrides your linked batch for this command."),
        ],
        "examples": [
            "/timetable",
            "/timetable tomorrow",
            "/timetable fri",
            "/timetable batch:2",
        ],
        "access": "👤 Everyone (Students & Faculty)",
        "tip": "Link your account once with `/link <batch>` so you never have to type your batch number again!",
    },
    "next": {
        "name": "next",
        "aliases": ["now", "upnext"],
        "category": "timetable",
        "category_name": "Timetable & Schedule",
        "emoji": "🧭",
        "summary": "See what class is happening right now and what is up next.",
        "description": (
            "Displays a live status view of your schedule:\n"
            "• Progress bar and remaining time for the ongoing lecture or break.\n"
            "• Relative Discord countdown (`in 25 minutes`) for your next upcoming class."
        ),
        "usage": "[batch]",
        "parameters": [
            ("batch", False, "Filter for a specific batch. Defaults to your linked batch."),
        ],
        "examples": [
            "/next",
            "/next batch:1",
        ],
        "access": "👤 Everyone",
        "tip": "Run between lectures to quickly find out your next classroom and faculty name.",
    },
    "week": {
        "name": "week",
        "aliases": ["weekly", "schedule"],
        "category": "timetable",
        "category_name": "Timetable & Schedule",
        "emoji": "🗓️",
        "summary": "View a full weekly overview of all your classes and teaching hours.",
        "description": (
            "Summarizes Monday through Friday (and weekend slots if scheduled).\n"
            "Calculates total weekly class counts and total teaching hours, and highlights today's schedule."
        ),
        "usage": "[batch]",
        "parameters": [
            ("batch", False, "Filter for a specific batch. Defaults to your linked batch."),
        ],
        "examples": [
            "/week",
            "/week batch:2",
        ],
        "access": "👤 Everyone",
        "tip": "Great for planning your week or checking weekly workload.",
    },
    "link": {
        "name": "link",
        "aliases": ["linkaccount"],
        "category": "linking",
        "category_name": "Account & Batch Linking",
        "emoji": "🎓",
        "summary": "Link your Discord account to your batch and student profile.",
        "description": (
            "Connects your Discord account to your batch number.\n"
            "Once linked, `/timetable`, `/next`, and `/week` will automatically display your schedule!\n"
            "Running `/link` with no arguments opens interactive one-click batch buttons."
        ),
        "usage": "[batch] [name] [roll_no]",
        "parameters": [
            ("batch", False, "Your batch number or code (e.g. 1, 2, CS-A). Omit to show interactive buttons."),
            ("name", False, "Optional full student name (e.g. \"Alex Doe\")."),
            ("roll_no", False, "Optional roll number or student ID (e.g. \"21CS042\")."),
        ],
        "examples": [
            "/link",
            "/link 1",
            '/link batch:2 name:"Alex" roll_no:"21CS042"',
        ],
        "access": "👤 Everyone",
        "tip": "You can change your batch anytime by running `/link <new_batch>`.",
    },
    "profile": {
        "name": "profile",
        "aliases": ["me", "myprofile", "whoami"],
        "category": "linking",
        "category_name": "Account & Batch Linking",
        "emoji": "👤",
        "summary": "View your student profile card and schedule summary for today.",
        "description": (
            "Displays your linked batch, registered student name, roll number, "
            "registration timestamp, and a summary of how many classes you have today."
        ),
        "usage": "[member]",
        "parameters": [
            ("member", False, "Optional server member to view their profile (Admins/Moderators)."),
        ],
        "examples": [
            "/profile",
            "/profile member:@Alice",
        ],
        "access": "👤 Everyone",
        "tip": "Shows avatar, student credentials, and today's class count at a glance.",
    },
    "unlink": {
        "name": "unlink",
        "aliases": ["unlinkaccount", "clearbatch"],
        "category": "linking",
        "category_name": "Account & Batch Linking",
        "emoji": "🗑️",
        "summary": "Unlink your Discord account and reset to viewing all batches.",
        "description": (
            "Disconnects your Discord account from any saved batch.\n"
            "Timetable commands will revert to showing all batches by default until you link again."
        ),
        "usage": "",
        "parameters": [],
        "examples": [
            "/unlink",
        ],
        "access": "👤 Everyone",
        "tip": "You can re-link anytime with `/link <batch>`.",
    },
    "mybatch": {
        "name": "mybatch",
        "aliases": ["setbatch"],
        "category": "linking",
        "category_name": "Account & Batch Linking",
        "emoji": "👥",
        "summary": "Quick shortcut to check, link, or clear your batch preference.",
        "description": (
            "Backwards-compatible shortcut command.\n"
            "• `/mybatch` ➔ Views your profile.\n"
            "• `/mybatch 1` ➔ Links to Batch 1.\n"
            "• `/mybatch clear` ➔ Unlinks your account."
        ),
        "usage": "[batch]",
        "parameters": [
            ("batch", False, "Batch number (e.g. 1, 2) or 'clear' to unlink."),
        ],
        "examples": [
            "/mybatch",
            "/mybatch 1",
            "/mybatch clear",
        ],
        "access": "👤 Everyone",
        "tip": "Prefer using the dedicated `/link`, `/profile`, and `/unlink` commands for more features.",
    },
    "setclass": {
        "name": "setclass",
        "aliases": [],
        "category": "admin",
        "category_name": "Admin & Management",
        "emoji": "✏️",
        "summary": "Add or replace a class slot or break in the schedule.",
        "description": (
            "Directly modifies `data/schedule.json`. Changes apply immediately with no bot restart needed.\n"
            "Supports multi-batch slots (e.g. `batch:1, 2` or `batch:ALL`), room numbers, and faculty names."
        ),
        "usage": "<day> <time_slot> <subject> [batch] [room] [faculty]",
        "parameters": [
            ("day", True, "Day of the week (mon, tue, wed, thu, fri, sat, sun)."),
            ("time_slot", True, "Time range in 24-hr format (e.g. 09:30-10:30)."),
            ("subject", True, "Subject name (e.g. \"Applied Physics (Lecture)\") or break (FREE, LUNCH)."),
            ("batch", False, "Batch number(s), e.g. \"1\", \"1, 2\", or \"ALL\". Default: ALL."),
            ("room", False, "Classroom or laboratory location."),
            ("faculty", False, "Professor or instructor name."),
        ],
        "examples": [
            '/setclass tue 08:30-09:30 "Applied Physics (Lecture)" batch:1 room:8208 faculty:"Dr Abin"',
            '/setclass mon 12:30-13:30 LUNCH',
            '/setclass wed 10:30-11:30 "Calculus (Tutorial)" batch:"1, 2"',
        ],
        "access": "🔒 Bot Owner Only",
        "tip": "Smart overlap detection prevents conflicting classes for the same batch while allowing different batches to share the time slot.",
    },
    "delclass": {
        "name": "delclass",
        "aliases": [],
        "category": "admin",
        "category_name": "Admin & Management",
        "emoji": "🗑️",
        "summary": "Remove a scheduled slot by day and start time.",
        "description": (
            "Deletes a class or break from `data/schedule.json`.\n"
            "If multiple batches have classes at the same start time, specify `batch:<number>`."
        ),
        "usage": "<day> <start> [batch]",
        "parameters": [
            ("day", True, "Day of the week (mon, tue, wed, thu, fri, sat, sun)."),
            ("start", True, "Start time in HH:MM (e.g. 09:30)."),
            ("batch", False, "Optional batch filter if multiple slots share this start time."),
        ],
        "examples": [
            "/delclass wed 09:30",
            "/delclass wed 09:30 batch:1",
        ],
        "access": "🔒 Bot Owner Only",
        "tip": "Use `/timetable [day]` to check slot start times before deleting.",
    },
    "linkuser": {
        "name": "linkuser",
        "aliases": [],
        "category": "admin",
        "category_name": "Admin & Management",
        "emoji": "👥",
        "summary": "Admin command to link another server member to a batch.",
        "description": (
            "Enables server administrators to set up or adjust student batch affiliations directly."
        ),
        "usage": "<member> <batch> [name] [roll_no]",
        "parameters": [
            ("member", True, "The Discord server member to link."),
            ("batch", True, "Batch number or code (e.g. 1, 2)."),
            ("name", False, "Optional student real name."),
            ("roll_no", False, "Optional student roll / registration number."),
        ],
        "examples": [
            "/linkuser member:@Alice batch:1",
            '/linkuser member:@Bob batch:2 name:"Robert" roll_no:"21CS045"',
        ],
        "access": "🔒 Bot Owner Only",
        "tip": "Saved immediately to `data/users.json`.",
    },
    "unlinkuser": {
        "name": "unlinkuser",
        "aliases": [],
        "category": "admin",
        "category_name": "Admin & Management",
        "emoji": "👤",
        "summary": "Admin command to unlink a server member's profile.",
        "description": "Removes a specific member's batch profile from `data/users.json`.",
        "usage": "<member>",
        "parameters": [
            ("member", True, "The Discord member to unlink."),
        ],
        "examples": [
            "/unlinkuser member:@Alice",
        ],
        "access": "🔒 Bot Owner Only",
        "tip": "The member will revert to viewing all batches.",
    },
    "students": {
        "name": "students",
        "aliases": ["batchlist", "roster"],
        "category": "admin",
        "category_name": "Admin & Management",
        "emoji": "📋",
        "summary": "List all linked students grouped by batch with counts and roll numbers.",
        "description": (
            "Generates a complete roster of all registered students, organized neatly by batch.\n"
            "Shows total student counts and optional roll numbers."
        ),
        "usage": "[batch]",
        "parameters": [
            ("batch", False, "Optional filter to list students from only one specific batch."),
        ],
        "examples": [
            "/students",
            "/students batch:1",
        ],
        "access": "🔒 Bot Owner Only",
        "tip": "Great for class representatives checking who has linked their account.",
    },
    "setprefix": {
        "name": "setprefix",
        "aliases": [],
        "category": "admin",
        "category_name": "Admin & Management",
        "emoji": "⚙️",
        "summary": "Change the command prefix for the bot.",
        "description": (
            "Sets a new command prefix (1–5 characters, no spaces).\n"
            "Persists to `data/config.json` and updates the bot's Discord activity presence."
        ),
        "usage": "<prefix>",
        "parameters": [
            ("prefix", True, "New prefix (1–5 characters, e.g. !, ?, ., $)."),
        ],
        "examples": [
            "/setprefix ?",
            "/setprefix .",
        ],
        "access": "🔒 Bot Owner Only",
        "tip": "Slash commands continue working normally regardless of the prefix.",
    },
    "sync": {
        "name": "sync",
        "aliases": [],
        "category": "admin",
        "category_name": "Admin & Management",
        "emoji": "⚡",
        "summary": "Sync slash commands globally with Discord.",
        "description": (
            "Registers all slash commands and command options globally with Discord's API.\n"
            "Run this after updating command parameters or adding new commands."
        ),
        "usage": "",
        "parameters": [],
        "examples": [
            "/sync",
        ],
        "access": "🔒 Bot Owner Only",
        "tip": "Discord may take up to a minute to refresh slash commands in your client.",
    },
    "reload": {
        "name": "reload",
        "aliases": [],
        "category": "admin",
        "category_name": "Admin & Management",
        "emoji": "🔄",
        "summary": "Hot-reload bot cogs without restarting Python.",
        "description": (
            "Reloads extensions live in memory.\n"
            "Pass a specific cog (e.g. `cogs.timetable`) or leave empty to reload everything."
        ),
        "usage": "[cog_path]",
        "parameters": [
            ("cog_path", False, "Specific extension (e.g. cogs.timetable, cogs.help). Omit for all."),
        ],
        "examples": [
            "/reload",
            "/reload cogs.timetable",
        ],
        "access": "🔒 Bot Owner Only",
        "tip": "If you changed slash command parameters, run `/sync` after reloading.",
    },
    "help": {
        "name": "help",
        "aliases": ["commands", "guide", "h"],
        "category": "general",
        "category_name": "General & Information",
        "emoji": "📚",
        "summary": "Explore commands, feature guides, and detailed command syntax.",
        "description": (
            "Shows an interactive multi-category help explorer.\n"
            "Specify a command name (`/help <command>`) for in-depth parameter breakdown and examples."
        ),
        "usage": "[command]",
        "parameters": [
            ("command", False, "Name of the command to get in-depth help for (e.g. timetable, link)."),
        ],
        "examples": [
            "/help",
            "/help timetable",
            "/help link",
            "/help setclass",
        ],
        "access": "👤 Everyone",
        "tip": "Use the dropdown menu below the help message to switch between categories.",
    },
}


# --------------------------------------------------------------------------- #
# Interactive UI Components
# --------------------------------------------------------------------------- #
if _HAS_UI:
    class HelpSelect(discord.ui.Select):
        def __init__(self, cog: "HelpCog", author_id: int, prefix: str):
            self.cog = cog
            self.author_id = author_id
            self.prefix = prefix
            options = [
                discord.SelectOption(
                    label="Overview & All Commands",
                    value="overview",
                    description="Summary of all commands and quick start guide",
                    emoji="🏠",
                    default=True,
                ),
                discord.SelectOption(
                    label="Timetable & Schedule",
                    value="timetable",
                    description="Daily timetable, live status & weekly overview",
                    emoji="📅",
                ),
                discord.SelectOption(
                    label="Account & Batch Linking",
                    value="linking",
                    description="Personalized schedule linking & profile cards",
                    emoji="🔗",
                ),
                discord.SelectOption(
                    label="Admin & Management",
                    value="admin",
                    description="Class editor, student roster & bot settings",
                    emoji="🛡️",
                ),
                discord.SelectOption(
                    label="FAQ, Tips & Termux",
                    value="faq",
                    description="Termux notes, multi-batch tips & shortcuts",
                    emoji="💡",
                ),
            ]
            super().__init__(
                placeholder="Choose a category to browse...",
                min_values=1,
                max_values=1,
                options=options,
                custom_id=f"help_select_{author_id}",
            )

        async def callback(self, interaction: discord.Interaction):
            if interaction.user.id != self.author_id:
                if hasattr(interaction, "response"):
                    await interaction.response.send_message(
                        "❌ This help menu belongs to the user who ran the command. Run `/help` to open your own!",
                        ephemeral=True,
                    )
                return

            chosen = self.values[0]
            for opt in self.options:
                opt.default = (opt.value == chosen)

            embed = self.cog.build_category_embed(chosen, self.prefix, interaction.user)
            await interaction.response.edit_message(embed=embed, view=self.view)

    class HelpView(discord.ui.View):
        def __init__(self, cog: "HelpCog", author_id: int, prefix: str, timeout: float = 120.0):
            super().__init__(timeout=timeout)
            self.cog = cog
            self.author_id = author_id
            self.prefix = prefix
            self.add_item(HelpSelect(cog, author_id, prefix))

        async def on_timeout(self) -> None:
            for item in self.children:
                setattr(item, "disabled", True)
else:
    HelpView = None  # type: ignore[assignment, misc]


# --------------------------------------------------------------------------- #
# Cog Implementation
# --------------------------------------------------------------------------- #
class HelpCog(commands.Cog):
    """Modern, interactive command explorer and feature guide."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    def _prefix(self, ctx: commands.Context) -> str:
        return ctx.clean_prefix or getattr(self.bot, "prefix", "!")

    def _tz_name(self) -> str:
        name = (
            os.environ.get("TIMEZONE")
            or os.environ.get("TZ")
            or str(getattr(self.bot, "config", {}).get("timezone") or "")
        ).strip()
        if not name:
            android_tz = _detect_android_timezone()
            name = android_tz if android_tz else DEFAULT_TZ_NAME
        return name

    async def _is_owner(self, user: discord.abc.User) -> bool:
        try:
            return await self.bot.is_owner(user)
        except Exception:
            return False

    # ----- category embed builders ----------------------------------------- #
    def build_overview_embed(self, prefix: str, tz_name: str, is_owner: bool = False) -> discord.Embed:
        embed = discord.Embed(
            title="📚 Timetable Bot — Help & Commands Guide",
            description=(
                "Welcome to **Timetable Bot**! View class schedules, track live lecture progress, "
                "and link your Discord account for personalized batch schedules.\n\n"
                f"> 💡 **Commands work both ways:** Modern Slash (`/timetable`) or Prefix (`{prefix}timetable`)."
            ),
            color=COLOR_PRIMARY,
        )
        embed.set_author(name="Timetable Bot")

        embed.add_field(
            name="📅 Timetable & Schedules",
            value=(
                f"• **`{prefix}timetable [day] [batch]`** — View day's classes *(auto-filters to your batch)*\n"
                f"• **`{prefix}next [batch]`** — Live status: ongoing class with progress bar & up next\n"
                f"• **`{prefix}week [batch]`** — Weekly overview of all classes & total teaching time"
            ),
            inline=False,
        )

        embed.add_field(
            name="🔗 Account & Batch Linking",
            value=(
                f"• **`{prefix}link [batch] [name] [roll]`** — Link your account to your batch (or click buttons!)\n"
                f"• **`{prefix}profile [member]`** — View your student profile, linked batch & today's classes\n"
                f"• **`{prefix}unlink`** — Disconnect your account & revert to viewing all batches"
            ),
            inline=False,
        )

        owner_tag = " *(👑 You have access)*" if is_owner else ""
        embed.add_field(
            name=f"🛡️ Admin Controls{owner_tag}",
            value=(
                f"• **`{prefix}setclass`** / **`{prefix}delclass`** — Add, edit, or delete classes & breaks\n"
                f"• **`{prefix}linkuser`** / **`{prefix}unlinkuser`** — Link or unlink any server member\n"
                f"• **`{prefix}students [batch]`** — View all registered students grouped by batch\n"
                f"• **`{prefix}setprefix`** / **`{prefix}sync`** / **`{prefix}reload`** — Bot management"
            ),
            inline=False,
        )

        embed.add_field(
            name="🚀 Quick Start in 3 Easy Steps",
            value=(
                "**1️⃣ Link your batch:** Run `/link 1` (or your batch number)\n"
                "**2️⃣ View your schedule:** Run `/timetable` (automatically shows your batch)\n"
                "**3️⃣ Check live status:** Run `/next` between classes to see your next room & faculty"
            ),
            inline=False,
        )

        embed.set_footer(
            text=f"Prefix: {prefix} • Timezone: {tz_name} • Tip: Use the dropdown below or run /help <command>"
        )
        return embed

    def build_timetable_embed(self, prefix: str) -> discord.Embed:
        embed = discord.Embed(
            title="📅 Timetable & Schedule Commands",
            description=(
                "View and track your daily classes, breaks, and lecture schedules.\n"
                "When you link your account, the bot automatically isolates your batch's classes!"
            ),
            color=COLOR_PRIMARY,
        )
        embed.set_author(name="Timetable Bot • Schedules")

        embed.add_field(
            name=f"### 📅 `{prefix}timetable [day] [batch]`",
            value=(
                "Shows the complete timetable for a specific day.\n"
                "• **Auto-Batch:** Automatically filters classes for your linked batch.\n"
                "• **Override:** Pass `batch:2` to temporarily check another batch.\n"
                "> 💡 **Examples:**\n"
                f"> • `{prefix}timetable` — Today's schedule\n"
                f"> • `{prefix}timetable tomorrow` — Tomorrow's classes\n"
                f"> • `{prefix}timetable fri` — Friday's classes\n"
                f"> • `{prefix}timetable batch:2` — Check Batch 2"
            ),
            inline=False,
        )

        embed.add_field(
            name=f"### 🧭 `{prefix}next [batch]`",
            value=(
                "Live status view of what's happening right now:\n"
                "• Displays an ongoing lecture/break with visual percentage bar (`▰▰▰▱▱ 60%`).\n"
                "• Shows exact end time and countdown to your next class (`in 20 minutes`).\n"
                "> 💡 **Example:** `/next`"
            ),
            inline=False,
        )

        embed.add_field(
            name=f"### 🗓️ `{prefix}week [batch]`",
            value=(
                "Displays the entire Monday to Friday schedule (and weekends if present).\n"
                "Calculates total weekly classes and teaching hours, and marks today's schedule.\n"
                "> 💡 **Example:** `/week`"
            ),
            inline=False,
        )

        embed.set_footer(text=f"Prefix: {prefix} • Tip: Run /help <command> for parameter details")
        return embed

    def build_linking_embed(self, prefix: str) -> discord.Embed:
        embed = discord.Embed(
            title="🔗 Account & Batch Linking Commands",
            description=(
                "Link your Discord account to your batch! When two students run `/timetable`, "
                "each receives their own specific batch's classes automatically."
            ),
            color=COLOR_SUCCESS,
        )
        embed.set_author(name="Timetable Bot • Account Linking")

        embed.add_field(
            name=f"### 🎓 `{prefix}link [batch] [name] [roll_no]`",
            value=(
                "Connect your Discord ID to your batch.\n"
                "• **Interactive Mode:** Run `/link` with no arguments to pick your batch via buttons!\n"
                "• **Quick Link:** Run `/link 1` or `/link 2`.\n"
                "• **Full Profile:** `/link batch:1 name:\"Alex\" roll_no:\"21CS042\"`.\n"
                "> 💡 **Tip:** You can switch batches anytime by running `/link <new_batch>`."
            ),
            inline=False,
        )

        embed.add_field(
            name=f"### 👤 `{prefix}profile [member]`",
            value=(
                "Displays your personal student profile card:\n"
                "• Linked batch, student name, roll number, and registration date.\n"
                "• Live count of how many classes you have scheduled today.\n"
                "> 💡 **Example:** `/profile` (or `/profile member:@Alice`)"
            ),
            inline=False,
        )

        embed.add_field(
            name=f"### 🗑️ `{prefix}unlink`",
            value=(
                "Disconnects your Discord account from any saved batch.\n"
                "The bot will revert to showing schedules for all batches until you link again.\n"
                "> 💡 **Example:** `/unlink`"
            ),
            inline=False,
        )

        embed.set_footer(text=f"Prefix: {prefix} • Saved in data/users.json")
        return embed

    def build_admin_embed(self, prefix: str, is_owner: bool = False) -> discord.Embed:
        owner_badge = " *(👑 Access Granted)*" if is_owner else " *(🔒 Owner Only)*"
        embed = discord.Embed(
            title=f"🛡️ Admin & Management Commands{owner_badge}",
            description=(
                "Controls for managing timetable schedules, student rosters, and bot settings.\n"
                "These commands are restricted to the bot owner."
            ),
            color=COLOR_WARN,
        )
        embed.set_author(name="Timetable Bot • Administration")

        embed.add_field(
            name=f"### ✏️ `{prefix}setclass <day> <time> <subject> [batch] [room] [faculty]`",
            value=(
                "Insert or replace a class slot or break in the schedule.\n"
                "• **Multi-batch:** Set `batch:1` or `batch:\"1, 2\"` or `batch:ALL`.\n"
                "• **Breaks:** Use `FREE` or `LUNCH` as the subject for break periods.\n"
                "> 💡 **Example:**\n"
                f'> `{prefix}setclass tue 08:30-09:30 "Applied Physics (Lecture)" batch:1 room:8208 faculty:"Dr Abin"`'
            ),
            inline=False,
        )

        embed.add_field(
            name=f"### 🗑️ `{prefix}delclass <day> <start> [batch]`",
            value=(
                "Delete a slot by day and start time (HH:MM).\n"
                "> 💡 **Example:** `/delclass tue 08:30 batch:1`"
            ),
            inline=False,
        )

        embed.add_field(
            name=f"### 📋 `{prefix}students [batch]`",
            value=(
                "View all linked students grouped by batch with student counts and roll numbers.\n"
                "> 💡 **Example:** `/students` or `/students batch:1`"
            ),
            inline=False,
        )

        embed.add_field(
            name=f"### 👥 `{prefix}linkuser` & `{prefix}unlinkuser`",
            value=(
                "• **`linkuser <member> <batch> [name] [roll]`** — Link any server member to a batch.\n"
                "• **`unlinkuser <member>`** — Unlink a server member's profile."
            ),
            inline=False,
        )

        embed.add_field(
            name=f"### ⚙️ `{prefix}setprefix` / `{prefix}sync` / `{prefix}reload`",
            value=(
                f"• **`{prefix}setprefix <prefix>`** — Change bot prefix (saved to data/config.json).\n"
                f"• **`{prefix}sync`** — Globally sync slash commands with Discord.\n"
                f"• **`{prefix}reload [cog]`** — Hot-reload bot extensions without restarting."
            ),
            inline=False,
        )

        embed.set_footer(text=f"Prefix: {prefix} • Live immediate updates in data/schedule.json")
        return embed

    def build_faq_embed(self, prefix: str, tz_name: str) -> discord.Embed:
        embed = discord.Embed(
            title="💡 FAQ, Tips & Termux Information",
            description="Helpful tips and technical details for running and using Timetable Bot.",
            color=COLOR_PRIMARY,
        )
        embed.set_author(name="Timetable Bot • FAQ & Tips")

        embed.add_field(
            name="📱 Termux & Mobile Hosting",
            value=(
                "The bot is specifically optimized for Termux on Android:\n"
                f"• **Timezone Detection:** Automatically detects your Android system timezone via `persist.sys.timezone` (currently: **`{tz_name}`**).\n"
                "• **Crash-Proof Storage:** All data files (`schedule.json`, `users.json`) use atomic file writes (`.tmp` ➔ replace) "
                "so abrupt Android battery saver kills never corrupt your data."
            ),
            inline=False,
        )

        embed.add_field(
            name="👥 Multi-Batch Classes",
            value=(
                "Different batches can share the same time slot! When adding classes:\n"
                "• Set `batch:1` for Batch 1 only.\n"
                "• Set `batch:\"1, 2\"` if Batches 1 & 2 share a lecture.\n"
                "• Set `batch:ALL` (or leave empty) for common university classes."
            ),
            inline=False,
        )

        embed.add_field(
            name="⚡ Slash Commands vs Prefix Commands",
            value=(
                f"You can use modern Discord slash commands (`/timetable`, `/next`, `/link`) "
                f"or traditional prefix commands (`{prefix}timetable`, `{prefix}next`, `{prefix}link`). "
                "Both provide the exact same rich embeds!"
            ),
            inline=False,
        )

        embed.add_field(
            name="🔧 First-Run Setup & Token Storage",
            value=(
                "Bot credentials are saved in the project's `.env` file (`DISCORD_TOKEN`). "
                "If the bot starts without a token, it will safely prompt you in the terminal "
                "and save it automatically for every future run."
            ),
            inline=False,
        )

        embed.set_footer(text=f"Prefix: {prefix} • Timezone: {tz_name} • Run /help to return to overview")
        return embed

    def build_category_embed(self, category: str, prefix: str, user: discord.abc.User) -> discord.Embed:
        tz_name = self._tz_name()
        is_owner = (user.id == getattr(self.bot, "owner_id", None)) or (
            hasattr(self.bot, "owner_ids") and user.id in (getattr(self.bot, "owner_ids") or ())
        )
        cat = category.lower().strip()
        if cat in ("timetable", "schedule", "classes"):
            return self.build_timetable_embed(prefix)
        if cat in ("linking", "link", "profile", "account"):
            return self.build_linking_embed(prefix)
        if cat in ("admin", "management", "owner"):
            return self.build_admin_embed(prefix, is_owner=is_owner)
        if cat in ("faq", "tips", "termux", "info"):
            return self.build_faq_embed(prefix, tz_name)
        return self.build_overview_embed(prefix, tz_name, is_owner=is_owner)

    # ----- command detail card --------------------------------------------- #
    def build_command_embed(self, cmd_data: dict[str, Any], prefix: str) -> discord.Embed:
        name = cmd_data["name"]
        emoji = cmd_data.get("emoji", "📖")
        embed = discord.Embed(
            title=f"{emoji} Command: /{name}",
            description=cmd_data["description"],
            color=COLOR_PRIMARY,
        )
        embed.set_author(name=f"Timetable Bot • {cmd_data.get('category_name', 'Command Help')}")

        usage_str = f"/{name} {cmd_data['usage']}".strip()
        prefix_str = f"{prefix}{name} {cmd_data['usage']}".strip()
        embed.add_field(
            name="⚙️ Syntax & Usage",
            value=f"> Slash: **`{usage_str}`**\n> Prefix: **`{prefix_str}`**",
            inline=False,
        )

        aliases = cmd_data.get("aliases", [])
        if aliases:
            alias_list = ", ".join(f"`{prefix}{a}` (or `/{a}`)" for a in aliases)
            embed.add_field(name="🔤 Aliases", value=alias_list, inline=False)

        params = cmd_data.get("parameters", [])
        if params:
            param_lines = []
            for p_name, p_req, p_desc in params:
                req_badge = "**Required**" if p_req else "*Optional*"
                param_lines.append(f"• **`{p_name}`** ({req_badge}) — {p_desc}")
            embed.add_field(name="📋 Parameters", value="\n".join(param_lines), inline=False)

        examples = cmd_data.get("examples", [])
        if examples:
            example_lines = [f"> • `{ex}`" for ex in examples]
            embed.add_field(name="💡 Examples", value="\n".join(example_lines), inline=False)

        access = cmd_data.get("access", "👤 Everyone")
        embed.add_field(name="🔒 Permissions", value=access, inline=True)

        tip = cmd_data.get("tip")
        if tip:
            embed.add_field(name="✨ Tip", value=tip, inline=False)

        embed.set_footer(text=f"Prefix: {prefix} • Run /help to view all categories")
        return embed

    # ----- help command ---------------------------------------------------- #
    @commands.hybrid_command(
        name="help",
        aliases=["commands", "guide", "h"],
        description="View bot commands, feature guides, and detailed command help.",
    )
    @app_commands.describe(command="Optional command to get detailed help for (e.g. timetable, link, setclass)")
    async def help_command(self, ctx: commands.Context, command: Optional[str] = None) -> None:
        prefix = self._prefix(ctx)
        tz_name = self._tz_name()
        is_owner = await self._is_owner(ctx.author)

        # Case 1: Specific command lookup
        if command is not None and command.strip():
            query = command.strip().lower().lstrip("/!?.")
            # Exact match or alias match
            target_data: Optional[dict[str, Any]] = None
            if query in COMMANDS_DATA:
                target_data = COMMANDS_DATA[query]
            else:
                for cdata in COMMANDS_DATA.values():
                    if query in cdata.get("aliases", []):
                        target_data = cdata
                        break

            if target_data is not None:
                embed = self.build_command_embed(target_data, prefix)
                await _safe_send(ctx, embed=embed, ephemeral=True)
                return

            # Check if query matches a category name
            if query in ("timetable", "schedule", "classes", "linking", "link", "profile", "admin", "owner", "faq", "termux", "tips"):
                embed = self.build_category_embed(query, prefix, ctx.author)
                view = None
                if _HAS_UI and HelpView is not None:
                    view = HelpView(self, ctx.author.id, prefix)
                await _safe_send(ctx, embed=embed, view=view, ephemeral=True)
                return

            # Not found: suggest closest matches
            all_names = list(COMMANDS_DATA.keys())
            for cdata in COMMANDS_DATA.values():
                all_names.extend(cdata.get("aliases", []))
            close_matches = difflib.get_close_matches(query, all_names, n=3, cutoff=0.5)

            embed = discord.Embed(
                title="⚠️ Command Not Found",
                description=f"I couldn't find a command named **`{query[:40]}`**.",
                color=COLOR_WARN,
            )
            embed.set_author(name="Timetable Bot • Help")
            if close_matches:
                embed.add_field(
                    name="🔍 Did you mean?",
                    value=", ".join(f"`/{m}`" for m in close_matches),
                    inline=False,
                )
            embed.add_field(
                name="💡 Need help?",
                value=f"Run **`{prefix}help`** or **`/help`** to view all available commands.",
                inline=False,
            )
            embed.set_footer(text=f"Prefix: {prefix} • Timezone: {tz_name}")
            await _safe_send(ctx, embed=embed, ephemeral=True)
            return

        # Case 2: Main overview
        embed = self.build_overview_embed(prefix, tz_name, is_owner=is_owner)
        view = None
        if _HAS_UI and HelpView is not None:
            view = HelpView(self, ctx.author.id, prefix)

        await _safe_send(ctx, embed=embed, view=view)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(HelpCog(bot))
