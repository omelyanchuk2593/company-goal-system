#!/usr/bin/env python3
"""Check a goal register's mechanical constraints; never grant human approval."""

from __future__ import annotations

import argparse
import calendar
from collections import Counter, defaultdict
import csv
from datetime import date
import json
from pathlib import Path
import re
import sys


COLUMNS = (
    "id", "owner", "category", "horizon", "statement", "start_date", "due_date",
    "status", "criterion", "evidence_expected", "evidence_actual", "pm", "reviewer",
    "referee", "ambition", "company_link",
)
STATES = {"draft", "submitted", "active", "completion_review", "appeal", "achieved", "failed", "cancelled"}
OCCUPYING = {"submitted", "active", "completion_review", "appeal"}
REQUIRED = ("id", "owner", "category", "horizon", "statement", "start_date", "due_date", "criterion", "evidence_expected", "pm", "company_link")
CAPS = {"S": 2, "M": 2, "L": 1}
ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}\Z", re.ASCII)


def add_months(value: date, months: int) -> date:
    """Advance calendar months, clamping the day to the target month's end."""
    index = value.month - 1 + months
    year = value.year + index // 12
    month = index % 12 + 1
    return date(year, month, min(value.day, calendar.monthrange(year, month)[1]))


def audit(path: Path) -> dict:
    """Return JSON-serializable diagnostics. Input errors are ordinary results."""
    result = {
        "file": str(path), "ok": True, "rows": [], "owners": [], "issues": [],
        "limitations": [
            "Mechanical checks only; no feasibility judgment or human approval.",
            "A snapshot cannot verify the historical S -> M -> L establishment gate.",
        ],
    }

    def issue(level: str, code: str, message: str, row: int | None = None, goal_id: str = "") -> None:
        result["issues"].append({"level": level, "code": code, "row": row, "id": goal_id, "message": message})
        if level == "error":
            result["ok"] = False

    seen = set()
    slots = defaultdict(Counter)
    try:
        with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle, strict=True)
            header = reader.fieldnames
            if header is None:
                issue("error", "missing_header", "CSV is empty; a header row is required.")
                return result
            duplicates = sorted(name for name, count in Counter(header).items() if count > 1)
            missing = sorted(set(COLUMNS) - set(header))
            extra = sorted(set(header) - set(COLUMNS))
            if duplicates:
                issue("error", "duplicate_columns", "Duplicate columns: " + ", ".join(duplicates))
            if missing:
                issue("error", "missing_columns", "Missing columns: " + ", ".join(missing))
            if extra:
                issue("error", "unknown_columns", "Unexpected columns: " + ", ".join(extra))
            if duplicates or missing or extra:
                return result
            for record in reader:
                # Row is the record's ending physical line (quoted newlines are valid).
                line = reader.line_num
                if None in record or any(value is None for value in record.values()):
                    issue("error", "malformed_row", "CSV record has extra or missing fields.", line)
                    continue
                raw_statement = record["statement"]
                row = {key: value.strip() for key, value in record.items()}
                goal_id = row["id"]
                state = row["status"]
                result["rows"].append({"row": line, "id": goal_id, "statement_length": len(raw_statement)})

                def error(code: str, message: str) -> None:
                    issue("error", code, message, line, goal_id)

                if not goal_id:
                    error("missing_id", "Every register entry needs an id, including drafts.")
                elif goal_id in seen:
                    error("duplicate_id", "Duplicate id: " + goal_id)
                seen.add(goal_id)
                if state not in STATES:
                    error("unknown_status", "Unknown status: " + (state or "(empty)"))
                if state in OCCUPYING | {"achieved", "failed"}:
                    for field in REQUIRED:
                        if not row[field] and field != "id":
                            error("required_field", "Required field is empty: " + field)
                if len(raw_statement) >= 250:
                    error("statement_length", f"Statement has {len(raw_statement)} Unicode characters; limit is strictly fewer than 250, including spaces.")
                horizon = row["horizon"]
                if horizon and horizon not in CAPS:
                    error("invalid_horizon", "Horizon must be S, M or L.")
                ambition = row["ambition"]
                if ambition and ambition not in {"1", "2", "3", "4", "5"}:
                    error("invalid_ambition", "Ambition, when supplied, must be an integer from 1 through 5.")
                if ambition in {"1", "2"} and state in {"active", "completion_review", "appeal", "achieved"}:
                    issue("warning", "ambition_revision_expected", "Source method expects revision for ambition 1–2; check policy/PM decision. Status was not changed.", line, goal_id)
                dates = {}
                for field in ("start_date", "due_date"):
                    if row[field]:
                        try:
                            if not ISO_DATE.fullmatch(row[field]):
                                raise ValueError("not ISO")
                            dates[field] = date.fromisoformat(row[field])
                        except ValueError:
                            error("invalid_date", field + " must be a real ISO date (YYYY-MM-DD).")
                if len(dates) == 2:
                    start, due = dates["start_date"], dates["due_date"]
                    if due <= start:
                        error("date_order", "due_date must be later than start_date.")
                    elif horizon in CAPS:
                        # Date.max can be valid input; compare safely beyond the supported year.
                        def boundary(months: int) -> date:
                            try:
                                return add_months(start, months)
                            except ValueError:
                                return date.max
                        two, six, twelve = boundary(2), boundary(6), boundary(12)
                        valid = {"S": due <= two, "M": two < due <= six, "L": six < due <= twelve}[horizon]
                        if not valid:
                            error("horizon_dates", "Dates do not match the calendar-month convention: S <= 2; M > 2 and <= 6; L > 6 and <= 12 (month-end clamping).")
                if state in OCCUPYING and row["owner"]:
                    slots[row["owner"]]["total"] += 1
                    if horizon in CAPS:
                        slots[row["owner"]][horizon] += 1
                if state == "achieved":
                    if not row["evidence_actual"]:
                        error("missing_evidence", "An achieved goal requires evidence_actual.")
                    issue("warning", "human_acceptance_unverified", "Human acceptance cannot be verified from this CSV. Evidence text and role names do not prove approval.", line, goal_id)
    except (OSError, UnicodeError, csv.Error) as exc:
        issue("error", "input_error", f"Cannot read valid UTF-8 CSV: {exc}")
    for owner, counts in sorted(slots.items()):
        result["owners"].append({"owner": owner, "total": counts["total"], **{key: counts[key] for key in CAPS}})
        if counts["total"] > 5:
            issue("error", "owner_cap", f"{owner}: {counts['total']} occupied slots; maximum is 5.")
        for horizon, cap in CAPS.items():
            if counts[horizon] > cap:
                issue("error", "horizon_cap", f"{owner}: {horizon} has {counts[horizon]} occupied slots; maximum is {cap}.")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv_file", type=Path, help="UTF-8 goal register CSV")
    parser.add_argument("--json", action="store_true", help="Machine-readable report")
    args = parser.parse_args(argv)
    report = audit(args.csv_file)
    if args.json:
        print(json.dumps(report, ensure_ascii=True, indent=2))
    else:
        for row in report["rows"]:
            print(f"Row {row['row']} [{row['id']}]: statement length = {row['statement_length']} / 249")
        for entry in report["issues"]:
            location = f" row {entry['row']}" if entry["row"] else ""
            print(f"{entry['level'].upper()} [{entry['code']}]{location}: {entry['message']}")
        errors = sum(item["level"] == "error" for item in report["issues"])
        warnings = sum(item["level"] == "warning" for item in report["issues"])
        print(f"{'PASS' if report['ok'] else 'FAIL'}: {len(report['rows'])} records, {errors} errors, {warnings} warnings. Mechanical checks only; no feasibility judgment or approval.")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
