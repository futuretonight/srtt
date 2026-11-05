"""
cogs/help.py - Clean, Professional, Emoji-Free Help Desk for Academic Timetable Bot.

Features:
  - Interactive dropdown menu to browse categories without spamming chat.
  - Dedicated cards for all 21 commands across Timetable, Linking, Attendance, and Administration.
  - Direct deep-dive support: /help <command_name> shows full syntax, arguments, permissions, and examples.
  - Clean typographic hierarchy with standard Discord Markdown (bold, codeblocks, quotes).
  - Strictly 0 emojis for a clean, distraction-free aesthetic.
  - Safe fallback for headless/Termux environments where UI components might be limited.
"""

from __future__ import annotations

import logging
from typing import Optional

import discord
from discord.ext import commands

if not hasattr(commands, "hybrid_command"):
    commands.hybrid_command = commands.command  # type: ignore[attr-defined]

if not hasattr(commands, "hybrid_group"):
    commands.hybrid_group = commands.group  # type: ignore[attr-defined]

logger = logging.getLogger("timetable_bot.help")

# Palette constants (Professional slate / indigo / navy)
COLOR_DEFAULT = 0x2B2D31  # Discord Dark Slate
COLOR_ACCENT = 0x5865F2   # Blurple
COLOR_ADMIN = 0xED4245    # Red / Coral
COLOR_SUCCESS = 0x57F287  # Green
COLOR_WARNING = 0xFEE75C  # Amber

# ---------------------------------------------------------------------------
# All 21 Commands Catalog & Metadata
# ---------------------------------------------------------------------------
COMMAND_REGISTRY = {
    # Category 1: Timetable & Classes
    "timetable": {
        "category": "Timetable",
        "syntax": "/timetable [day] [batch]",
        "aliases": ["tt"],
        "description": "Displays the schedule for a specific day or today by default. If your account is linked, your batch is auto-selected.",
        "arguments": [
            ("day", "Optional", "Day of the week (e.g. monday, tue, today). Defaults to current day."),
            ("batch", "Optional", "Specific batch to query (e.g. 1, 2, 3). Overrides linked profile."),
        ],
        "permissions": "Everyone",
        "example": "/timetable day:wednesday batch:1",
    },
    "next": {
        "category": "Timetable",
        "syntax": "/next [batch]",
        "aliases": ["upcoming"],
        "description": "Quick lookup for your next upcoming class today with real-time countdown, room, faculty, and remaining minutes.",
        "arguments": [
            ("batch", "Optional", "Specific batch to check. Defaults to your linked batch."),
        ],
        "permissions": "Everyone",
        "example": "/next",
    },
    "week": {
        "category": "Timetable",
        "syntax": "/week [batch]",
        "aliases": ["fullweek", "weekly"],
        "description": "Displays the full weekly schedule (Monday - Saturday) for your batch in a single consolidated report.",
        "arguments": [
            ("batch", "Optional", "Batch number (e.g. 1, 2). Defaults to your linked batch."),
        ],
        "permissions": "Everyone",
        "example": "/week batch:2",
    },

    # Category 2: Student Profile & Linking
    "link": {
        "category": "Linking",
        "syntax": "/link [batch] [name] [roll_no]",
        "aliases": ["linkme", "register"],
        "description": "Links your Discord account to your batch, full name, and roll number for personalized timetables and attendance tracking.",
        "arguments": [
            ("batch", "Required", "Your assigned batch number or identifier (e.g. 1, 2, B1)."),
            ("name", "Optional", "Your real full name (used in attendance rosters)."),
            ("roll_no", "Optional", "Your university roll number or student ID."),
        ],
        "permissions": "Everyone",
        "example": "/link batch:1 name:John Doe roll_no:21CS042",
    },
    "profile": {
        "category": "Linking",
        "syntax": "/profile [member]",
        "aliases": ["me", "whois"],
        "description": "Views academic profile details (batch, name, roll number, attendance summary) for yourself or another student.",
        "arguments": [
            ("member", "Optional", "The Discord member to inspect. Defaults to yourself."),
        ],
        "permissions": "Everyone",
        "example": "/profile member:@Student",
    },
    "unlink": {
        "category": "Linking",
        "syntax": "/unlink",
        "aliases": ["unlinkme"],
        "description": "Removes your linked batch, student name, and roll number profile from the bot database.",
        "arguments": [],
        "permissions": "Everyone",
        "example": "/unlink",
    },
    "mybatch": {
        "category": "Linking",
        "syntax": "/mybatch [batch]",
        "aliases": ["setbatch", "switchbatch"],
        "description": "Quickly inspects or updates your active batch without resetting your saved name and roll number.",
        "arguments": [
            ("batch", "Optional", "New batch to switch to. If omitted, prints your current active batch."),
        ],
        "permissions": "Everyone",
        "example": "/mybatch batch:2",
    },

    # Category 3: Attendance & Faculty
    "attendance": {
        "category": "Attendance",
        "syntax": "/attendance <take|summary|export> ...",
        "aliases": ["att"],
        "description": "Master attendance command group with subcommands to mark attendance, review statistics, or export batch rosters.",
        "arguments": [
            ("take", "Subcommand", "Opens interactive attendance portal for authorized faculty (/attendance take <batch> <subject> [day])."),
            ("summary", "Subcommand", "Shows percentage summaries and at-risk students below 75% (/attendance summary <batch> [subject])."),
            ("export", "Subcommand", "Generates and uploads an attendance spreadsheet (.csv) (/attendance export <batch>)."),
        ],
        "permissions": "Authorized Faculty or Server Admins",
        "example": "/attendance take batch:1 subject:Data Structures",
    },
    "myattendance": {
        "category": "Attendance",
        "syntax": "/myattendance [subject]",
        "aliases": ["attendance_me", "bunk"],
        "description": "Personal student attendance portal. Calculates attendance percentages, visual status bars, safe classes to miss, and shortage recovery.",
        "arguments": [
            ("subject", "Optional", "Filter statistics for a specific subject name. If omitted, shows all subjects."),
        ],
        "permissions": "Linked Students",
        "example": "/myattendance subject:Physics",
    },
    "setlecturer": {
        "category": "Attendance",
        "syntax": "/setlecturer <member> <subject> [batch]",
        "aliases": ["addlecturer", "authfaculty"],
        "description": "Authorizes a Discord member to mark attendance for a subject. Can optionally be scoped to a single batch.",
        "arguments": [
            ("member", "Required", "Discord user/faculty to grant attendance recording permissions."),
            ("subject", "Required", "Exact subject name or shorthand (e.g. Mathematics, Operating Systems)."),
            ("batch", "Optional", "Optional batch restriction (e.g. 1). If omitted, faculty can take for all batches."),
        ],
        "permissions": "Server Admin or Bot Owner",
        "example": "/setlecturer member:@Professor subject:Algorithms batch:1",
    },
    "dellecturer": {
        "category": "Attendance",
        "syntax": "/dellecturer <member>",
        "aliases": ["removelecturer"],
        "description": "Revokes all faculty attendance taking permissions from the specified Discord user.",
        "arguments": [
            ("member", "Required", "Faculty member whose access will be revoked."),
        ],
        "permissions": "Server Admin or Bot Owner",
        "example": "/dellecturer member:@Professor",
    },
    "lecturers": {
        "category": "Attendance",
        "syntax": "/lecturers",
        "aliases": ["faculty", "faculty_list"],
        "description": "Displays the directory of all registered faculty, their authorized subjects, batch scopes, and granted dates.",
        "arguments": [],
        "permissions": "Everyone",
        "example": "/lecturers",
    },

    # Category 4: Administration & Setup
    "setclass": {
        "category": "Admin",
        "syntax": "/setclass <day> <time_slot> <subject> [batch] [room] [faculty]",
        "aliases": ["addclass"],
        "description": "Adds or overwrites a class slot in the schedule. Automatically sorts and persists changes to timetable.json.",
        "arguments": [
            ("day", "Required", "Day of the week (e.g. Monday, wed)."),
            ("time_slot", "Required", "Time window in HH:MM-HH:MM 24h format (e.g. 09:00-10:00)."),
            ("subject", "Required", "Title or code of the course."),
            ("batch", "Optional", "Specific batch (e.g. 1, 2) or 'all' for joint lectures. Defaults to 'all'."),
            ("room", "Optional", "Assigned lecture hall, classroom, or lab number (e.g. Room 402, CS-Lab-A)."),
            ("faculty", "Optional", "Name or designation of the instructor in charge."),
        ],
        "permissions": "Server Admin or Bot Owner",
        "example": "/setclass day:monday time_slot:09:00-10:00 subject:Physics batch:1 room:LH-101 faculty:Dr. Smith",
    },
    "delclass": {
        "category": "Admin",
        "syntax": "/delclass <day> <start> [batch]",
        "aliases": ["removeclass", "rmclass"],
        "description": "Removes a scheduled class by day, start time, and optional batch filter.",
        "arguments": [
            ("day", "Required", "Day of the week (e.g. monday)."),
            ("start", "Required", "Start time of the class in HH:MM 24h format (e.g. 09:00)."),
            ("batch", "Optional", "Batch filter to target specific batch or 'all'."),
        ],
        "permissions": "Server Admin or Bot Owner",
        "example": "/delclass day:monday start:09:00 batch:1",
    },
    "linkuser": {
        "category": "Admin",
        "syntax": "/linkuser <member> <batch> [name] [roll_no]",
        "aliases": ["adminlink", "setstudent"],
        "description": "Administrator command to manually link or correct any student's batch, name, and roll number.",
        "arguments": [
            ("member", "Required", "Target Discord student."),
            ("batch", "Required", "Assigned batch number."),
            ("name", "Optional", "Student full real name."),
            ("roll_no", "Optional", "Student college roll number."),
        ],
        "permissions": "Server Admin or Bot Owner",
        "example": "/linkuser member:@Student batch:1 name:Jane Doe roll_no:21CS099",
    },
    "unlinkuser": {
        "category": "Admin",
        "syntax": "/unlinkuser <member>",
        "aliases": ["adminunlink", "delstudent"],
        "description": "Administrator command to remove another student's account link and profile information.",
        "arguments": [
            ("member", "Required", "Discord student to unlink."),
        ],
        "permissions": "Server Admin or Bot Owner",
        "example": "/unlinkuser member:@Student",
    },
    "students": {
        "category": "Admin",
        "syntax": "/students [batch]",
        "aliases": ["batchlist", "roster"],
        "description": "Displays the complete directory of registered students with roll numbers and batches.",
        "arguments": [
            ("batch", "Optional", "Filter by specific batch number. If omitted, lists all linked students across batches."),
        ],
        "permissions": "Server Admin or Bot Owner",
        "example": "/students batch:1",
    },
    "setprefix": {
        "category": "Admin",
        "syntax": "/setprefix <prefix>",
        "aliases": ["prefix"],
        "description": "Changes the bot text command prefix for this server or globally (e.g. !, ?, .).",
        "arguments": [
            ("prefix", "Required", "New symbol or prefix string (1 to 5 characters)."),
        ],
        "permissions": "Server Admin or Bot Owner",
        "example": "/setprefix prefix:!",
    },
    "sync": {
        "category": "Admin",
        "syntax": "/sync",
        "aliases": ["syncslash"],
        "description": "Syncs slash commands with Discord. Run this after updating the bot to refresh all application commands.",
        "arguments": [],
        "permissions": "Bot Owner",
        "example": "!sync",
    },
    "reload": {
        "category": "Admin",
        "syntax": "/reload [cog_path]",
        "aliases": ["refresh"],
        "description": "Hot-reloads one or all bot extension modules (cogs) without restarting the bot process.",
        "arguments": [
            ("cog_path", "Optional", "Specific cog to reload (e.g. cogs.attendance). If omitted, reloads all active cogs."),
        ],
        "permissions": "Bot Owner",
        "example": "!reload cog_path:cogs.attendance",
    },

    # Category 5: General & Utilities
    "help": {
        "category": "General",
        "syntax": "/help [command]",
        "aliases": ["commands", "guide", "h"],
        "description": "Opens this interactive help menu or provides detailed technical syntax for any individual command.",
        "arguments": [
            ("command", "Optional", "Name of the command to inspect (e.g. timetable, setclass, attendance)."),
        ],
        "permissions": "Everyone",
        "example": "/help command:attendance",
    },
}

CATEGORIES = [
    ("Overview", "Main index, quick navigation, and feature breakdown"),
    ("Timetable", "Class routines, upcoming lectures, and weekly timetables"),
    ("Linking", "Student profiles, roll numbers, and batch management"),
    ("Attendance", "Faculty marking portal, student stats, bunk rules, and export"),
    ("Admin", "Schedule modification, roster controls, and bot configuration"),
    ("FAQ", "Frequently asked questions, format guides, and tips"),
]


# ---------------------------------------------------------------------------
# Embed Builders (Strictly Emoji-Free)
# ---------------------------------------------------------------------------

def build_overview_embed(prefix: str, user: Optional[discord.User | discord.Member] = None) -> discord.Embed:
    """Builds the primary emoji-free help desk overview embed."""
    embed = discord.Embed(
        title="Bot Documentation & Command Reference",
        description=(
            "Welcome to the Academic Timetable and Attendance Management System.\n"
            "This bot provides batch-aware schedule routing, interactive faculty attendance marking, "
            "and student attendance threshold tracking.\n\n"
            "Use the dropdown selector below to browse documentation by category, "
            f"or type `{prefix}help <command>` for in-depth parameter specifications."
        ),
        color=COLOR_ACCENT,
    )

    embed.add_field(
        name="Category Index",
        value=(
            "• **Timetable & Classes** (3 commands)\n"
            "  Routines, upcoming lecture alerts, weekly overviews.\n\n"
            "• **Student Profile & Linking** (4 commands)\n"
            "  Personal batch assignment, student IDs, roster profiles.\n\n"
            "• **Attendance & Faculty** (5 commands)\n"
            "  Interactive marking, threshold tracking, safe-bunk math, CSV export.\n\n"
            "• **Administration & Setup** (8 commands)\n"
            "  Class management, roster edits, prefix and slash command sync.\n\n"
            "• **General Utilities** (1 command)\n"
            "  Command lookup and system manual."
        ),
        inline=False,
    )

    embed.add_field(
        name="Quick Start Guide",
        value=(
            "1. Link your profile: `/link batch:1 name:YourName roll_no:CS01`\n"
            "2. Check today's routine: `/timetable`\n"
            "3. View your next lecture: `/next`\n"
            "4. Track attendance status: `/myattendance`"
        ),
        inline=False,
    )

    embed.add_field(
        name="Execution Methods",
        value=(
            f"• **Slash Commands:** Type `/` and select any command from the Discord UI.\n"
            f"• **Message Commands:** Type `{prefix}<command>` directly in server chat channels."
        ),
        inline=False,
    )

    embed.set_footer(text=f"Total: 21 Commands | Active Prefix: {prefix} | Page: Overview")
    return embed


def build_timetable_embed(prefix: str) -> discord.Embed:
    """Builds the timetable category help embed."""
    embed = discord.Embed(
        title="Documentation: Timetable & Classes",
        description=(
            "Commands for viewing daily routines, upcoming classes, and full week schedules.\n"
            "All commands automatically tailor responses to your linked batch unless explicitly overridden."
        ),
        color=COLOR_DEFAULT,
    )

    embed.add_field(
        name=f"`{prefix}timetable` [day] [batch]",
        value=(
            "Displays the schedule for a specific day or today by default.\n"
            "• Aliases: `tt`\n"
            "• Examples: `/timetable`, `/timetable day:monday`, `/timetable day:fri batch:2`"
        ),
        inline=False,
    )

    embed.add_field(
        name=f"`{prefix}next` [batch]",
        value=(
            "Shows the next class taking place today with a live countdown and room location.\n"
            "• Aliases: `upcoming`\n"
            "• Example: `/next`"
        ),
        inline=False,
    )

    embed.add_field(
        name=f"`{prefix}week` [batch]",
        value=(
            "Displays the consolidated Monday through Saturday timetable for the specified batch.\n"
            "• Aliases: `fullweek`, `weekly`\n"
            "• Examples: `/week`, `/week batch:1`"
        ),
        inline=False,
    )

    embed.set_footer(text=f"Category: Timetable (3 commands) | Prefix: {prefix}")
    return embed


def build_linking_embed(prefix: str) -> discord.Embed:
    """Builds the student profile and account linking category help embed."""
    embed = discord.Embed(
        title="Documentation: Student Profile & Linking",
        description=(
            "Commands to associate your Discord identity with your university batch and roll number.\n"
            "Linking unlocks personalized schedules and enables attendance participation."
        ),
        color=COLOR_DEFAULT,
    )

    embed.add_field(
        name=f"`{prefix}link` [batch] [name] [roll_no]",
        value=(
            "Links your Discord account to your batch, name, and university roll number.\n"
            "• Aliases: `linkme`, `register`\n"
            "• Example: `/link batch:1 name:Alex Smith roll_no:21CS042`"
        ),
        inline=False,
    )

    embed.add_field(
        name=f"`{prefix}profile` [member]",
        value=(
            "Inspects the linked student profile and attendance statistics for yourself or a classmate.\n"
            "• Aliases: `me`, `whois`\n"
            "• Examples: `/profile`, `/profile member:@Student`"
        ),
        inline=False,
    )

    embed.add_field(
        name=f"`{prefix}mybatch` [batch]",
        value=(
            "Checks or updates your current active batch without resetting your registered name and roll number.\n"
            "• Aliases: `setbatch`, `switchbatch`\n"
            "• Examples: `/mybatch`, `/mybatch batch:2`"
        ),
        inline=False,
    )

    embed.add_field(
        name=f"`{prefix}unlink`",
        value=(
            "Removes your student identity and batch association from the database.\n"
            "• Aliases: `unlinkme`\n"
            "• Example: `/unlink`"
        ),
        inline=False,
    )

    embed.set_footer(text=f"Category: Linking (4 commands) | Prefix: {prefix}")
    return embed


def build_attendance_embed(prefix: str) -> discord.Embed:
    """Builds the attendance and faculty management category help embed."""
    embed = discord.Embed(
        title="Documentation: Attendance & Faculty Management",
        description=(
            "Complete attendance tracking system with faculty authorization, schedule locking, "
            "interactive roll-call rosters, and student threshold calculation (default 75%)."
        ),
        color=COLOR_DEFAULT,
    )

    embed.add_field(
        name=f"`{prefix}attendance` <take | summary | export>",
        value=(
            "Master attendance management suite:\n"
            "• `take <batch> <subject> [day]`: Opens interactive marking menu for authorized faculty.\n"
            "• `summary <batch> [subject]`: Batch-wide attendance analytics with at-risk highlighting.\n"
            "• `export <batch>`: Generates and uploads a downloadable `.csv` spreadsheet."
        ),
        inline=False,
    )

    embed.add_field(
        name=f"`{prefix}myattendance` [subject]",
        value=(
            "Personal student attendance dashboard.\n"
            "• Calculates percentage, total present, and total conducted.\n"
            "• Safe-bunk calculation: Shows exactly how many classes you can miss while staying >= 75%.\n"
            "• Recovery calculation: Shows consecutive classes needed to return above 75%.\n"
            "• Aliases: `attendance_me`, `bunk`\n"
            "• Example: `/myattendance`"
        ),
        inline=False,
    )

    embed.add_field(
        name=f"`{prefix}setlecturer` <member> <subject> [batch]",
        value=(
            "Grants attendance recording permission to a faculty member for a subject and optional batch.\n"
            "• Aliases: `addlecturer`, `authfaculty`\n"
            "• Requires: Administrator or Bot Owner\n"
            "• Example: `/setlecturer member:@Prof_Davis subject:Data Structures batch:1`"
        ),
        inline=False,
    )

    embed.add_field(
        name=f"`{prefix}dellecturer` <member>",
        value=(
            "Revokes attendance permissions from the specified faculty member.\n"
            "• Aliases: `removelecturer`\n"
            "• Requires: Administrator or Bot Owner"
        ),
        inline=False,
    )

    embed.add_field(
        name=f"`{prefix}lecturers`",
        value=(
            "Lists all authorized faculty members, assigned subjects, and batch permissions.\n"
            "• Aliases: `faculty`, `faculty_list`\n"
            "• Example: `/lecturers`"
        ),
        inline=False,
    )

    embed.set_footer(text=f"Category: Attendance (5 commands) | Prefix: {prefix}")
    return embed


def build_admin_embed(prefix: str) -> discord.Embed:
    """Builds the administration and setup category help embed."""
    embed = discord.Embed(
        title="Documentation: Administration & Bot Setup",
        description=(
            "Commands for server administrators and bot owners to manage timetable slots, "
            "manage student rosters, change prefixes, and sync slash commands."
        ),
        color=COLOR_ADMIN,
    )

    embed.add_field(
        name=f"`{prefix}setclass` <day> <time_slot> <subject> [batch] [room] [faculty]",
        value=(
            "Adds or updates a class entry in `timetable.json`.\n"
            "• `time_slot`: 24-hour format `HH:MM-HH:MM` (e.g. `09:00-10:00`)\n"
            "• `batch`: Batch ID or `all` for joint lectures.\n"
            "• Example: `/setclass day:monday time_slot:11:00-12:00 subject:OS batch:1 room:LH-201 faculty:Dr. Alan`"
        ),
        inline=False,
    )

    embed.add_field(
        name=f"`{prefix}delclass` <day> <start> [batch]",
        value=(
            "Deletes a scheduled class matching the specified day, start time, and batch.\n"
            "• Example: `/delclass day:monday start:11:00 batch:1`"
        ),
        inline=False,
    )

    embed.add_field(
        name=f"`{prefix}linkuser` <member> <batch> [name] [roll_no]",
        value=(
            "Manually links or updates another user's student credentials.\n"
            "• Example: `/linkuser member:@Student batch:2 name:Sarah roll_no:21CS088`"
        ),
        inline=False,
    )

    embed.add_field(
        name=f"`{prefix}unlinkuser` <member>",
        value=(
            "Unlinks another member's profile from the database.\n"
            "• Example: `/unlinkuser member:@Student`"
        ),
        inline=False,
    )

    embed.add_field(
        name=f"`{prefix}students` [batch]",
        value=(
            "Displays the registered student directory with names, roll numbers, and batches.\n"
            "• Aliases: `batchlist`, `roster`\n"
            "• Example: `/students batch:1`"
        ),
        inline=False,
    )

    embed.add_field(
        name=f"`{prefix}setprefix` <prefix>",
        value=(
            "Configures the bot command prefix string for prefix-based messages.\n"
            "• Example: `/setprefix prefix:!`"
        ),
        inline=False,
    )

    embed.add_field(
        name=f"`{prefix}sync`",
        value=(
            "Forces immediate synchronization of all slash commands with the Discord API.\n"
            "• Requires: Bot Owner\n"
            "• Example: `!sync`"
        ),
        inline=False,
    )

    embed.add_field(
        name=f"`{prefix}reload` [cog_path]",
        value=(
            "Hot-reloads code modules without terminating the Python bot runtime.\n"
            "• Requires: Bot Owner\n"
            "• Example: `!reload cog_path:cogs.attendance`"
        ),
        inline=False,
    )

    embed.set_footer(text=f"Category: Administration (8 commands) | Prefix: {prefix}")
    return embed


def build_faq_embed(prefix: str) -> discord.Embed:
    """Builds the frequently asked questions and technical tips embed."""
    embed = discord.Embed(
        title="Documentation: FAQ & Technical Tips",
        description="Helpful guidelines for schedule management, time formats, and attendance rules.",
        color=COLOR_DEFAULT,
    )

    embed.add_field(
        name="Time Formatting Standard",
        value=(
            "All timetable slots require strict 24-hour time formatting: `HH:MM-HH:MM`.\n"
            "• Valid: `09:00-10:00`, `14:30-16:00`\n"
            "• Invalid: `9:00am-10:00am`, `2:00-3:00`"
        ),
        inline=False,
    )

    embed.add_field(
        name="Batch Routing Rules",
        value=(
            "• Classes designated as batch `all` are automatically visible to every batch.\n"
            "• If you are linked to batch `1`, `/timetable` returns both `all` and `1` entries.\n"
            "• You can always view another batch on the fly by appending the `batch` argument."
        ),
        inline=False,
    )

    embed.add_field(
        name="Faculty Attendance Time-Window",
        value=(
            "By default, attendance can only be recorded during the scheduled lecture window:\n"
            "• Window opens 5 minutes before scheduled start time.\n"
            "• Window closes 15 minutes after scheduled end time.\n"
            "• Attempting to record outside this window sends an interactive approval request to server administrators."
        ),
        inline=False,
    )

    embed.add_field(
        name="Attendance Threshold (75%)",
        value=(
            "Students falling below 75% attendance are automatically flagged in red on `/myattendance` "
            "and in batch summaries. The bot calculates the exact number of consecutive sessions required to recover."
        ),
        inline=False,
    )

    embed.set_footer(text=f"Academic Timetable System | Prefix: {prefix}")
    return embed


def build_command_embed(name: str, prefix: str) -> discord.Embed:
    """Builds a detailed single-command technical reference card."""
    cmd_info = COMMAND_REGISTRY.get(name.lower().strip())
    if not cmd_info:
        embed = discord.Embed(
            title="Command Not Found",
            description=f"No command matching `{name}` was located in the registry.\nType `{prefix}help` to review all available commands.",
            color=COLOR_WARNING,
        )
        return embed

    embed = discord.Embed(
        title=f"Command Reference: {name}",
        description=cmd_info["description"],
        color=COLOR_ACCENT,
    )

    # Syntax block
    embed.add_field(
        name="Syntax",
        value=f"```{cmd_info['syntax']}```",
        inline=False,
    )

    # Aliases
    if cmd_info.get("aliases"):
        aliases_str = ", ".join(f"`{a}`" for a in cmd_info["aliases"])
        embed.add_field(name="Aliases", value=aliases_str, inline=True)

    # Required Permissions
    embed.add_field(
        name="Permission Level",
        value=cmd_info.get("permissions", "Everyone"),
        inline=True,
    )

    # Category
    embed.add_field(
        name="Category",
        value=cmd_info.get("category", "General"),
        inline=True,
    )

    # Arguments breakdown
    args = cmd_info.get("arguments", [])
    if args:
        lines = []
        for arg_name, req_type, arg_desc in args:
            lines.append(f"• `{arg_name}` ({req_type}): {arg_desc}")
        embed.add_field(name="Arguments", value="\n".join(lines), inline=False)
    else:
        embed.add_field(name="Arguments", value="None (invoked without parameters)", inline=False)

    # Usage Example
    if cmd_info.get("example"):
        embed.add_field(name="Example Usage", value=f"`{cmd_info['example']}`", inline=False)

    embed.set_footer(text=f"Syntax legend: <required> [optional] | Prefix: {prefix}")
    return embed


def build_category_embed(category_key: str, prefix: str, user: Optional[discord.User | discord.Member] = None) -> discord.Embed:
    """Router to return the appropriate category embed."""
    key = category_key.lower()
    if key in ("overview", "home"):
        return build_overview_embed(prefix, user)
    elif key in ("timetable", "classes"):
        return build_timetable_embed(prefix)
    elif key in ("linking", "profile"):
        return build_linking_embed(prefix)
    elif key in ("attendance", "faculty"):
        return build_attendance_embed(prefix)
    elif key in ("admin", "administration"):
        return build_admin_embed(prefix)
    elif key in ("faq", "tips"):
        return build_faq_embed(prefix)
    return build_overview_embed(prefix, user)


# ---------------------------------------------------------------------------
# UI Components: Clean Dropdown Menu (Strictly Emoji-Free)
# ---------------------------------------------------------------------------

_HAS_UI = False
try:
    if hasattr(discord, "ui") and hasattr(discord.ui, "View") and hasattr(discord.ui, "Select"):
        _HAS_UI = True
except Exception:
    _HAS_UI = False

if _HAS_UI:
    class HelpSelect(discord.ui.Select):
        """Clean category selection dropdown menu with zero emojis."""

        def __init__(self, prefix: str, invoker_id: int):
            self.prefix = prefix
            self.invoker_id = invoker_id

            options = [
                discord.SelectOption(
                    label="Overview",
                    value="overview",
                    description="Main index, quick navigation, and feature breakdown",
                    default=True,
                ),
                discord.SelectOption(
                    label="Timetable & Classes",
                    value="timetable",
                    description="Class routines, upcoming lectures, and weekly timetables",
                ),
                discord.SelectOption(
                    label="Student Profile & Linking",
                    value="linking",
                    description="Student profiles, roll numbers, and batch management",
                ),
                discord.SelectOption(
                    label="Attendance & Faculty",
                    value="attendance",
                    description="Faculty marking portal, student stats, bunk rules, and export",
                ),
                discord.SelectOption(
                    label="Admin & Bot Setup",
                    value="admin",
                    description="Schedule modification, roster controls, and bot configuration",
                ),
                discord.SelectOption(
                    label="FAQ & Quick Tips",
                    value="faq",
                    description="Frequently asked questions, format guides, and tips",
                ),
            ]
            super().__init__(
                placeholder="Select a category to view documentation...",
                min_values=1,
                max_values=1,
                options=options,
            )

        async def callback(self, interaction: discord.Interaction):
            # Allow any user to inspect if invoker check is disabled, or lock to invoker
            if interaction.user.id != self.invoker_id:
                await interaction.response.send_message(
                    "This menu is linked to the original user. Run /help to open your own interactive menu.",
                    ephemeral=True,
                )
                return

            selected_value = self.values[0]

            # Update the default flag on options for visual feedback
            for opt in self.options:
                opt.default = (opt.value == selected_value)

            embed = build_category_embed(selected_value, self.prefix, interaction.user)
            await interaction.response.edit_message(embed=embed, view=self.view)


    class HelpPaginationView(discord.ui.View):
        """View holding the category select menu and navigation buttons."""

        def __init__(self, prefix: str, invoker_id: int, timeout: float = 180.0):
            super().__init__(timeout=timeout)
            self.prefix = prefix
            self.invoker_id = invoker_id
            self.message: Optional[discord.Message] = None

            # Add select dropdown
            self.select_menu = HelpSelect(prefix=prefix, invoker_id=invoker_id)
            self.add_item(self.select_menu)

        async def on_timeout(self):
            """Disables controls when the view expires."""
            for child in self.children:
                if hasattr(child, "disabled"):
                    child.disabled = True
            if self.message:
                try:
                    await self.message.edit(view=self)
                except discord.HTTPException:
                    pass
else:
    class HelpPaginationView:  # type: ignore[no-redef]
        def __init__(self, *args, **kwargs):
            pass


# ---------------------------------------------------------------------------
# Help Cog Implementation
# ---------------------------------------------------------------------------

class HelpCog(commands.Cog, name="Help"):
    """Comprehensive, emoji-free help documentation system."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def _resolve_prefix(self, ctx: commands.Context) -> str:
        """Determines active prefix for the context."""
        prefix = getattr(self.bot, "default_prefix", "!")
        if ctx.prefix and isinstance(ctx.prefix, str) and not ctx.prefix.startswith("/"):
            prefix = ctx.prefix
        return prefix

    @commands.hybrid_command(
        name="help",
        aliases=["commands", "guide", "h"],
        description="Comprehensive documentation for all timetable, attendance, and admin commands.",
    )
    async def help_command(self, ctx: commands.Context, *, command: Optional[str] = None):
        """Displays documentation overview or a deep-dive card for a specific command."""
        prefix = self._resolve_prefix(ctx)

        # Case 1: Specific command lookup
        if command:
            cmd_clean = command.strip().lower()
            if cmd_clean.startswith(prefix):
                cmd_clean = cmd_clean[len(prefix):]
            if cmd_clean.startswith("/"):
                cmd_clean = cmd_clean[1:]

            # Check registry or aliases
            matched_key = None
            if cmd_clean in COMMAND_REGISTRY:
                matched_key = cmd_clean
            else:
                for k, v in COMMAND_REGISTRY.items():
                    if cmd_clean in v.get("aliases", []):
                        matched_key = k
                        break

            if matched_key:
                embed = build_command_embed(matched_key, prefix)
                await ctx.reply(embed=embed, mention_author=False)
                return

            # Check if it matches a category directly
            for cat_key, _ in CATEGORIES:
                if cmd_clean == cat_key.lower():
                    embed = build_category_embed(cat_key, prefix, ctx.author)
                    await ctx.reply(embed=embed, mention_author=False)
                    return

            # Unknown command error embed
            embed = discord.Embed(
                title="Unknown Command",
                description=(
                    f"No command matching `{command}` was found.\n"
                    f"Use `{prefix}help` to view all 21 available commands."
                ),
                color=COLOR_WARNING,
            )
            await ctx.reply(embed=embed, mention_author=False)
            return

        # Case 2: Main interactive help menu
        overview_embed = build_overview_embed(prefix, ctx.author)
        if _HAS_UI:
            try:
                view = HelpPaginationView(prefix=prefix, invoker_id=ctx.author.id)
                msg = await ctx.reply(embed=overview_embed, view=view, mention_author=False)
                if isinstance(msg, discord.Message):
                    view.message = msg
                return
            except Exception as e:
                logger.warning("Could not send interactive view, falling back to basic embed: %s", e)

        await ctx.reply(embed=overview_embed, mention_author=False)


async def setup(bot: commands.Bot):
    await bot.add_cog(HelpCog(bot))
