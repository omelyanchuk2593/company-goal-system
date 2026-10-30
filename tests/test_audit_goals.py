from __future__ import annotations

import csv
from datetime import date
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "skills" / "company-goal-system" / "scripts" / "audit_goals.py"
SPEC = importlib.util.spec_from_file_location("audit_goals", SCRIPT)
audit_goals = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit_goals)


def goal(**changes):
    row = dict.fromkeys(audit_goals.COLUMNS, "")
    row.update(id="G-1", owner="Example Owner", category="professional", horizon="S", statement="Ship a verified example result", start_date="2026-01-31", due_date="2026-03-31", status="active", criterion="Acceptance metric equals the agreed target", evidence_expected="Result report", pm="Example PM", company_link="Supports the example team priority")
    row.update(changes)
    return row


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.temp_root = Path(tempfile.mkdtemp(prefix="company-goal-system-test-")).resolve()
        self.temp_parent = self.temp_root.parent
        self.addCleanup(self.cleanup_temp)
        self.path = self.temp_root / "goals.csv"

    def cleanup_temp(self):
        if self.temp_root.is_symlink() or self.temp_root.resolve() != self.temp_root or self.temp_root.resolve().parent != self.temp_parent or not self.temp_root.name.startswith("company-goal-system-test-"):
            raise ValueError("Test temporary cleanup path failed containment checks.")
        shutil.rmtree(self.temp_root)

    def write(self, rows):
        with self.path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=audit_goals.COLUMNS)
            writer.writeheader()
            writer.writerows(rows)

    def run_audit(self, *rows):
        self.write(rows)
        return audit_goals.audit(self.path)

    def codes(self, report):
        return [item["code"] for item in report["issues"]]

    def test_statement_249_and_250_unicode_characters(self):
        for length, valid in ((249, True), (250, False)):
            with self.subTest(length=length):
                report = self.run_audit(goal(statement="я" * (length - 1) + "😀"))
                self.assertEqual(report["ok"], valid)
                self.assertEqual(report["rows"][0]["statement_length"], length)

    def test_statement_spaces_are_counted_without_trimming(self):
        report = self.run_audit(goal(statement=" " + "x" * 248 + " "))
        self.assertIn("statement_length", self.codes(report))

    def test_calendar_boundaries_are_disjoint(self):
        for horizon, due, valid in (("S", "2026-03-31", True), ("M", "2026-03-31", False), ("M", "2026-04-01", True), ("M", "2026-07-31", True), ("L", "2026-07-31", False), ("L", "2026-08-01", True), ("L", "2027-01-31", True), ("L", "2027-02-01", False)):
            with self.subTest(horizon=horizon, due=due):
                self.assertEqual(self.run_audit(goal(horizon=horizon, due_date=due))["ok"], valid)

    def test_end_of_month_clamps_including_leap_year(self):
        self.assertEqual(audit_goals.add_months(date(2025, 12, 31), 2), date(2026, 2, 28))
        self.assertEqual(audit_goals.add_months(date(2023, 12, 31), 2), date(2024, 2, 29))
        self.assertEqual(audit_goals.add_months(date(2024, 2, 29), 12), date(2025, 2, 28))
        self.assertTrue(self.run_audit(goal(start_date="2025-12-31", due_date="2026-02-28"))["ok"])
        self.assertFalse(self.run_audit(goal(start_date="2025-12-31", due_date="2026-03-01"))["ok"])

    def test_due_must_follow_start(self):
        for due in ("2026-01-31", "2026-01-30"):
            self.assertIn("date_order", self.codes(self.run_audit(goal(due_date=due))))

    def test_dates_are_real_and_strict_iso(self):
        for due in ("2026-02-30", "20260228", "2026-2-28", "not-a-date"):
            self.assertIn("invalid_date", self.codes(self.run_audit(goal(due_date=due))))

    def test_maximum_date_does_not_crash(self):
        self.assertTrue(self.run_audit(goal(start_date="9999-12-01", due_date="9999-12-31"))["ok"])

    def test_exact_2_2_1_portfolio_passes(self):
        rows = [goal(id=f"G-{i}", horizon=horizon, due_date=due) for i, (horizon, due) in enumerate((("S", "2026-03-31"), ("S", "2026-03-31"), ("M", "2026-07-31"), ("M", "2026-07-31"), ("L", "2027-01-31")))]
        report = self.run_audit(*rows)
        self.assertTrue(report["ok"])
        self.assertEqual(report["owners"][0]["total"], 5)

    def test_total_cap_counts_every_waiting_status(self):
        rows = [goal(id=f"G-{i}", status=state) for i, state in enumerate(("submitted", "active", "completion_review", "appeal", "active", "active"))]
        report = self.run_audit(*rows)
        self.assertEqual(report["owners"][0]["total"], 6)
        self.assertIn("owner_cap", self.codes(report))
        self.assertIn("horizon_cap", self.codes(report))

    def test_each_horizon_cap(self):
        for horizon, count, due in (("S", 3, "2026-03-31"), ("M", 3, "2026-07-31"), ("L", 2, "2027-01-31")):
            report = self.run_audit(*(goal(id=f"G-{i}", horizon=horizon, due_date=due) for i in range(count)))
            self.assertIn("horizon_cap", self.codes(report))
            self.assertNotIn("owner_cap", self.codes(report))

    def test_drafts_and_closed_goals_do_not_occupy_slots(self):
        rows = [goal(id=f"G-{i}", status=state, evidence_actual="Example report") for i, state in enumerate(("draft", "achieved", "failed", "cancelled", "draft", "cancelled"))]
        report = self.run_audit(*rows)
        self.assertTrue(report["ok"])
        self.assertEqual(report["owners"], [])

    def test_owners_have_separate_caps(self):
        report = self.run_audit(*(goal(id=f"G-{i}", owner=f"Owner {i // 2}") for i in range(6)))
        self.assertTrue(report["ok"])

    def test_draft_and_cancelled_can_be_sparse(self):
        for state in ("draft", "cancelled"):
            row = dict.fromkeys(audit_goals.COLUMNS, "")
            row.update(id="G-1", status=state)
            self.assertTrue(self.run_audit(row)["ok"])
            row["start_date"] = "bad"
            self.assertIn("invalid_date", self.codes(self.run_audit(row)))

    def test_non_draft_requires_operational_fields(self):
        for state in (*sorted(audit_goals.OCCUPYING), "achieved", "failed"):
            report = self.run_audit(goal(status=state, criterion="", evidence_actual="Example report"))
            self.assertIn("required_field", self.codes(report))

    def test_achieved_needs_evidence_but_cannot_verify_human_acceptance(self):
        report = self.run_audit(goal(status="achieved"))
        self.assertIn("missing_evidence", self.codes(report))
        report = self.run_audit(goal(status="achieved", evidence_actual="Example report"))
        self.assertTrue(report["ok"])
        self.assertIn("human_acceptance_unverified", self.codes(report))

    def test_ambition_optional_or_single_integer(self):
        for ambition in ("", "1", "5"):
            self.assertTrue(self.run_audit(goal(ambition=ambition))["ok"])
        for ambition in ("0", "6", "1.0", "2.5", "true", "01"):
            self.assertIn("invalid_ambition", self.codes(self.run_audit(goal(ambition=ambition))))

    def test_low_ambition_warns_without_rejecting_or_changing_status(self):
        for state in ("active", "completion_review", "appeal", "achieved"):
            for ambition in ("1", "2"):
                with self.subTest(state=state, ambition=ambition):
                    self.write([goal(status=state, ambition=ambition, evidence_actual="Example report")])
                    before = self.path.read_bytes()
                    report = audit_goals.audit(self.path)
                    self.assertTrue(report["ok"])
                    warnings = [item for item in report["issues"] if item["code"] == "ambition_revision_expected"]
                    self.assertEqual(len(warnings), 1)
                    self.assertEqual(warnings[0]["level"], "warning")
                    self.assertIn("check policy/PM decision", warnings[0]["message"])
                    self.assertEqual(self.path.read_bytes(), before)

    def test_ambition_warning_does_not_cover_pending_drafts_or_passing_scores(self):
        for state in ("draft", "submitted", "failed", "cancelled"):
            self.assertNotIn("ambition_revision_expected", self.codes(self.run_audit(goal(status=state, ambition="1"))))
        for ambition in ("", "3", "4", "5"):
            self.assertNotIn("ambition_revision_expected", self.codes(self.run_audit(goal(ambition=ambition))))

    def test_duplicate_ids_and_unknown_states(self):
        self.assertIn("duplicate_id", self.codes(self.run_audit(goal(), goal(status="draft"))))
        self.assertIn("unknown_status", self.codes(self.run_audit(goal(status="approved"))))
        self.assertIn("missing_id", self.codes(self.run_audit(goal(id="", status="draft"))))

    def test_unknown_horizon_rejected(self):
        self.assertIn("invalid_horizon", self.codes(self.run_audit(goal(horizon="short"))))

    def test_missing_duplicate_and_unknown_columns(self):
        for header, code in (("id,owner\n", "missing_columns"), (",".join(audit_goals.COLUMNS) + ",id\n", "duplicate_columns"), (",".join(audit_goals.COLUMNS) + ",extra\n", "unknown_columns")):
            self.path.write_text(header, encoding="utf-8")
            self.assertIn(code, self.codes(audit_goals.audit(self.path)))

    def test_extra_and_missing_record_fields(self):
        for row in ("G-1,draft\n", ",".join(["value"] * 17) + "\n"):
            self.path.write_text(",".join(audit_goals.COLUMNS) + "\n" + row, encoding="utf-8")
            self.assertIn("malformed_row", self.codes(audit_goals.audit(self.path)))

    def test_malformed_quotes_and_bad_encoding(self):
        for payload in ((",".join(audit_goals.COLUMNS) + '\n"unfinished\n').encode(), b"\xff\xfe\xff"):
            self.path.write_bytes(payload)
            self.assertIn("input_error", self.codes(audit_goals.audit(self.path)))

    def test_empty_and_missing_file_are_errors(self):
        self.assertFalse(audit_goals.audit(self.path)["ok"])
        self.path.write_text("", encoding="utf-8")
        self.assertIn("missing_header", self.codes(audit_goals.audit(self.path)))

    def test_utf8_bom_quoted_comma_and_newline_are_valid(self):
        self.write([goal(statement="Measure, verify\nand accept")])
        self.path.write_bytes(b"\xef\xbb\xbf" + self.path.read_bytes())
        self.assertTrue(audit_goals.audit(self.path)["ok"])

    def test_cli_json_exit_status_and_warnings(self):
        for row, exit_code in ((goal(status="achieved", evidence_actual="Example report"), 0), (goal(ambition="1"), 0), (goal(statement="x" * 250), 1)):
            self.write([row])
            result = subprocess.run([sys.executable, str(SCRIPT), str(self.path), "--json"], capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, exit_code, result.stderr)
            self.assertEqual(json.loads(result.stdout)["ok"], exit_code == 0)
            self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()
