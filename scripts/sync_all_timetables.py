#!/usr/bin/env python3
"""Sync all timetables from SRUX.in for all degrees, years, and batches.

Usage:
    python scripts/sync_all_timetables.py
    python scripts/sync_all_timetables.py --limit 10   # test run with 10 batches
    python scripts/sync_all_timetables.py --concurrency 15
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import aiohttp

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
TIMETABLES_DIR = DATA_DIR / "timetables"
RAW_DIR = TIMETABLES_DIR / "raw"
CONVERTED_DIR = TIMETABLES_DIR / "converted"
OPTIONS_FILE = DATA_DIR / "timetable_options.json"
INDEX_FILE = DATA_DIR / "batches_index.json"
ROOMS_FILE = DATA_DIR / "room_availability.json"

OPTIONS_URL = "https://www.srux.in/timetable_options.json"
ROOMS_URL = "https://www.srux.in/room_availability.json"
TIMETABLE_URL_TEMPLATE = "https://www.srux.in/timetables/{batch}.json"

DAY_MAP = {
    "monday": "mon",
    "tuesday": "tue",
    "wednesday": "wed",
    "thursday": "thu",
    "friday": "fri",
    "saturday": "sat",
    "sunday": "sun",
}

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("sync_timetables")


def parse_time_minutes(val: str) -> Optional[int]:
    """Parse time string like '08:30', '8:30', '8:30 AM', '1:30 PM' into minutes since midnight."""
    if not val or not isinstance(val, str):
        return None
    val = val.strip()
    m = re.match(r"^(\d{1,2})(?::(\d{2}))?\s*(AM|PM)?$", val, re.IGNORECASE)
    if not m:
        return None
    h = int(m.group(1))
    minute = int(m.group(2) or 0)
    meridian = (m.group(3) or "").upper()
    if meridian == "PM" and h != 12:
        h += 12
    elif meridian == "AM" and h == 12:
        h = 0
    if 0 <= h < 24 and 0 <= minute < 60:
        return h * 60 + minute
    return None


def format_minutes_hhmm(mins: int) -> str:
    h = mins // 60
    m = mins % 60
    return f"{h:02d}:{m:02d}"


def convert_srux_to_bot_schedule(raw_data: dict[str, Any], batch_code: str) -> dict[str, list[dict[str, Any]]]:
    """Convert raw srux timetable format into timetable_bot's schedule structure."""
    raw_timetable = raw_data.get("timetable", raw_data)
    if not isinstance(raw_timetable, dict):
        return {d: [] for d in DAY_MAP.values()}

    bot_schedule: dict[str, list[dict[str, Any]]] = {d: [] for d in DAY_MAP.values()}

    for day_name, slots in raw_timetable.items():
        day_key = DAY_MAP.get(str(day_name).strip().lower())
        if not day_key or not isinstance(slots, list):
            continue

        # Sort slots by start time if possible
        parsed_slots: list[tuple[int, dict[str, Any]]] = []
        for slot in slots:
            if not isinstance(slot, dict):
                continue
            time_val = slot.get("time") or slot.get("start") or slot.get("startTime") or ""
            # Handle range if time is e.g. "08:30-09:30"
            start_m = None
            explicit_end_m = None
            if "-" in time_val or "–" in time_val:
                parts = re.split(r"[-–]", time_val)
                start_m = parse_time_minutes(parts[0])
                explicit_end_m = parse_time_minutes(parts[1]) if len(parts) > 1 else None
            else:
                start_m = parse_time_minutes(time_val)

            if start_m is not None:
                parsed_slots.append((start_m, {
                    "raw": slot,
                    "start_m": start_m,
                    "explicit_end_m": explicit_end_m,
                }))

        parsed_slots.sort(key=lambda x: x[0])

        for idx, (start_m, slot_info) in enumerate(parsed_slots):
            explicit_end = slot_info.get("explicit_end_m")
            if explicit_end is not None and explicit_end > start_m:
                end_m = explicit_end
            elif idx + 1 < len(parsed_slots) and parsed_slots[idx + 1][0] > start_m:
                end_m = parsed_slots[idx + 1][0]
            else:
                end_m = start_m + 60  # default 1 hour slot

            # Cap slot length at 4 hours in case of strange data
            if end_m > start_m + 240:
                end_m = start_m + 60

            start_str = format_minutes_hhmm(start_m)
            end_str = format_minutes_hhmm(end_m)

            raw_slot = slot_info["raw"]
            subjects = raw_slot.get("subjects", [])
            if not isinstance(subjects, list):
                continue

            for sub in subjects:
                if not isinstance(sub, dict):
                    continue
                subj_name = (sub.get("subject") or "").strip()
                if not subj_name:
                    continue

                kind = (sub.get("ltp") or "Lecture").strip()
                room = (sub.get("room_name") or str(sub.get("room") or "")).strip()
                faculty = (sub.get("facultyName") or "").strip()

                bot_schedule[day_key].append({
                    "start": start_str,
                    "end": end_str,
                    "category": "class",
                    "subject": subj_name,
                    "kind": kind,
                    "room": room,
                    "faculty": faculty,
                    "batches": [batch_code],
                })

        # Sort slots of this day by start time
        bot_schedule[day_key].sort(key=lambda s: s["start"])

    return bot_schedule


async def fetch_options(session: aiohttp.ClientSession) -> dict[str, Any]:
    """Fetch and save timetable_options.json."""
    log.info("Fetching timetable options directory from %s ...", OPTIONS_URL)
    async with session.get(OPTIONS_URL) as resp:
        if resp.status != 200:
            raise RuntimeError(f"Failed to fetch options: HTTP {resp.status}")
        data = await resp.json(content_type=None)

    OPTIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
    OPTIONS_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    log.info("Saved timetable options to %s", OPTIONS_FILE)
    return data


async def fetch_rooms(session: aiohttp.ClientSession) -> dict[str, Any]:
    """Fetch and save room_availability.json."""
    log.info("Fetching room availability from %s ...", ROOMS_URL)
    try:
        async with session.get(ROOMS_URL) as resp:
            if resp.status == 200:
                data = await resp.json(content_type=None)
                ROOMS_FILE.parent.mkdir(parents=True, exist_ok=True)
                ROOMS_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
                log.info("Saved room availability to %s", ROOMS_FILE)
                return data
            else:
                log.warning("Could not fetch rooms: HTTP %d", resp.status)
    except Exception as exc:
        log.warning("Error fetching room availability: %s", exc)
    return {}


async def fetch_batch_timetable(
    session: aiohttp.ClientSession,
    batch_code: str,
    semaphore: asyncio.Semaphore,
    max_retries: int = 3,
) -> Tuple[str, Optional[dict[str, Any]], Optional[str]]:
    """Fetch a single batch timetable with retries."""
    url = TIMETABLE_URL_TEMPLATE.format(batch=batch_code)
    async with semaphore:
        for attempt in range(1, max_retries + 1):
            try:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=20)) as resp:
                    if resp.status == 200:
                        data = await resp.json(content_type=None)
                        return batch_code, data, None
                    elif resp.status == 404:
                        return batch_code, None, "404 Not Found"
                    else:
                        if attempt == max_retries:
                            return batch_code, None, f"HTTP {resp.status}"
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if attempt == max_retries:
                    return batch_code, None, f"{type(exc).__name__}: {exc}"
            await asyncio.sleep(0.5 * attempt)
    return batch_code, None, "Max retries exceeded"


async def main() -> None:
    parser = argparse.ArgumentParser(description="Sync all timetables from SRUX")
    parser.add_argument("--limit", type=int, default=None, help="Limit number of batches to fetch for testing")
    parser.add_argument("--concurrency", type=int, default=15, help="Concurrent request limit")
    args = parser.parse_args()

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    CONVERTED_DIR.mkdir(parents=True, exist_ok=True)

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 TimetableBot/2.0",
        "Accept": "application/json",
    }

    connector = aiohttp.TCPConnector(limit=args.concurrency, ssl=False)
    async with aiohttp.ClientSession(headers=headers, connector=connector) as session:
        options = await fetch_options(session)
        await fetch_rooms(session)

        # Build mapping: batch_code -> list of {degree, year}
        batch_meta: dict[str, dict[str, Any]] = {}
        for deg, years in options.items():
            if not isinstance(years, dict):
                continue
            for yr, batches in years.items():
                if not isinstance(batches, list):
                    continue
                for b in batches:
                    b_code = str(b).strip()
                    if not b_code:
                        continue
                    if b_code not in batch_meta:
                        batch_meta[b_code] = {
                            "batch": b_code,
                            "degrees": [],
                            "years": [],
                        }
                    if deg not in batch_meta[b_code]["degrees"]:
                        batch_meta[b_code]["degrees"].append(deg)
                    if yr not in batch_meta[b_code]["years"]:
                        batch_meta[b_code]["years"].append(yr)

        unique_batches = list(batch_meta.keys())
        log.info("Found %d degrees, %d unique batches.", len(options), len(unique_batches))

        if args.limit:
            unique_batches = unique_batches[: args.limit]
            log.info("Limiting download to first %d batches.", len(unique_batches))

        semaphore = asyncio.Semaphore(args.concurrency)
        tasks = [
            fetch_batch_timetable(session, b, semaphore)
            for b in unique_batches
        ]

        log.info("Starting concurrent download of %d batches...", len(tasks))
        start_time = time.time()
        results = await asyncio.gather(*tasks)
        elapsed = time.time() - start_time
        log.info("Downloaded %d batches in %.2f seconds.", len(results), elapsed)

        # Process and write files
        success_count = 0
        missing_count = 0
        error_count = 0
        index_entries: dict[str, Any] = {}

        for batch_code, raw_data, err in results:
            meta = batch_meta.get(batch_code, {"degrees": [], "years": []})
            if raw_data is not None:
                success_count += 1
                raw_path = RAW_DIR / f"{batch_code}.json"
                raw_path.write_text(json.dumps(raw_data, indent=2, ensure_ascii=False), encoding="utf-8")

                converted = convert_srux_to_bot_schedule(raw_data, batch_code)
                conv_path = CONVERTED_DIR / f"{batch_code}.json"
                conv_path.write_text(json.dumps(converted, indent=2, ensure_ascii=False), encoding="utf-8")

                # Collect subjects & faculties for index
                subjects: Set[str] = set()
                faculties: Set[str] = set()
                total_classes = 0
                for day_slots in converted.values():
                    total_classes += len(day_slots)
                    for s in day_slots:
                        if s.get("subject"):
                            subjects.add(s["subject"])
                        if s.get("faculty"):
                            faculties.add(s["faculty"])

                index_entries[batch_code] = {
                    "batch": batch_code,
                    "degrees": meta["degrees"],
                    "years": meta["years"],
                    "total_classes": total_classes,
                    "subjects": sorted(subjects),
                    "faculties": sorted(faculties),
                    "status": "available",
                    "raw_file": f"data/timetables/raw/{batch_code}.json",
                    "converted_file": f"data/timetables/converted/{batch_code}.json",
                }
            else:
                if "404" in (err or ""):
                    missing_count += 1
                    status = "not_found"
                else:
                    error_count += 1
                    status = f"error: {err}"

                index_entries[batch_code] = {
                    "batch": batch_code,
                    "degrees": meta["degrees"],
                    "years": meta["years"],
                    "total_classes": 0,
                    "subjects": [],
                    "faculties": [],
                    "status": status,
                }

        # Write master index
        index_data = {
            "last_synced": datetime.now().isoformat(),
            "total_degrees": len(options),
            "total_unique_batches": len(unique_batches),
            "available_batches": success_count,
            "missing_batches": missing_count,
            "error_batches": error_count,
            "batches": index_entries,
        }
        INDEX_FILE.write_text(json.dumps(index_data, indent=2, ensure_ascii=False), encoding="utf-8")

        log.info("==========================================")
        log.info("SYNC SUMMARY:")
        log.info("  Total batches queried: %d", len(unique_batches))
        log.info("  Available & Converted: %d", success_count)
        log.info("  Missing on SRUX (404): %d", missing_count)
        log.info("  Errors / Timeouts:     %d", error_count)
        log.info("  Master Index written:  %s", INDEX_FILE)
        log.info("==========================================")


if __name__ == "__main__":
    asyncio.run(main())
