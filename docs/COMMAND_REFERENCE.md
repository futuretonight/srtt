# Comprehensive Command Reference

This document provides technical syntax, argument specifications, permission tiers, and usage examples for all 32 commands available in the university academic bot.

---

## 1. Timetable and Campus Room Discovery

### `/timetable`
- **Description:** Displays the class schedule for today or a specified day. Automatically filters to your registered batch.
- **Aliases:** `/today`, `/tt`, `/day`
- **Syntax:** `/timetable [day] [batch]`
- **Arguments:**
  - `day` (Optional): Day of week (`Monday`, `fri`, `today`, `tomorrow`) or calendar date (`2026-11-06`, `06-11-2026`).
  - `batch` (Optional): Batch identifier (e.g. `AI 16`, `Core 35`, `Cyber 59`, `DS 57`, `16`, or full code `26CAIBTAIB16`). Overrides your linked profile.
- **Permissions:** Everyone
- **Example:** `/timetable day:Friday batch:AI 16`

### `/next`
- **Description:** Live countdown display showing the lecture currently in progress and the upcoming class.
- **Aliases:** `/now`, `/upnext`, `/upcoming`
- **Syntax:** `/next [batch]`
- **Arguments:**
  - `batch` (Optional): Specific batch to check. Defaults to your registered batch.
- **Permissions:** Everyone
- **Example:** `/next`

### `/week`
- **Description:** Consolidates all classes from Monday through Saturday for the specified batch into a unified weekly table.
- **Aliases:** `/weekly`, `/schedule`, `/fullweek`
- **Syntax:** `/week [batch]`
- **Arguments:**
  - `batch` (Optional): Batch code. Defaults to your registered batch.
- **Permissions:** Everyone
- **Example:** `/week`

### `/freerooms`
- **Description:** Searches all university facilities across campus to locate vacant classrooms and laboratories with no scheduled classes.
- **Aliases:** `/emptyrooms`, `/freeroomsnow`
- **Syntax:** `/freerooms [day] [time] [block]`
- **Arguments:**
  - `day` (Optional): Day of week (`today`, `Friday`). Defaults to today.
  - `time` (Optional): Time in 24h format (`11:30`, `14:30`). Defaults to current hour.
  - `block` (Optional): Specific building block filter (`BL7`, `BL8`, `BL10`, `BL11`).
- **Permissions:** Everyone
- **Example:** `/freerooms day:Friday time:11:30 block:BL8`

### `/findbatch`
- **Description:** Queries the university course database to locate batch identifiers by department, degree, or section number.
- **Aliases:** `/searchbatch`, `/batches`
- **Syntax:** `/findbatch <query>`
- **Arguments:**
  - `query` (Required): Search keyword (e.g. `CSE`, `AI`, `16`, `ECE`, `BTech`).
- **Permissions:** Everyone
- **Example:** `/findbatch query:CSE 16`

### `/batchinfo`
- **Description:** Detailed academic summary of a section including enrolled courses, instructors, classrooms, and weekly teaching volume.
- **Aliases:** None
- **Syntax:** `/batchinfo <batch>`
- **Arguments:**
  - `batch` (Required): Batch identifier (e.g. `AI 16`, `Core 35`, or code `26CAIBTAIB16`).
- **Permissions:** Everyone
- **Example:** `/batchinfo batch:AI 16`

### `/faculty`
- **Description:** Searches faculty assignments across all university timetables to show what courses and rooms an instructor teaches.
- **Aliases:** `/lecturer`, `/findfaculty`, `/prof`
- **Syntax:** `/faculty <name>`
- **Arguments:**
  - `name` (Required): Instructor name or surname keyword (e.g. `Ashim`, `Dipesh`, `Dasari`).
- **Permissions:** Everyone
- **Example:** `/faculty name:Ashim`

### `/degrees`
- **Description:** Displays the full roster of all 30 university degree programs and departments loaded into the bot.
- **Aliases:** None
- **Syntax:** `/degrees`
- **Arguments:** None
- **Permissions:** Everyone
- **Example:** `/degrees`

---

## 2. Student Profile and Registration

### `/register`
- **Description:** The primary student onboarding command. Triggers an interactive 3-step setup prompt or accepts a 1-line parameter setup.
- **Aliases:** `/setup`, `/onboard`
- **Syntax:** `/register [degree] [year] [batch] [name] [roll_no]`
- **Arguments:**
  - `degree` (Optional): Department or degree (e.g. `CSE`, `ECE`, `BTech`).
  - `year` (Optional): Academic year (`1`, `2`, `3`, `4`).
  - `batch` (Optional): Section number or batch code (`16`, `26CAIBTAIB16`).
  - `name` (Optional): Student full name.
  - `roll_no` (Optional): Student university roll number.
- **Permissions:** Everyone
- **Examples:**
  - Interactive: `/register`
  - Direct: `/register degree:CSE year:1 batch:16`

### `/link`
- **Description:** Direct link command to associate a Discord user ID with a specific batch, real name, and roll number.
- **Aliases:** `/linkme`
- **Syntax:** `/link <batch> [name] [roll_no]`
- **Arguments:**
  - `batch` (Required): Assigned batch code.
  - `name` (Optional): Real full name.
  - `roll_no` (Optional): University roll number.
- **Permissions:** Everyone
- **Example:** `/link batch:26CAIBTAIB16 name:Alex Johnson roll_no:26CS042`

### `/profile`
- **Description:** Shows registered degree, academic year, batch code, student name, roll number, and attendance status.
- **Aliases:** `/me`, `/whois`
- **Syntax:** `/profile [member]`
- **Arguments:**
  - `member` (Optional): Discord user to inspect. Defaults to yourself.
- **Permissions:** Everyone
- **Example:** `/profile`

### `/mybatch`
- **Description:** Inspects your current active batch or updates it without erasing your saved name or roll number.
- **Aliases:** `/setbatch`, `/switchbatch`
- **Syntax:** `/mybatch [batch]`
- **Arguments:**
  - `batch` (Optional): New batch code to switch to. Omit to view current batch.
- **Permissions:** Everyone
- **Example:** `/mybatch batch:26CAIBTAIB17`

### `/unlink`
- **Description:** Removes your student registration and batch association from the database.
- **Aliases:** `/unlinkme`, `/clearbatch`
- **Syntax:** `/unlink`
- **Arguments:** None
- **Permissions:** Everyone
- **Example:** `/unlink`

---

## 3. Attendance and Faculty Management

### `/attendance`
- **Description:** Master command group for faculty to record class attendance, review batch performance, or export CSV spreadsheets.
- **Aliases:** `/att`
- **Syntax:** `/attendance <take | summary | export> ...`
- **Subcommands:**
  - `take <batch> <subject> [day]`: Opens interactive roll-call portal for authorized faculty.
  - `summary <batch> [subject]`: Displays percentage calculations and at-risk students below 75%.
  - `export <batch>`: Generates and uploads a `.csv` spreadsheet file.
- **Permissions:** Authorized Faculty or Server Administrators
- **Examples:**
  - `/attendance take batch:26CAIBTAIB16 subject:Algorithms`
  - `/attendance summary batch:26CAIBTAIB16`
  - `/attendance export batch:26CAIBTAIB16`

### `/myattendance`
- **Description:** Student self-service attendance dashboard. Calculates overall percentage, safe absences remaining, or classes needed to recover above 75%.
- **Aliases:** `/attendance_me`, `/bunk`
- **Syntax:** `/myattendance [subject]`
- **Arguments:**
  - `subject` (Optional): Filter statistics to a single subject.
- **Permissions:** Registered Students
- **Example:** `/myattendance`

### `/setlecturer`
- **Description:** Grants authorization to a Discord member to record attendance for a specified subject and optional batch.
- **Aliases:** `/addlecturer`, `/authfaculty`
- **Syntax:** `/setlecturer <member> <subject> [batch]`
- **Arguments:**
  - `member` (Required): Target Discord user.
  - `subject` (Required): Course title or code.
  - `batch` (Optional): Section restriction. Omit to grant access for all sections.
- **Permissions:** Server Administrator or Bot Owner
- **Example:** `/setlecturer member:@Professor subject:Data Structures batch:26CAIBTAIB16`

### `/dellecturer`
- **Description:** Revokes attendance recording permissions from a specified faculty member.
- **Aliases:** `/removelecturer`
- **Syntax:** `/dellecturer <member>`
- **Arguments:**
  - `member` (Required): Faculty Discord user.
- **Permissions:** Server Administrator or Bot Owner
- **Example:** `/dellecturer member:@Professor`

### `/lecturers`
- **Description:** Outputs the directory of all registered instructors, their authorized subjects, and batch assignments.
- **Aliases:** `/faculty_list`
- **Syntax:** `/lecturers`
- **Arguments:** None
- **Permissions:** Everyone
- **Example:** `/lecturers`

---

## 4. Administration and Schedule Management

### `/reschedule`
- **Description:** Reschedules or substitutes a class for a specific calendar date and hour without altering the weekly master routine.
- **Aliases:** `/override`, `/setoverride`
- **Syntax:** `/reschedule <date> <time_slot> <subject> [batch] [room] [faculty] [note]`
- **Arguments:**
  - `date` (Required): Target date (`today`, `tomorrow`, `YYYY-MM-DD`, `DD-MM-YYYY`).
  - `time_slot` (Required): Time range (`10:30-11:30`) or start time (`10:30`).
  - `subject` (Required): Course title or `FREE` / `CANCELLED`.
  - `batch` (Optional): Target section (e.g. `26CAIBTAIB16` or `ALL`). Defaults to `ALL`.
  - `room` (Optional): Classroom or laboratory.
  - `faculty` (Optional): Instructor name.
  - `note` (Optional): Explanation note shown to students.
- **Permissions:** Server Administrator or Bot Owner
- **Example:** `/reschedule date:today time_slot:10:30-11:30 subject:AI Lab batch:26CAIBTAIB16 room:7004 note:Makeup Session`

### `/cancelclass`
- **Description:** Marks a class slot as cancelled for a specific calendar date and converts it into a Free Period with a reason note.
- **Aliases:** None
- **Syntax:** `/cancelclass <date> <time_slot> [batch] [reason]`
- **Arguments:**
  - `date` (Required): Target date (`today`, `tomorrow`, `YYYY-MM-DD`).
  - `time_slot` (Required): Start time or span (`09:30`).
  - `batch` (Optional): Target batch or `ALL`.
  - `reason` (Optional): Reason displayed to students.
- **Permissions:** Server Administrator or Bot Owner
- **Example:** `/cancelclass date:today time_slot:09:30 batch:26CAIBTAIB16 reason:Faculty on leave`

### `/overrides`
- **Description:** Displays all active single-date timetable overrides, rescheduled lectures, and cancellations currently stored in the system.
- **Aliases:** None
- **Syntax:** `/overrides`
- **Arguments:** None
- **Permissions:** Everyone
- **Example:** `/overrides`

### `/deloverride`
- **Description:** Deletes a single-date override and restores the original recurring weekly timetable for that date and time.
- **Aliases:** None
- **Syntax:** `/deloverride <date> <start> [batch]`
- **Arguments:**
  - `date` (Required): Target date (`today`, `tomorrow`, `YYYY-MM-DD`).
  - `start` (Required): Start time (`HH:MM`).
  - `batch` (Optional): Batch filter.
- **Permissions:** Server Administrator or Bot Owner
- **Example:** `/deloverride date:today start:10:30 batch:26CAIBTAIB16`

### `/setclass`
- **Description:** Adds or overwrites a recurring slot in the weekly schedule. When given a calendar date or `today`/`tomorrow`, it automatically creates a single-date override.
- **Aliases:** `/addclass`
- **Syntax:** `/setclass <day> <time_slot> <subject> [batch] [room] [faculty]`
- **Arguments:**
  - `day` (Required): Weekday name (`Friday`, `Monday`) or date (`today`, `YYYY-MM-DD`).
  - `time_slot` (Required): Strict 24h format `HH:MM-HH:MM` (e.g. `09:30-10:30`).
  - `subject` (Required): Course name, or `FREE` for break.
  - `batch` (Optional): Batch code or `ALL`.
  - `room` (Optional): Classroom code.
  - `faculty` (Optional): Instructor name.
- **Permissions:** Server Administrator or Bot Owner
- **Example:** `/setclass day:Friday time_slot:10:30-11:30 subject:Chemistry batch:26CAIBTAIB16 room:8104 faculty:Dr. Sathish`

### `/delclass`
- **Description:** Permanently removes a recurring class slot from the weekly master timetable.
- **Aliases:** `/removeclass`, `/rmclass`
- **Syntax:** `/delclass <day> <start> [batch]`
- **Arguments:**
  - `day` (Required): Weekday (`Monday`, `Friday`).
  - `start` (Required): Start time `HH:MM` (e.g. `09:30`).
  - `batch` (Optional): Batch filter.
- **Permissions:** Server Administrator or Bot Owner
- **Example:** `/delclass day:Friday start:10:30 batch:26CAIBTAIB16`

### `/students`
- **Description:** Displays the student directory with registered names, university roll numbers, and assigned batch sections.
- **Aliases:** `/batchlist`, `/roster`
- **Syntax:** `/students [batch]`
- **Arguments:**
  - `batch` (Optional): Filter by batch. Omit to list all registered students.
- **Permissions:** Server Administrator or Bot Owner
- **Example:** `/students batch:26CAIBTAIB16`

### `/linkuser`
- **Description:** Administrator utility to manually associate or correct another user's student credentials.
- **Aliases:** `/adminlink`, `/setstudent`
- **Syntax:** `/linkuser <member> <batch> [name] [roll_no]`
- **Arguments:**
  - `member` (Required): Discord user to link.
  - `batch` (Required): Assigned batch code.
  - `name` (Optional): Real name.
  - `roll_no` (Optional): Roll number.
- **Permissions:** Server Administrator or Bot Owner
- **Example:** `/linkuser member:@Student batch:26CAIBTAIB16 name:Jane Doe roll_no:26CS088`

### `/unlinkuser`
- **Description:** Administrator utility to remove another student's account link and profile.
- **Aliases:** `/adminunlink`, `/delstudent`
- **Syntax:** `/unlinkuser <member>`
- **Arguments:**
  - `member` (Required): Target Discord member.
- **Permissions:** Server Administrator or Bot Owner
- **Example:** `/unlinkuser member:@Student`

### `/loadbatch`
- **Description:** Activates a precomputed university batch schedule as the server's default timetable template.
- **Aliases:** None
- **Syntax:** `/loadbatch <batch>`
- **Arguments:**
  - `batch` (Required): Batch code (e.g. `26CAIBTAIB16`).
- **Permissions:** Bot Owner only
- **Example:** `/loadbatch batch:26CAIBTAIB16`

### `/setprefix`
- **Description:** Configures the bot command prefix string for prefix-based text messages.
- **Aliases:** `/prefix`
- **Syntax:** `/setprefix <prefix>`
- **Arguments:**
  - `prefix` (Required): New prefix (1 to 5 characters, e.g. `!`, `?`, `.`).
- **Permissions:** Server Administrator or Bot Owner
- **Example:** `/setprefix prefix:!`

### `/sync`
- **Description:** Forces immediate synchronization of slash application commands with the Discord API.
- **Aliases:** `/syncslash`
- **Syntax:** `/sync` (or `!sync`)
- **Arguments:** None
- **Permissions:** Bot Owner only
- **Example:** `!sync`

### `/reload`
- **Description:** Hot-reloads code modules without restarting the bot process or disconnecting from the Discord gateway.
- **Aliases:** `/refresh`
- **Syntax:** `/reload [cog_path]`
- **Arguments:**
  - `cog_path` (Optional): Module path (e.g. `cogs.timetable`, `cogs.admin`, `cogs.attendance`, `cogs.help`). Omit to reload all.
- **Permissions:** Bot Owner only
- **Example:** `!reload`

---

## 5. General Utilities

### `/help`
- **Description:** Interactive help menu featuring categorized dropdown navigation and command syntax inspections.
- **Aliases:** `/commands`, `/guide`, `/h`
- **Syntax:** `/help [command]`
- **Arguments:**
  - `command` (Optional): Exact command name to inspect parameters and examples for.
- **Permissions:** Everyone
- **Examples:**
  - General Menu: `/help`
  - Specific Command: `/help command:reschedule`
