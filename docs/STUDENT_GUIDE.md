# Student User Guide

Welcome to the university academic timetable and attendance bot. This guide is written specifically for students, including those who are completely new to Discord, to help you configure your schedule, view daily routines, find empty study classrooms, and track attendance.

---

## 1. Introduction for Students New to Discord

### What is this Bot?
This bot is an automated university assistant running inside your department's Discord server. It connects to the university course directory to give you:
- Personalized daily class timetables.
- Live countdowns to your next lecture with room numbers and faculty names.
- Campus-wide room availability so you can find quiet, empty classrooms during breaks.
- Personal attendance monitoring with safe-absence calculations.

### How to Execute Commands
There are two ways to talk to the bot in any permitted server channel:

1. **Slash Commands (Recommended):**
   - Click the chat box, type a forward slash `/`, and a menu of available bot commands will pop up.
   - Click the command you want (for example, `/today` or `/register`).
   - Fill in any optional fields and press Enter.

2. **Prefix Text Commands:**
   - Type an exclamation mark followed by the command name (for example, `!today`, `!next`, or `!week`) and press Enter.
   - If the bot prefix has been customized on your server, use that prefix instead.

---

## 2. First-Time Setup: 10-Second Registration

Because each department, year, and section has its own unique timetable, the bot needs to know your section before it can display your classes.

### Method A: Interactive Setup Wizard (Easiest)
1. Type `/register` (or `!register`) in the chat and press Enter.
2. The bot will send a private setup prompt asking for:
   - **Step 1: Department / Degree:** Type your program, such as `CSE`, `ECE`, `BTech`, `BCA`, or `BBA`.
   - **Step 2: Academic Year:** Type `1`, `2`, `3`, or `4`.
   - **Step 3: Section / Batch Number:** Type your section. You only need your friendly 2-digit number (e.g. `66`, `16`, `35`) or stream prefix (e.g. `AI 16`, `Core 66`, `Cyber 59`).
3. The bot will confirm your registration immediately. From that moment forward, all timetable commands work automatically for your classes.

### Method B: 2-Tap Interactive Batch Picker
If you don't want to type anything at all:
1. Type `/batchpicker` (or `!batchpicker`).
2. Tap your **Academic Year** (`Year 1`, `Year 2`, `Year 3`, `Year 4`).
3. Pick your **Department / Stream** from the dropdown menu.
4. Select your **Batch Section** to instantly link your account with 1 click!

### Friendly Batch Formats Supported
You never need to remember or type the full 12-character university code unless you prefer to. The bot natively understands all of these formats across all commands:

- **Year + Section Number (Super Simple):**
  - `1 66` (Year 1, Batch 66 -> `26CAIBTCSB66`)
  - `1 16` (Year 1, Batch 16 -> `26CAIBTAIB16`)
  - `2 66` (Year 2, Batch 66 -> `25CAIBTCSB66`)
  - `2 16` (Year 2, Batch 16 -> `25CAIBTAIB16`)
- **Specialization + Section Number (Recommended):**
  - AI / ML: `AI 16` (also accepts `AIML 16`, `AI/ML 16`)
  - Core CSE: `Core 66` (also accepts `Core 35`, `CS 66`, `CSE 66`)
  - Cyber Security: `Cyber 59` (also accepts `CY 59`)
  - Data Science: `DS 57` (also accepts `Data 57`)
  - ECE: `ECE 05`
  - Civil: `Civil 01` (or `CE 01`)
  - Mechanical: `Mech 01` (or `ME 01`)
- **Section Number Only:**
  - If your department/year is registered, simply typing `66`, `16`, or `35` automatically maps to your cohort.
- **Full 12-Character Code (Fully Retained):**
  - E.g. `26CAIBTCSB66`, `26CAIBTAIB16`, `25CAIBTCSB66`.

### Method C: One-Line Quick Setup
If you know your details, register in a single command:
```
/register degree:CSE year:1 batch:66
```
Or with text command:
```
!register CSE 1 66
```

### What if You Do Not Know Your Exact Code?
- To see all university degrees: `/degrees`
- To search valid batch numbers: `/findbatch query:CSE`

---

## 3. Daily Schedule Commands

Once registered, you never need to enter your batch number again.

### Viewing Today's Classes
```
/timetable
```
*Aliases: `/today`, `/tt`*

Displays the complete timetable for today, including:
- Time spans (for example, `09:30 - 10:30`).
- Subject title and class type (`LECTURE`, `LAB`, `TUTORIAL`).
- Assigned lecture hall / room number (for example, `Room 7004-BL7-GF`).
- Instructor name.
- Highlight on any lecture currently in progress.

### Viewing Other Days
```
/timetable day:Monday
/timetable day:tomorrow
```

### Checking Your Next Upcoming Class
```
/next
```
*Aliases: `/now`, `/upnext`*

Use this when you are walking between classes or wondering where to go next. The bot displays:
- The ongoing class with a live progress bar.
- The next scheduled class.
- The exact classroom number.
- The time remaining until start.

### Viewing the Full Weekly Schedule
```
/week
```
*Aliases: `/weekly`, `/schedule`*

Displays your consolidated Monday through Saturday timetable in a clean overview table.

---

## 4. Finding Empty Study Classrooms During Free Periods

When you have a break or free period, you do not need to roam the campus looking for a quiet place to study or charge your laptop.

```
/freerooms
```
*Aliases: `/emptyrooms`, `/freeroomsnow`*

### How it works:
- Scans all university classrooms across Blocks 7, 8, 10, and 11.
- Identifies rooms that have no lecture scheduled during the current hour.
- Groups available rooms by building floor and block.

### Targeted Search Examples:
- Find empty rooms in Block 8 right now:
  ```
  /freerooms block:BL8
  ```
- Find empty rooms at 2:30 PM today:
  ```
  /freerooms time:14:30
  ```
- Find empty rooms on Friday at 11:30 AM in Block 11:
  ```
  /freerooms day:Friday time:11:30 block:BL11
  ```

---

## 5. Attendance Tracking and Safe-Bunk Calculator

The bot includes an attendance dashboard designed to keep you comfortably above the university 75% attendance rule.

### Viewing Your Attendance
```
/myattendance
```
*Aliases: `/bunk`*

The bot responds with an attendance breakdown for all your enrolled subjects:
1. **Attendance Percentage:** Exact percentage of conducted lectures you attended.
2. **Safe Absence Calculation (Bunk Margin):**
   - If your attendance is above 75%, it calculates the exact number of upcoming classes you can miss while keeping your attendance at or above 75%.
3. **Recovery Calculation:**
   - If your attendance is below 75%, it calculates how many consecutive classes you must attend without missing to restore your standing above 75%.

### Filtering by Subject
```
/myattendance subject:Calculus
```

---

## 6. Profile and Account Management

### Inspecting Your Saved Profile
```
/profile
```
Displays your linked degree, year, batch code, student name, roll number, and overall attendance rate.

### Switching Sections
If your section changes or you want to switch batch schedules:
```
/mybatch batch:26CAIBTAIB17
```

### Unlinking Your Account
If you want to clear your data from the server:
```
/unlink
```

---

## 7. Frequently Asked Questions for Students

**Question: Can other students see my personal attendance?**  
Answer: No. When you run `/myattendance`, the response is displayed privately or restricted to your interaction. Faculty and administrators can review batch summaries for academic auditing.

**Question: The bot says my batch is not found. What should I do?**  
Answer: Type `/findbatch query:YourDepartment` (for example, `/findbatch query:ECE` or `/findbatch query:BCA`). The bot will display the exact active section identifiers.

**Question: My lecture time was changed today. Why does the timetable show the old time?**  
Answer: Check if your faculty has posted a reschedule. Faculty can issue single-date updates using `/reschedule`, which automatically updates the bot schedule for that date. You can view all temporary schedule modifications with `/overrides`.
