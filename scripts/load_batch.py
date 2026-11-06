#!/usr/bin/env python3
"""Utility script to set a specific batch timetable as the primary data/schedule.json.

Usage:
    python scripts/load_batch.py 26CAIBTAIB01
    python scripts/load_batch.py --search "CSE"
    python scripts/load_batch.py --info 26CAIBTAIB01
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
SCHEDULE_FILE = DATA_DIR / "schedule.json"
CONVERTED_DIR = DATA_DIR / "timetables" / "converted"
INDEX_FILE = DATA_DIR / "batches_index.json"
OPTIONS_FILE = DATA_DIR / "timetable_options.json"


def load_index() -> dict:
    if not INDEX_FILE.is_file():
        print(f"Error: Index file not found at {INDEX_FILE}. Run sync_all_timetables.py first.")
        sys.exit(1)
    return json.loads(INDEX_FILE.read_text(encoding="utf-8"))


def search_batches(query: str) -> None:
    index = load_index()
    q = query.strip().lower()
    matches = []
    for b_code, info in index.get("batches", {}).items():
        if (
            q in b_code.lower()
            or any(q in deg.lower() for deg in info.get("degrees", []))
            or any(q in yr.lower() for yr in info.get("years", []))
            or any(q in sub.lower() for sub in info.get("subjects", []))
        ):
            matches.append(info)

    print(f"\nFound {len(matches)} matching batches for '{query}':")
    for m in matches[:25]:
        degs = ", ".join(m.get("degrees", []))
        yrs = ", ".join(m.get("years", []))
        classes = m.get("total_classes", 0)
        print(f"  • {m['batch']:<16} | {degs} ({yrs}) | {classes} classes/week")
    if len(matches) > 25:
        print(f"  ... and {len(matches) - 25} more. Refine your query.")


def show_batch_info(batch_code: str) -> None:
    b_code = batch_code.strip().upper()
    index = load_index()
    info = index.get("batches", {}).get(b_code)
    if not info:
        print(f"Batch '{b_code}' not found in index.")
        return

    print(f"\nBatch: {b_code}")
    print(f"Degrees: {', '.join(info.get('degrees', []))}")
    print(f"Years:   {', '.join(info.get('years', []))}")
    print(f"Classes: {info.get('total_classes', 0)} per week")
    print("\nSubjects:")
    for s in info.get("subjects", []):
        print(f"  - {s}")
    print("\nFaculties:")
    for f in info.get("faculties", []):
        print(f"  - {f}")


def activate_batch(batch_code: str) -> None:
    b_code = batch_code.strip().upper()
    conv_file = CONVERTED_DIR / f"{b_code}.json"
    if not conv_file.is_file():
        print(f"Error: Converted timetable file not found for {b_code} at {conv_file}.")
        return

    shutil.copyfile(conv_file, SCHEDULE_FILE)
    print(f"\n Successfully updated primary {SCHEDULE_FILE.relative_to(BASE_DIR)} with batch {b_code}!")
    show_batch_info(b_code)


def main():
    parser = argparse.ArgumentParser(description="Inspect or set primary timetable batch.")
    parser.add_argument("batch", nargs="?", help="Batch code to activate (e.g. 26CAIBTAIB01)")
    parser.add_argument("--search", "-s", help="Search batches by keyword (degree, year, subject)")
    parser.add_argument("--info", "-i", help="Display details for a specific batch")

    args = parser.parse_args()

    if args.search:
        search_batches(args.search)
    elif args.info:
        show_batch_info(args.info)
    elif args.batch:
        activate_batch(args.batch)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
