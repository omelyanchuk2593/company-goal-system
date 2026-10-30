from __future__ import annotations

import io
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import check_release
import install
import package_skill


SKILL = b"---\nname: company-goal-system\ndescription: Example goals skill.\n---\n\nExample content.\n"


class ToolTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="company-goal-system-test-")).resolve()
        self.temp_parent = self.root.parent
        self.addCleanup(self.cleanup_temp)
        self.source = self.root / "skills" / "company-goal-system"
        self.source.mkdir(parents=True)
        (self.source / "SKILL.md").write_bytes(SKILL)
        (self.root / "README.md").write_text("Public example.\n", encoding="utf-8")
        (self.root / "LICENSE").write_text("Example license.\n", encoding="utf-8")

    def cleanup_temp(self):
        if self.root.is_symlink() or self.root.resolve() != self.root or self.root.resolve().parent != self.temp_parent or not self.root.name.startswith("company-goal-system-test-"):
            raise ValueError("Test temporary cleanup path failed containment checks.")
        shutil.rmtree(self.root)

    def test_project_target_paths_and_missing_project(self):
        paths = install.destinations("both", "project", self.root)
        self.assertEqual(paths, [self.root / ".agents" / "skills" / "company-goal-system", self.root / ".claude" / "skills" / "company-goal-system"])
        with self.assertRaises(ValueError):
            install.destinations("codex", "project")
        with self.assertRaises(ValueError):
            install.destinations("codex", "user", self.root)

    def test_dry_run_creates_nothing(self):
        target = install.destinations("codex", "project", self.root)[0]
        messages = install.install({"SKILL.md": SKILL}, [target], dry_run=True)
        self.assertTrue(messages[0].startswith("Would install"))
        self.assertFalse(target.parent.exists())

    def test_install_is_idempotent_and_preserves_changed_destination(self):
        target = install.destinations("codex", "project", self.root)[0]
        files = {"SKILL.md": SKILL, "assets/example.csv": b"id\n"}
        install.install(files, [target])
        self.assertEqual((target / "assets" / "example.csv").read_bytes(), b"id\n")
        self.assertTrue(install.install(files, [target])[0].startswith("Already identical"))
        (target / "SKILL.md").write_bytes(b"Local customization")
        with self.assertRaisesRegex(ValueError, "Back it up"):
            install.install(files, [target])
        self.assertEqual((target / "SKILL.md").read_bytes(), b"Local customization")

    def test_both_targets_preflight_before_writing(self):
        targets = install.destinations("both", "project", self.root)
        targets[1].mkdir(parents=True)
        (targets[1] / "SKILL.md").write_bytes(b"Local customization")
        with self.assertRaises(ValueError):
            install.install({"SKILL.md": SKILL}, targets)
        self.assertFalse(targets[0].exists())

    def test_extra_destination_file_prevents_overwrite(self):
        target = install.destinations("codex", "project", self.root)[0]
        install.install({"SKILL.md": SKILL}, [target])
        (target / "notes.md").write_text("Keep this", encoding="utf-8")
        with self.assertRaises(ValueError):
            install.install({"SKILL.md": SKILL}, [target])
        self.assertEqual((target / "notes.md").read_text(encoding="utf-8"), "Keep this")

    def test_install_refuses_path_traversal(self):
        target = install.destinations("codex", "project", self.root)[0]
        with self.assertRaises(ValueError):
            install.install({"SKILL.md": SKILL, "../escape.txt": b"bad"}, [target])
        self.assertFalse((target.parent / "escape.txt").exists())
        self.assertFalse(target.exists())

    def test_symlink_in_install_path_is_rejected(self):
        outside = self.root / "outside"
        outside.mkdir()
        try:
            (self.root / ".agents").symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest("Creating symlinks is unavailable on this platform.")
        target = self.root / ".agents" / "skills" / "company-goal-system"
        with self.assertRaises(ValueError):
            install.install({"SKILL.md": SKILL}, [target])
        self.assertFalse((outside / "skills").exists())

    def test_archive_is_deterministic_sorted_and_has_one_skill_root(self):
        files = {"assets/z.csv": b"id\n", "SKILL.md": SKILL, "references/a.md": b"Example"}
        payload = package_skill.archive_bytes(files)
        self.assertEqual(payload, package_skill.archive_bytes(dict(reversed(list(files.items())))))
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            self.assertEqual(archive.namelist(), ["company-goal-system/" + name for name in sorted(files)])
            self.assertTrue(all(info.date_time == (1980, 1, 1, 0, 0, 0) for info in archive.infolist()))

    def test_archive_rejects_traversal_and_windows_paths(self):
        for name in ("../escape", "/absolute", "C:/escape", "a/../b", "a\\b", "a//b", "./SKILL.md"):
            with self.subTest(name=name), self.assertRaises(ValueError):
                package_skill.archive_bytes({name: b"bad"})

    def test_allowlist_skips_caches_and_rejects_source_media(self):
        (self.source / "scripts" / "__pycache__").mkdir(parents=True)
        (self.source / "scripts" / "__pycache__" / "example.pyc").write_bytes(b"cache")
        self.assertEqual(package_skill.collect_skill_files(self.source), {"SKILL.md": SKILL})
        (self.source / "source.vtt").write_text("Source media", encoding="utf-8")
        with self.assertRaises(ValueError):
            package_skill.collect_skill_files(self.source)

    def test_skill_symlink_is_rejected(self):
        target = self.root / "external.md"
        target.write_text("External", encoding="utf-8")
        try:
            (self.source / "link.md").symlink_to(target)
        except OSError:
            self.skipTest("Creating symlinks is unavailable on this platform.")
        with self.assertRaises(ValueError):
            package_skill.collect_skill_files(self.source)

    def test_release_checks_accept_example_and_detect_risky_content(self):
        self.assertEqual(check_release.check(self.root), [])
        (self.root / "README.md").write_text("Example credential: " + "sk-" + "x" * 30, encoding="utf-8")
        self.assertTrue(any("API credential" in error for error in check_release.check(self.root)))

    def test_release_checks_examples_without_git_for_private_patterns_and_media(self):
        self.assertFalse((self.root / ".git").exists())
        examples = self.root / "examples"
        examples.mkdir()
        sample = examples / "example.md"
        sample.write_text("Public example.\n", encoding="utf-8")
        self.assertEqual(check_release.check(self.root), [])
        sample.write_text("Example credential: " + "sk-" + "x" * 30, encoding="utf-8")
        (examples / "source.pptm").write_bytes(b"Excluded source presentation")
        errors = check_release.check(self.root)
        self.assertTrue(any("examples/example.md: possible API credential" in error for error in errors))
        self.assertTrue(any("examples/source.pptm: source media" in error for error in errors))

    def test_release_checks_source_media_without_reading_adjacent_files(self):
        (self.root / "adjacent-source.vtt").write_text("Not selected for release", encoding="utf-8")
        self.assertEqual(check_release.check(self.root), [])
        (self.source / "source.vtt").write_text("Not release content", encoding="utf-8")
        self.assertTrue(any("source media" in error for error in check_release.check(self.root)))

    def test_release_checks_frontmatter(self):
        (self.source / "SKILL.md").write_text("Missing frontmatter", encoding="utf-8")
        self.assertTrue(any("frontmatter" in error for error in check_release.check(self.root)))

    def test_release_checks_missing_or_escaping_skill_links(self):
        (self.source / "SKILL.md").write_bytes(SKILL + b"\n[missing](references/missing.md)\n[escape](../../../README.md)\n")
        errors = check_release.check(self.root)
        self.assertTrue(any("missing local link" in error for error in errors))
        self.assertTrue(any("leaves the skill" in error for error in errors))

    def test_real_package_cli_produces_equivalent_archives(self):
        if not package_skill.SOURCE.joinpath("SKILL.md").is_file():
            self.skipTest("Repository content is still being authored.")
        output = self.root / "dist"
        result = subprocess.run([sys.executable, str(ROOT / "tools" / "package_skill.py"), "--output-dir", str(output)], capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        archive = (output / "company-goal-system.zip").read_bytes()
        self.assertEqual(archive, (output / "company-goal-system.skill").read_bytes())
        self.assertEqual(archive, package_skill.archive_bytes(package_skill.collect_skill_files(package_skill.SOURCE)))

    def test_real_installer_cli_uses_temporary_project_only(self):
        if not package_skill.SOURCE.joinpath("SKILL.md").is_file():
            self.skipTest("Repository content is still being authored.")
        arguments = [sys.executable, str(ROOT / "tools" / "install.py"), "--target", "both", "--scope", "project", "--project", str(self.root)]
        dry_run = subprocess.run(arguments + ["--dry-run"], capture_output=True, text=True, check=False)
        self.assertEqual(dry_run.returncode, 0, dry_run.stderr)
        self.assertFalse((self.root / ".agents").exists())
        installed = subprocess.run(arguments, capture_output=True, text=True, check=False)
        self.assertEqual(installed.returncode, 0, installed.stderr)
        repeated = subprocess.run(arguments, capture_output=True, text=True, check=False)
        self.assertEqual(repeated.returncode, 0, repeated.stderr)
        self.assertIn("Already identical", repeated.stdout)
        for path in install.destinations("both", "project", self.root):
            self.assertEqual(package_skill.collect_skill_files(path), package_skill.collect_skill_files(package_skill.SOURCE))


if __name__ == "__main__":
    unittest.main()
