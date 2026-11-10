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
    "freerooms": {
        "category": "Timetable",
        "syntax": "/freerooms [day] [time] [block]",
        "aliases": ["emptyrooms", "freeroomsnow"],
        "description": "Scans all university classrooms to find empty, available rooms by day, hour, or block.",
        "arguments": [
            ("day", "Optional", "Day of the week (e.g. today, Friday). Defaults to today."),
            ("time", "Optional", "Time slot (e.g. 11:30, 2:30 PM). Defaults to current hour."),
            ("block", "Optional", "Filter by specific block (e.g. 7, 8, 10, 11, BL8)."),
        ],
        "permissions": "Everyone",
        "example": "/freerooms day:Friday time:11:30 block:BL8",
    },
    "findbatch": {
        "category": "Timetable",
        "syntax": "/findbatch <query>",
        "aliases": ["searchbatch", "batches"],
        "description": "Searches all 428 university batches by department, degree, branch, or section number.",
        "arguments": [
            ("query", "Required", "Search term, e.g. CSE, AI, 16, BTech, ECE."),
        ],
        "permissions": "Everyone",
        "example": "/findbatch query:CSE",
    },
    "batchinfo": {
        "category": "Timetable",
        "syntax": "/batchinfo <batch>",
        "aliases": [],
        "description": "Displays subjects, faculties, and weekly classes for any batch section.",
        "arguments": [
            ("batch", "Required", "Exact batch identifier (e.g. 26CAIBTAIB16)."),
        ],
        "permissions": "Everyone",
        "example": "/batchinfo batch:26CAIBTAIB16",
    },
    "faculty": {
        "category": "Timetable",
        "syntax": "/faculty <name>",
        "aliases": ["lecturer", "findfaculty", "prof"],
        "description": "Searches for faculty members across all batches to view where and what they teach.",
        "arguments": [
            ("name", "Required", "Faculty name or keyword (e.g. Ashim, Dasari)."),
        ],
        "permissions": "Everyone",
        "example": "/faculty name:Ashim",
    },
    "degrees": {
        "category": "Timetable",
        "syntax": "/degrees",
        "aliases": [],
        "description": "Lists all 30 university degrees and programs currently supported in the system.",
        "arguments": [],
        "permissions": "Everyone",
        "example": "/degrees",
    },

    # Category 2: Student Profile & Linking
    "register": {
        "category": "Linking",
        "syntax": "/register [degree] [year] [batch] [name] [roll_no]",
        "aliases": ["setup", "onboard"],
        "description": "Onboarding wizard that registers your department, year, and batch to unlock your timetable.",
        "arguments": [
            ("degree", "Optional", "Your department / degree (e.g. CSE, ECE, BTech, BCA)."),
            ("year", "Optional", "Academic year (1, 2, 3, or 4)."),
            ("batch", "Optional", "Section number or batch code (e.g. 16, 26CAIBTAIB16)."),
            ("name", "Optional", "Your full student name."),
            ("roll_no", "Optional", "Your university roll number."),
        ],
        "permissions": "Everyone",
        "example": "/register degree:CSE year:1 batch:16",
    },
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
        "aliases": ["setbatch"],
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
    "setadmin": {
        "category": "Admin",
        "syntax": "/setadmin <member>",
        "aliases": ["addadmin", "makeadmin"],
        "description": "Grant a user full administrator privileges with identical powers to the bot owner.",
        "arguments": [
            ("member", "Required", "Server member to promote to bot administrator."),
        ],
        "permissions": "Server Admin or Bot Owner",
        "example": "/setadmin member:@CoAdmin",
    },
    "deladmin": {
        "category": "Admin",
        "syntax": "/deladmin <member>",
        "aliases": ["removeadmin", "rmadmin"],
        "description": "Revoke administrator privileges from an authorized user.",
        "arguments": [
            ("member", "Required", "Administrator to demote."),
        ],
        "permissions": "Server Admin or Bot Owner",
        "example": "/deladmin member:@CoAdmin",
    },
    "admins": {
        "category": "Admin",
        "syntax": "/admins",
        "aliases": ["adminlist"],
        "description": "Lists the primary owner and all authorized bot administrators.",
        "arguments": [],
        "permissions": "Server Admin or Bot Owner",
        "example": "/admins",
    },
    "setcr": {
        "category": "Admin",
        "syntax": "/setcr <member> <batch>",
        "aliases": ["addcr", "assigncr"],
        "description": "Authorize a member as Class Representative for a specific batch/class to manage timetable slots and student rosters.",
        "arguments": [
            ("member", "Required", "Server member to appoint as Class Representative."),
            ("batch", "Required", "Batch or class code they represent (e.g. 26CAIBTAIB16 or 1)."),
        ],
        "permissions": "Server Admin or Bot Owner",
        "example": "/setcr member:@Student batch:26CAIBTAIB16",
    },
    "delcr": {
        "category": "Admin",
        "syntax": "/delcr <member> [batch]",
        "aliases": ["removecr", "rmcr"],
        "description": "Revoke Class Representative authorization from a member.",
        "arguments": [
            ("member", "Required", "Class Representative to revoke."),
            ("batch", "Optional", "Specific batch to unassign if representing multiple classes."),
        ],
        "permissions": "Server Admin or Bot Owner",
        "example": "/delcr member:@Student",
    },
    "representatives": {
        "category": "Admin",
        "syntax": "/representatives [batch]",
        "aliases": ["crlist", "crs"],
        "description": "Displays the complete directory of authorized Class Representatives and their assigned classes.",
        "arguments": [
            ("batch", "Optional", "Filter by specific batch code."),
        ],
        "permissions": "Everyone",
        "example": "/representatives batch:26CAIBTAIB16",
    },
    "setclass": {
        "category": "Admin",
        "syntax": "/setclass <day> <time_slot> <subject> [batch] [room] [faculty]",
        "aliases": ["addclass"],
        "description": "Adds or overwrites a class slot in the schedule. Automatically sorts and persists changes to timetable.json.",
        "arguments": [
            ("day", "Required", "Day of the week (e.g. Monday, wed)."),
            ("time_slot", "Required", "Time window in HH:MM-HH:MM 24h format (e.g. 09:00-10:00)."),
            ("subject", "Required", "Title or code of the course."),
            ("batch", "Optional", "Specific batch (e.g. 1, 2) or 'all' for joint lectures. Defaults to 'all' for Admins or CR class."),
            ("room", "Optional", "Assigned lecture hall, classroom, or lab number (e.g. Room 402, CS-Lab-A)."),
            ("faculty", "Optional", "Name or designation of the instructor in charge."),
        ],
        "permissions": "Administrator or Class Representative",
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
        "permissions": "Administrator or Class Representative",
        "example": "/delclass day:monday start:09:00 batch:1",
    },
    "reschedule": {
        "category": "Admin",
        "syntax": "/reschedule <date> <time_slot> <subject> [batch] [room] [faculty] [note]",
        "aliases": ["override", "setoverride"],
        "description": "Reschedules or substitutes a class for a specific calendar date and hour (one-time override).",
        "arguments": [
            ("date", "Required", "Target date ('today', 'tomorrow', or 'YYYY-MM-DD')."),
            ("time_slot", "Required", "Time slot (e.g. 10:30-11:30 or 10:30)."),
            ("subject", "Required", "Course name, or FREE / CANCELLED."),
            ("batch", "Optional", "Batch code (e.g. 26CAIBTAIB16 or ALL). Defaults to ALL for Admin or CR class."),
            ("room", "Optional", "Assigned lecture hall or room."),
            ("faculty", "Optional", "Instructor name."),
            ("note", "Optional", "Reason or explanation (e.g. 'Makeup lecture')."),
        ],
        "permissions": "Administrator or Class Representative",
        "example": "/reschedule date:today time_slot:10:30-11:30 subject:Data Structures batch:26CAIBTAIB16 room:7004 faculty:Dr. Sundheep note:Makeup lab",
    },
    "cancelclass": {
        "category": "Admin",
        "syntax": "/cancelclass <date> <time_slot> [batch] [reason]",
        "aliases": [],
        "description": "Marks a class as cancelled for a specific calendar date and hour.",
        "arguments": [
            ("date", "Required", "Target date ('today', 'tomorrow', or 'YYYY-MM-DD')."),
            ("time_slot", "Required", "Start time or span (e.g. 09:30 or 09:30-10:30)."),
            ("batch", "Optional", "Batch code (e.g. 26CAIBTAIB16 or ALL). Defaults to ALL for Admin or CR class."),
            ("reason", "Optional", "Cancellation reason shown to students."),
        ],
        "permissions": "Administrator or Class Representative",
        "example": "/cancelclass date:today time_slot:09:30 batch:26CAIBTAIB16 reason:Faculty on leave",
    },
    "deloverride": {
        "category": "Admin",
        "syntax": "/deloverride <date> <start> [batch]",
        "aliases": [],
        "description": "Removes a date-specific override and restores the original recurring weekly timetable.",
        "arguments": [
            ("date", "Required", "Target date ('today', 'tomorrow', or 'YYYY-MM-DD')."),
            ("start", "Required", "Start time in HH:MM format (e.g. 09:30)."),
            ("batch", "Optional", "Batch code or ALL."),
        ],
        "permissions": "Administrator or Class Representative",
        "example": "/deloverride date:today start:09:30 batch:26CAIBTAIB16",
    },
    "overrides": {
        "category": "Admin",
        "syntax": "/overrides",
        "aliases": [],
        "description": "Displays all active date-specific timetable overrides and substitutions.",
        "arguments": [],
        "permissions": "Everyone",
        "example": "/overrides",
    },
    "linkuser": {
        "category": "Admin",
        "syntax": "/linkuser <member> <batch> [name] [roll_no]",
        "aliases": ["adminlink", "setstudent"],
        "description": "Administrator and Class Representative command to manually link or correct student batch, name, and roll number.",
        "arguments": [
            ("member", "Required", "Target Discord student."),
            ("batch", "Required", "Assigned batch number."),
            ("name", "Optional", "Student full real name."),
            ("roll_no", "Optional", "Student college roll number."),
        ],
        "permissions": "Administrator or Class Representative",
        "example": "/linkuser member:@Student batch:1 name:Jane Doe roll_no:21CS099",
    },
    "unlinkuser": {
        "category": "Admin",
        "syntax": "/unlinkuser <member>",
        "aliases": ["adminunlink", "delstudent"],
        "description": "Administrator and Class Representative command to remove a student's account link and profile information.",
        "arguments": [
            ("member", "Required", "Discord student to unlink."),
        ],
        "permissions": "Administrator or Class Representative",
        "example": "/unlinkuser member:@Student",
    },
    "students": {
        "category": "Admin",
        "syntax": "/students [batch]",
        "aliases": ["batchlist", "roster"],
        "description": "Displays the complete directory of registered students with roll numbers and batches.",
        "arguments": [
            ("batch", "Optional", "Filter by specific batch number. Defaults to CR class or all for Admins."),
        ],
        "permissions": "Administrator or Class Representative",
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
    "loadbatch": {
        "category": "Admin",
        "syntax": "/loadbatch <batch>",
        "aliases": ["activatebatch", "importbatch"],
        "description": "Owner command to activate any SRUX batch schedule as the server's default timetable.",
        "arguments": [
            ("batch", "Required", "Batch code to load (e.g. 26CAIBTAIB16)."),
        ],
        "permissions": "Bot Owner",
        "example": "!loadbatch batch:26CAIBTAIB16",
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
        title="Bot Documentation and Command Reference",
        description=(
            "Academic Timetable, Room Directory, and Attendance Management System.\n"
            "This bot provides batch-aware schedule routing, interactive faculty attendance marking, "
            "campus-wide room availability discovery, and student threshold tracking.\n\n"
            "Select a category from the dropdown menu below or type `"
            f"{prefix}help <command>` for technical parameter specifications."
        ),
        color=COLOR_ACCENT,
    )

    embed.add_field(
        name="Category Index",
        value=(
            "• **Timetable and Campus Rooms** (8 commands)\n"
            "  Routines, upcoming lectures, weekly overviews, room availability, batch and faculty lookup.\n\n"
            "• **Student Profile and Registration** (5 commands)\n"
            "  Interactive registration wizard, batch assignment, student IDs, roster profiles.\n\n"
            "• **Attendance and Faculty** (5 commands)\n"
            "  Interactive marking, threshold tracking, safe-miss calculation, CSV export.\n\n"
            "• **Administration and Schedule Management** (19 commands)\n"
            "  Recurring class setup, date-specific overrides, co-admins, class representatives, roster controls.\n\n"
            "• **General Utilities** (1 command)\n"
            "  Command lookup and system manual."
        ),
        inline=False,
    )

    embed.add_field(
        name="Quick Start for New Students",
        value=(
            f"1. Register your batch: `{prefix}register` (3-step prompt) or `{prefix}register CSE 1 16`\n"
            f"2. View today's classes: `{prefix}timetable` (or `{prefix}today`)\n"
            f"3. Check your ongoing/next class: `{prefix}next`\n"
            f"4. View full week routine: `{prefix}week`\n"
            f"5. Find empty study rooms during breaks: `{prefix}freerooms`"
        ),
        inline=False,
    )

    embed.add_field(
        name="Quick Start for Faculty",
        value=(
            f"1. Reschedule class for a specific date: `{prefix}reschedule date:today time_slot:10:30-11:30 subject:Math`\n"
            f"2. Cancel class for a date: `{prefix}cancelclass date:today time_slot:09:30 reason:Leave`\n"
            f"3. Mark student attendance: `{prefix}attendance take <batch> <subject>`\n"
            f"4. View active date overrides: `{prefix}overrides`"
        ),
        inline=False,
    )

    embed.add_field(
        name="Execution Methods",
        value=(
            f"• **Slash Commands:** Type `/` and pick from the command list.\n"
            f"• **Text Commands:** Type `{prefix}<command>` directly in the chat channel."
        ),
        inline=False,
    )

    embed.set_footer(text=f"Total: 32 Commands | Active Prefix: {prefix} | Page: Overview")
    return embed


def build_timetable_embed(prefix: str) -> discord.Embed:
    """Builds the timetable category help embed."""
    embed = discord.Embed(
        title="Documentation: Timetable and Campus Rooms",
        description=(
            "Commands for inspecting daily routines, upcoming lectures, weekly overviews, "
            "batch directories, faculty lookup, and campus-wide room availability.\n"
            "All commands automatically tailor responses to your registered batch."
        ),
        color=COLOR_DEFAULT,
    )

    embed.add_field(
        name=f"`{prefix}timetable` [day] [batch]",
        value=(
            "Displays the schedule for a specific day or today by default.\n"
            "• Aliases: `tt`, `day`, `today`\n"
            "• Examples: `/timetable`, `/timetable day:monday`, `/timetable day:fri batch:26CAIBTAIB16`"
        ),
        inline=False,
    )

    embed.add_field(
        name=f"`{prefix}next` [batch]",
        value=(
            "Shows the ongoing class or the next upcoming class with countdown, room, and instructor.\n"
            "• Aliases: `now`, `upnext`, `upcoming`\n"
            "• Example: `/next`"
        ),
        inline=False,
    )

    embed.add_field(
        name=f"`{prefix}week` [batch]",
        value=(
            "Displays the consolidated Monday through Saturday timetable for the specified batch.\n"
            "• Aliases: `weekly`, `schedule`\n"
            "• Examples: `/week`, `/week batch:26CAIBTAIB16`"
        ),
        inline=False,
    )

    embed.add_field(
        name=f"`{prefix}freerooms` [day] [time] [block]",
        value=(
            "Scans all university classrooms to find empty, available study rooms by day, hour, or block.\n"
            "• Aliases: `emptyrooms`, `freeroomsnow`\n"
            "• Examples: `/freerooms`, `/freerooms day:today time:11:30 block:BL8`"
        ),
        inline=False,
    )

    embed.add_field(
        name=f"`{prefix}findbatch` <query>",
        value=(
            "Searches all university batches by department, degree, branch, or section number.\n"
            "• Aliases: `searchbatch`, `batches`\n"
            "• Examples: `/findbatch query:CSE`, `/findbatch query:AI 16`"
        ),
        inline=False,
    )

    embed.add_field(
        name=f"`{prefix}batchinfo` <batch>",
        value=(
            "Displays subjects, instructors, rooms, and weekly class count for any batch.\n"
            "• Example: `/batchinfo batch:26CAIBTAIB16`"
        ),
        inline=False,
    )

    embed.add_field(
        name=f"`{prefix}faculty` <name>",
        value=(
            "Searches for instructors across all university timetables to view assigned courses.\n"
            "• Aliases: `lecturer`, `findfaculty`, `prof`\n"
            "• Example: `/faculty name:Ashim`"
        ),
        inline=False,
    )

    embed.add_field(
        name=f"`{prefix}degrees`",
        value=(
            "Lists all university degree programs currently registered in the database.\n"
            "• Example: `/degrees`"
        ),
        inline=False,
    )

    embed.set_footer(text=f"Category: Timetable (8 commands) | Prefix: {prefix}")
    return embed


def build_linking_embed(prefix: str) -> discord.Embed:
    """Builds the student profile and account linking category help embed."""
    embed = discord.Embed(
        title="Documentation: Student Profile and Registration",
        description=(
            "Commands to configure your academic profile, degree, year, and section.\n"
            "Registration takes only a few seconds and personalizes all timetable queries."
        ),
        color=COLOR_DEFAULT,
    )

    embed.add_field(
        name=f"`{prefix}register` [degree] [year] [batch] [name] [roll_no]",
        value=(
            "Recommended setup method for all new students.\n"
            "• Interactive Wizard: Type `/register` and reply to the 3 prompts.\n"
            "• Direct Setup: `/register degree:CSE year:1 batch:16`\n"
            "• Aliases: `setup`, `onboard`"
        ),
        inline=False,
    )

    embed.add_field(
        name=f"`{prefix}link` [batch] [name] [roll_no]",
        value=(
            "Directly links your Discord account to a batch code, full name, and roll number.\n"
            "• Aliases: `linkme`\n"
            "• Example: `/link batch:26CAIBTAIB16 name:Alex Smith roll_no:26CS042`"
        ),
        inline=False,
    )

    embed.add_field(
        name=f"`{prefix}profile` [member]",
        value=(
            "Inspects the linked student profile, roll number, and attendance statistics.\n"
            "• Aliases: `me`, `whois`\n"
            "• Examples: `/profile`, `/profile member:@Student`"
        ),
        inline=False,
    )

    embed.add_field(
        name=f"`{prefix}mybatch` [batch]",
        value=(
            "Checks or updates your current active batch without resetting your saved name.\n"
            "• Aliases: `setbatch`\n"
            "• Examples: `/mybatch`, `/mybatch batch:26CAIBTAIB16`"
        ),
        inline=False,
    )

    embed.add_field(
        name=f"`{prefix}unlink`",
        value=(
            "Removes your student identity and batch association from the database.\n"
            "• Aliases: `unlinkme`, `clearbatch`\n"
            "• Example: `/unlink`"
        ),
        inline=False,
    )

    embed.set_footer(text=f"Category: Registration and Profile (5 commands) | Prefix: {prefix}")
    return embed


def build_attendance_embed(prefix: str) -> discord.Embed:
    """Builds the attendance and faculty management category help embed."""
    embed = discord.Embed(
        title="Documentation: Attendance and Faculty Management",
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
        title="Documentation: Administration and Schedule Management",
        description=(
            "Controls for administrators and class representatives to manage master schedules, "
            "issue date-specific overrides, assign class representatives, and manage student rosters."
        ),
        color=COLOR_ADMIN,
    )

    embed.add_field(
        name=f"`{prefix}setadmin` / `{prefix}deladmin` / `{prefix}admins`",
        value=(
            "Full administrator delegation commands (same powers as owner).\n"
            "• `/setadmin <member>`: Grant a user full administrator authority.\n"
            "• `/deladmin <member>`: Revoke administrator status from a user.\n"
            "• `/admins`: List all active administrators and the primary bot owner."
        ),
        inline=False,
    )

    embed.add_field(
        name=f"`{prefix}setcr` / `{prefix}delcr` / `{prefix}representatives`",
        value=(
            "Class Representative management commands.\n"
            "• `/setcr <member> <batch>`: Authorize a student as Class Representative for their class.\n"
            "• `/delcr <member> [batch]`: Revoke Class Representative authorization.\n"
            "• `/representatives [batch]`: View the directory of all appointed Class Representatives."
        ),
        inline=False,
    )

    embed.add_field(
        name=f"`{prefix}reschedule` <date> <time_slot> <subject> [batch] [room] [faculty] [note]",
        value=(
            "Reschedules or substitutes a class for a specific calendar date and hour (one-time override).\n"
            "• `date`: `today`, `tomorrow`, `2026-11-06`, or `06-11-2026`.\n"
            "• `time_slot`: `HH:MM-HH:MM` or `HH:MM` (e.g. `10:30-11:30`).\n"
            "• Aliases: `override`, `setoverride`\n"
            "• Example: `/reschedule date:today time_slot:10:30-11:30 subject:Algorithms batch:26CAIBTAIB16 note:Makeup Lab`"
        ),
        inline=False,
    )

    embed.add_field(
        name=f"`{prefix}cancelclass` <date> <time_slot> [batch] [reason]",
        value=(
            "Marks a lecture slot as cancelled for a specific calendar date and hour.\n"
            "• Example: `/cancelclass date:today time_slot:09:30 batch:26CAIBTAIB16 reason:Faculty on leave`"
        ),
        inline=False,
    )

    embed.add_field(
        name=f"`{prefix}overrides`",
        value=(
            "Lists all active date-specific timetable overrides and temporary class adjustments.\n"
            "• Example: `/overrides`"
        ),
        inline=False,
    )

    embed.add_field(
        name=f"`{prefix}deloverride` <date> <start> [batch]",
        value=(
            "Deletes an active date override and restores the original weekly timetable.\n"
            "• Example: `/deloverride date:today start:10:30 batch:26CAIBTAIB16`"
        ),
        inline=False,
    )

    embed.add_field(
        name=f"`{prefix}setclass` <day> <time_slot> <subject> [batch] [room] [faculty]",
        value=(
            "Adds or updates a recurring slot in the weekly timetable (or creates an override if day is a date).\n"
            "• Admins configure global/any batch; Class Representatives configure their assigned section.\n"
            "• Example: `/setclass day:Friday time_slot:10:30-11:30 subject:Calculus batch:26CAIBTAIB16 room:11206`"
        ),
        inline=False,
    )

    embed.add_field(
        name=f"`{prefix}delclass` <day> <start> [batch]",
        value=(
            "Permanently removes a recurring class slot from the weekly schedule.\n"
            "• Example: `/delclass day:Friday start:10:30 batch:26CAIBTAIB16`"
        ),
        inline=False,
    )

    embed.add_field(
        name=f"`{prefix}students` [batch]",
        value=(
            "Displays the registered student directory with names, roll numbers, and batches.\n"
            "• Aliases: `batchlist`, `roster`\n"
            "• Example: `/students batch:26CAIBTAIB16`"
        ),
        inline=False,
    )

    embed.add_field(
        name=f"`{prefix}linkuser` / `{prefix}unlinkuser`",
        value=(
            "Administrative commands to manually link or unlink student accounts.\n"
            "• Example: `/linkuser member:@Student batch:26CAIBTAIB16 name:Jane roll_no:26CS088`"
        ),
        inline=False,
    )

    embed.add_field(
        name=f"`{prefix}loadbatch` <batch>",
        value=(
            "Loads any scraped university batch into the default master schedule (Admin only).\n"
            "• Example: `/loadbatch batch:26CAIBTAIB16`"
        ),
        inline=False,
    )

    embed.add_field(
        name=f"`{prefix}setprefix` / `{prefix}sync` / `{prefix}reload`",
        value=(
            "System configuration commands to update prefix, sync slash commands, or hot-reload cogs (Admin only).\n"
            "• Example: `!sync`, `!reload`"
        ),
        inline=False,
    )

    embed.set_footer(text=f"Category: Administration (19 commands) | Prefix: {prefix}")
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
