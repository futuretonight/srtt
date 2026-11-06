# Server Administrator and IT Operations Guide

This guide provides technical specifications, database architecture, permission management, and operational workflows for server administrators and bot owners managing the academic Discord bot.

---

## 1. System Architecture and Storage Layout

All data is maintained locally in human-readable, atomic JSON files within the `data/` directory. Edits are handled through asynchronous locks and atomic file replacement (`tempfile.mkstemp` + `os.replace`), ensuring that process terminations never leave corrupt or half-written files.

### Storage Directory Overview

| File Path | Description |
| :--- | :--- |
| `data/schedule.json` | Default fallback schedule template. Used when a user is not linked to a batch or queries general routines. |
| `data/timetables/converted/{BATCH}.json` | Individual master schedules for all 428 university batches (converted from SRUX format). |
| `data/overrides.json` | Single-date schedule modifications, class reschedules, and temporary cancellations. |
| `data/users.json` | Registered user profiles containing Discord user IDs, degree, year, batch, student name, and roll number. |
| `data/timetable_options.json` | Hierarchical directory mapping Degrees -> Academic Years -> Sections / Batch Codes. |
| `data/batches_index.json` | Fast-lookup inverted index used by the batch resolver to match natural inputs (e.g. `CSE 1 16`). |
| `data/room_availability.json` | Precomputed campus classroom inventory across Blocks 7, 8, 10, and 11 used by `/freerooms`. |
| `data/attendance.json` | Session logs, present/absent roll calls, and lecturer permission assignments. |
| `data/config.json` | Server-specific configuration, command prefixes, and legacy link mappings. |

---

## 2. Managing Student Accounts and Rosters

While students are encouraged to use the self-service `/register` command, administrators can audit and manage student records directly.

### Manually Linking a Student Profile
Assign or correct a student's degree section, full name, and roll number:
```
/linkuser member:@Student batch:26CAIBTAIB16 name:Alex Johnson roll_no:26CS042
```

### Unlinking a Student Profile
Remove a student's profile from the database:
```
/unlinkuser member:@Student
```

### Auditing Batch Rosters
List all registered students in a specific batch:
```
/students batch:26CAIBTAIB16
```
To view all linked students across the entire server:
```
/students
```

---

## 3. Faculty Authorization and Permissions

Attendance recording requires specific authorization. Administrators can grant granular permissions by subject and batch.

### Authorizing an Instructor
Grant an instructor permission to record attendance for a course across all sections:
```
/setlecturer member:@FacultyMember subject:Data Structures
```
To restrict their authorization to a specific section only:
```
/setlecturer member:@FacultyMember subject:Data Structures batch:26CAIBTAIB16
```

### Revoking Authorization
```
/dellecturer member:@FacultyMember
```

### Inspecting Authorized Lecturers
```
/lecturers
```
Outputs the complete directory of authorized faculty, their assigned subjects, batch restrictions, and granted timestamps.

---

## 4. Master Timetable Maintenance

### Activating a Primary Default Batch
To set a specific university batch as the server default for unlinked queries:
```
/loadbatch batch:26CAIBTAIB16
```

### Modifying Batch Schedules
- **Add or update a recurring class slot:**
  ```
  /setclass day:Monday time_slot:09:30-10:30 subject:Algorithms batch:26CAIBTAIB16 room:7004 faculty:Dr. Smith
  ```
  If `batch` is a specific batch identifier, the update is saved directly into `data/timetables/converted/{BATCH}.json`. If `batch:ALL` is passed, it updates `data/schedule.json`.

- **Remove a recurring class slot:**
  ```
  /delclass day:Monday start:09:30 batch:26CAIBTAIB16
  ```

---

## 5. Date Overrides and Rescheduling Operations

Administrators have full oversight of single-date schedule changes:
- View all active single-date modifications on record:
  ```
  /overrides
  ```
- Reschedule or replace a class slot for a date:
  ```
  /reschedule date:2026-11-10 time_slot:14:30-15:30 subject:Seminar batch:26CAIBTAIB16 room:Auditorium
  ```
- Cancel a scheduled lecture for a date:
  ```
  /cancelclass date:today time_slot:11:30 batch:26CAIBTAIB16 reason:Guest Lecture
  ```
- Revert an override:
  ```
  /deloverride date:2026-11-10 start:14:30 batch:26CAIBTAIB16
  ```

---

## 6. Server Configuration and Runtime Controls

### Updating the Text Command Prefix
```
/setprefix prefix:!
```
Configures the bot to respond to a new symbol (1 to 5 characters) without altering slash command operations.

### Synchronizing Slash Commands with Discord
When new commands are added or updated, execute:
```
!sync
```
*Permission Requirement: Bot Owner only. This sends an HTTP PUT to Discord's application command endpoint.*

### Hot-Reloading Cogs Without Downtime
To reload modified code modules without stopping the Python process:
- Reload a single cog:
  ```
  !reload cog_path:cogs.timetable
  !reload cog_path:cogs.admin
  !reload cog_path:cogs.help
  ```
- Reload all active cogs:
  ```
  !reload
  ```

---

## 7. Data Backup Recommendations

It is recommended to run a daily or weekly backup of the `data/` folder. A basic PowerShell backup command:
```powershell
Copy-Item -Path "data" -Destination "backups/data_$(Get-Date -Format 'yyyyMMdd')" -Recurse
```
Because all files are local JSON, restoring from a backup simply requires copying the files back and issuing `!reload`.
