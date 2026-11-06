# University Academic Timetable Bot Documentation

Welcome to the documentation repository for the Academic Timetable, Classroom Directory, and Attendance Management Discord Bot.

This system is built specifically for academic university environments, offering batch-aware schedule routing, interactive faculty roll-call tracking, campus-wide empty classroom discovery, and date-specific schedule modifications.

---

## Documentation Index

The documentation is organized into targeted operational guides designed for different user roles:

1. **[Student User Guide](STUDENT_GUIDE.md)**
   - Designed for students, especially those new to Discord.
   - Quick 10-second registration walkthrough.
   - Checking daily routines, ongoing lectures, and weekly timetables.
   - Locating vacant classrooms during breaks using `/freerooms`.
   - Personal attendance tracking and safe-absence calculations.

2. **[Faculty and Instructor Guide](FACULTY_GUIDE.md)**
   - Operational guide for lecturers, lab assistants, and department coordinators.
   - Single-date class rescheduling and lecture cancellations with reason notes.
   - Managing substitute lectures and temporary classroom relocations.
   - Using the interactive attendance portal to record present/absent roll calls.
   - Exporting attendance spreadsheets (`.csv`) for university administration.
   - Finding empty lecture halls for remedial sessions and viva exams.

3. **[Server Administrator and IT Guide](ADMIN_GUIDE.md)**
   - Architecture overview and local atomic JSON storage layout.
   - Managing master weekly timetables across 428 sections.
   - Authorizing instructors and managing attendance access control.
   - Student roster audits, manual account linking, and unlinking.
   - Prefix controls, hot-reloading extensions, and slash command synchronization.

4. **[Comprehensive Command Reference](COMMAND_REFERENCE.md)**
   - Complete technical manual for all 32 commands across 5 categories.
   - Detailed parameter specifications, optional flags, required permissions, and examples.

---

## System Overview and Core Capabilities

### 1. Multi-Tenant Batch Routing
The system supports over 400 unique university batches across 30 degree programs. Students register their department, year, and section once using `/register`. From that point on, standard commands like `/today`, `/next`, and `/week` automatically load their specific class schedule without needing to supply batch codes.

### 2. Date-Specific Schedule Overrides
University schedules often change on short notice due to faculty leave, makeup classes, or exams. The bot allows authorized personnel to issue single-date overrides using `/reschedule` or `/cancelclass`. These modifications take effect only on the specified date, without corrupting or modifying the permanent weekly schedule.

### 3. Campus-Wide Classroom Availability
During free periods, students and faculty can query `/freerooms` to scan all academic facilities across campus. The system cross-references all 428 batch timetables to identify rooms that have no active lecture scheduled during that hour.

### 4. Compliant Attendance Tracking
Faculty can launch an interactive roll-call menu during the class time window, click students to mark absences, and submit the roster with an audit timestamp. Students can track their percentage and safe absence allowances under the university 75% rule using `/myattendance`.

---

## Quick Navigation Cheat Sheet

| Task | Command | Target Audience |
| :--- | :--- | :--- |
| First-time onboarding | `/register` | Students |
| View today's schedule | `/timetable` or `/today` | Students & Faculty |
| Check next class & room | `/next` | Students & Faculty |
| View full weekly table | `/week` | Students & Faculty |
| Find empty study rooms | `/freerooms` | Students & Faculty |
| Check personal attendance | `/myattendance` | Students |
| Reschedule a class date | `/reschedule` | Faculty & Admin |
| Cancel a class date | `/cancelclass` | Faculty & Admin |
| Review active overrides | `/overrides` | Everyone |
| Mark student attendance | `/attendance take` | Faculty |
| Export attendance CSV | `/attendance export` | Faculty & Admin |
| Search batch identifiers | `/findbatch` | Everyone |
| View command manual | `/help` | Everyone |
