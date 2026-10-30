#!/usr/bin/env python3
"""Check release structure and common privacy leaks, without external services."""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import subprocess
import sys

from package_skill import ROOT, SKILL_NAME, collect_skill_files


TEXT_SUFFIXES = {".md", ".txt", ".csv", ".json", ".py", ".yml", ".yaml", ".toml"}
ROOT_RELEASE_FILES = {"README.md", "LICENSE", "CHANGELOG.md", "CONTRIBUTING.md", "SECURITY.md", ".gitignore", ".gitattributes", "pyproject.toml"}
RELEASE_DIRS = {"skills", "tools", "tests", "evals", "examples", ".github"}
RISKY_SUFFIXES = {".vtt", ".srt", ".ppt", ".pptx", ".pptm", ".doc", ".docx", ".pdf", ".png", ".jpg", ".jpeg", ".webp", ".mp3", ".mp4", ".wav", ".pem", ".p12", ".key"}
PATTERNS = {
    "private key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "API credential": re.compile(r"\b(?:sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{20,}|AKIA[A-Z0-9]{16}|AIza[A-Za-z0-9_-]{30,})\b"),
    "personal home path": re.compile(r"(?:[A-Za-z]:[\\/](?:Users|Documents and Settings)[\\/][^\s/\\]+|/(?:Users|home)/[A-Za-z0-9_.-]+)"),
}
EMAIL = re.compile(r"\b[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+)\b")


def release_files(root: Path) -> list[Path]:
    """Scan explicit release locations plus tracked files, never adjacent sources."""
    paths = set()
    for name in ROOT_RELEASE_FILES:
        path = root / name
        if path.exists():
            paths.add(path)
    for name in RELEASE_DIRS:
        directory = root / name
        if directory.exists():
            for path in directory.rglob("*"):
                if "__pycache__" not in path.parts and path.suffix not in {".pyc", ".pyo"} and (path.is_file() or path.is_symlink()):
                    paths.add(path)
    # Only consult a Git repository rooted here, not an unrelated parent repository.
    if (root / ".git").exists():
        try:
            result = subprocess.run(["git", "-C", str(root), "ls-files", "-z"], check=True, capture_output=True)
            for name in result.stdout.decode("utf-8").split("\0"):
                if name:
                    path = root / name
                    if path.exists() or path.is_symlink():
                        paths.add(path)
        except (OSError, UnicodeError, subprocess.CalledProcessError) as exc:
            raise ValueError("Cannot enumerate tracked release files: " + str(exc)) from exc
    return sorted(paths)


def check(root: Path = ROOT) -> list[str]:
    errors = []
    root = Path(root).resolve()
    source = root / "skills" / SKILL_NAME
    try:
        skill_files = collect_skill_files(source)
    except (OSError, ValueError) as exc:
        errors.append(str(exc))
        skill_files = {}
    if "SKILL.md" in skill_files:
        try:
            skill = skill_files["SKILL.md"].decode("utf-8")
            if not skill.startswith("---\n") and not skill.startswith("---\r\n"):
                errors.append("SKILL.md must begin with YAML frontmatter.")
            frontmatter = re.split(r"^---\s*$", skill, flags=re.MULTILINE)
            if len(frontmatter) < 3:
                errors.append("SKILL.md needs closed YAML frontmatter.")
            else:
                if not re.search(r"^name:\s*[\"']?company-goal-system[\"']?\s*$", frontmatter[1], re.MULTILINE):
                    errors.append("SKILL.md frontmatter must declare name: company-goal-system.")
                if not re.search(r"^description:\s*\S", frontmatter[1], re.MULTILINE):
                    errors.append("SKILL.md frontmatter needs a description.")
        except UnicodeError:
            errors.append("SKILL.md must use UTF-8.")
    for relative, payload in skill_files.items():
        if not relative.endswith(".md"):
            continue
        try:
            content = payload.decode("utf-8")
        except UnicodeError:
            continue  # The text scan below reports invalid encodings.
        for target in re.findall(r"\[[^\]]*\]\(([^)]+)\)", content):
            target = target.strip().strip("<>").split("#", 1)[0]
            if not target or re.match(r"[A-Za-z][A-Za-z0-9+.-]*:", target):
                continue
            resolved = (source / relative).parent.joinpath(target).resolve()
            if source.resolve() not in resolved.parents and resolved != source.resolve():
                errors.append(relative + ": local link leaves the skill folder: " + target)
            elif not resolved.is_file():
                errors.append(relative + ": missing local link target: " + target)
    for required in ("README.md", "LICENSE"):
        if not (root / required).is_file():
            errors.append("Missing release file: " + required)
    try:
        candidates = release_files(root)
    except (OSError, ValueError) as exc:
        errors.append(str(exc))
        candidates = []
    for path in candidates:
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            errors.append(relative + ": symlinks are excluded from releases.")
            continue
        if path.suffix.lower() in RISKY_SUFFIXES or path.name.lower().startswith(".env") or path.name.lower() in {"credentials.json", "secrets.json"}:
            errors.append(relative + ": source media or sensitive file type is excluded.")
            continue
        if path.suffix.lower() not in TEXT_SUFFIXES and path.name not in ROOT_RELEASE_FILES:
            errors.append(relative + ": unrecognized release file type.")
            continue
        try:
            content = path.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeError) as exc:
            errors.append(relative + ": cannot read UTF-8 text (" + str(exc) + ").")
            continue
        for label, pattern in PATTERNS.items():
            if pattern.search(content):
                errors.append(relative + ": possible " + label + "; review and redact.")
        for match in EMAIL.finditer(content):
            domain = match.group(1).lower()
            if domain not in {"example.com", "example.org", "example.net"} and not domain.endswith(".invalid"):
                errors.append(relative + ": non-example email address; review and redact.")
                break
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)
    errors = check()
    for message in errors:
        print("ERROR: " + message)
    if errors:
        print(f"FAIL: {len(errors)} release issues.")
        return 1
    print("PASS: release structure and common privacy patterns checked.")
    print("Manual review still required for private names, company cases and context; pattern checks cannot certify anonymization.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
