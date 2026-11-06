# Faculty and Instructor Guide

This document is the official operational guide for university faculty members, department coordinators, lecturers, and teaching assistants using the academic Discord bot.

It details how to manage single-date schedule changes, mark student attendance through the interactive roll-call portal, export attendance spreadsheets, and locate vacant rooms for extra sessions.

---

## 1. Schedule Adjustments and Class Management

Faculty members often need to reschedule a class due to conferences, exams, lab makeup sessions, or leave. The bot provides two distinct mechanisms for schedule modifications:

1. **Date-Specific Overrides (Temporary):** Applies exclusively to a single calendar date without altering the recurring weekly timetable for future weeks.
2. **Recurring Weekly Updates (Permanent):** Changes the weekly timetable on that day for all future weeks of the semester.

---

### A. Rescheduling or Substituting a Class (Single Date)

Use `/reschedule` to substitute a course, change a classroom, or conduct a makeup session on a specific date.

```
/reschedule date:<date> time_slot:<start-end> subject:<name> batch:<batch> [room] [faculty] [note]
```

#### Supported Date Formats
- Relative terms: `today`, `tomorrow`
- Standard formats: `2026-11-06`, `06-11-2026`, `06/11/2026`, `6 Nov`
- Weekday names: `Friday` (resolves to the upcoming Friday)

#### Practical Examples
- Reschedule today's 10:30 AM class for Batch 16 to an AI Programming Lab:
  ```
  /reschedule date:today time_slot:10:30-11:30 subject:AI Programming batch:26CAIBTAIB16 room:7004-BL7-GF faculty:Dr. Sundheep R note:Makeup Lab
  ```
- Schedule an extra session tomorrow at 2:30 PM:
  ```
  /reschedule date:tomorrow time_slot:14:30-15:30 subject:Data Structures batch:26CAIBTAIB16 room:8104 note:Special Revision Class
  ```

#### How Students See the Change
When students in that batch run `/today` or `/next`, the modified slot immediately displays an active indicator:
```
[RESCHEDULED] 10:30 - 11:30 | AI Programming [LAB]
Location: Room 7004-BL7-GF | Instructor: Dr. Sundheep R
Note: Makeup Lab
```

---

### B. Cancelling a Class for a Specific Date

When an instructor is on leave or an exam replaces a routine session:

```
/cancelclass date:<date> time_slot:<start_time> batch:<batch> reason:<reason>
```

#### Example
```
/cancelclass date:today time_slot:09:30 batch:26CAIBTAIB16 reason:Department Faculty Meeting
```

#### Result
- The class slot is converted to a free period for that date only.
- Students checking `/today` see:
  ```
  [CANCELLED] 09:30 - 10:30 | FREE PERIOD
  Reason: Department Faculty Meeting
  ```
- Normal weekly schedules resume automatically on subsequent weeks.

---

### C. Reviewing and Reverting Overrides

- **View all active date overrides across the server:**
  ```
  /overrides
  ```
- **Remove an override and restore the standard weekly timetable:**
  ```
  /deloverride date:today start:10:30 batch:26CAIBTAIB16
  ```

---

### D. Permanent Weekly Timetable Modifications

If an official timetable revision takes effect for all remaining weeks of the semester:

- **Add or overwrite a recurring weekly slot:**
  ```
  /setclass day:Friday time_slot:10:30-11:30 subject:Calculus batch:26CAIBTAIB16 room:11206-BL11-SF faculty:Dr. Dipesh
  ```
- **Delete an existing weekly slot:**
  ```
  /delclass day:Friday start:10:30 batch:26CAIBTAIB16
  ```

*Changes made with `/setclass` and `/delclass` write directly to the persistent batch timetable file (`data/timetables/converted/{BATCH}.json`).*

---

## 2. Interactive Attendance Management

The bot includes an attendance recording module designed for academic compliance with university regulations (default 75% minimum threshold).

### A. Authorization
Only designated faculty members or server administrators can record attendance.
Administrators authorize instructors using:
```
/setlecturer member:@Professor subject:Operating Systems batch:26CAIBTAIB16
```
To review authorized instructors on the server:
```
/lecturers
```

---

### B. Recording Attendance During Class

To launch the roll-call portal for your lecture:
```
/attendance take batch:26CAIBTAIB16 subject:Operating Systems
```

#### The Interactive Workflow
1. The bot verifies that the recording request falls within the lecture window (opens 5 minutes prior to start and closes 15 minutes after end).
2. The bot displays an interactive roster listing all registered students in that section with their official roll numbers.
3. By default, all linked students are queued as Present. Click on any absent student to toggle their status to Absent.
4. Click **Submit Attendance**. The record is cryptographically timestamped and saved.

#### Out-of-Window Attendance Recording
If attendance must be marked outside the official time window (for example, delayed entry due to connectivity), the bot prompts for administrator authorization before committing the log.

---

### C. Reviewing Attendance Statistics

Faculty can audit batch attendance performance at any point:
```
/attendance summary batch:26CAIBTAIB16
```
To inspect attendance for a specific subject:
```
/attendance summary batch:26CAIBTAIB16 subject:Operating Systems
```

The summary provides:
- Total sessions conducted to date.
- Overall batch attendance percentage.
- List of at-risk students currently below the 75% mandatory threshold.
- Consecutive sessions required for each at-risk student to restore eligibility.

---

### D. Exporting Attendance to Excel / CSV

To export official records for university administration, grading, or internal audits:
```
/attendance export batch:26CAIBTAIB16
```

#### Output
- The bot generates an RFC-4180 compliant `.csv` spreadsheet and uploads it directly to the channel.
- Open the file in Microsoft Excel, Apple Numbers, or Google Sheets.
- Columns include: `Student Name`, `Roll Number`, `Subject`, `Conducted Sessions`, `Attended Sessions`, `Percentage`, and `Status (Eligible / At Risk)`.

---

## 3. Finding Empty Classrooms for Extra Sessions and Viva

When scheduling extra makeup lectures, remedial sessions, or project viva examinations, faculty can scan all campus facilities for vacant rooms:

```
/freerooms [day] [time] [block]
```

### Examples
- Check empty rooms right now:
  ```
  /freerooms
  ```
- Check available rooms on Thursday at 2:30 PM:
  ```
  /freerooms day:Thursday time:14:30
  ```
- Check empty rooms in Block 8 during Friday lunch:
  ```
  /freerooms day:Friday time:12:30 block:BL8
  ```

The system verifies all 428 batch schedules and returns only rooms that have zero classes scheduled during that hour.

---

## 4. Student Roster and Batch Directory

Faculty can review student registrations to ensure all students in their tutorial or lab are linked:

- **List all students registered in a batch:**
  ```
  /students batch:26CAIBTAIB16
  ```
- **Inspect curriculum subjects and faculty assignments for a batch:**
  ```
  /batchinfo batch:26CAIBTAIB16
  ```
- **Inspect your own teaching schedule across batches:**
  ```
  /faculty name:YourLastName
  ```

---

## 5. Summary of Recommended Best Practices

1. **Submit Reschedules Early:** Post reschedules at least 2 to 4 hours in advance so students checking `/today` or `/next` see the updated classroom and timing.
2. **Include Descriptive Notes:** When rescheduling, use the `note` parameter (for example, `note:Special Lab Makeup` or `note:Viva Room Shift`) to reduce student inquiries.
3. **Download Monthly CSV Backups:** Use `/attendance export` at the conclusion of each academic month and archive the spreadsheet in department records.
