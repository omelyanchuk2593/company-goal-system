#!/usr/bin/env python3
"""Install this skill for Codex or Claude without overwriting local changes."""

from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import sys
import tempfile

from package_skill import SKILL_NAME, SOURCE, collect_skill_files, safe_relative_name


def destinations(target: str, scope: str, project: Path | None = None) -> list[Path]:
    if scope == "project":
        if project is None or not project.is_dir():
            raise ValueError("--scope project requires --project PATH pointing to an existing directory.")
        base = project.resolve()
    else:
        if project is not None:
            raise ValueError("--project is only valid with --scope project.")
        base = Path.home().resolve()
    platforms = ("codex", "claude") if target == "both" else (target,)
    return [base / (".agents" if platform == "codex" else ".claude") / "skills" / SKILL_NAME for platform in platforms]


def check_destination(destination: Path, files: dict[str, bytes]) -> bool:
    """Return True when already identical; reject symlinks and any differences."""
    destination = Path(destination)
    for component in (destination, *destination.parents):
        if component.is_symlink():
            raise ValueError("Refusing a symlink in the installation path: " + str(component))
    if not destination.exists():
        return False
    if not destination.is_dir():
        raise ValueError("Installation destination is not a directory: " + str(destination))
    try:
        existing = collect_skill_files(destination)
    except (OSError, ValueError) as exc:
        raise ValueError(f"Existing destination cannot be replaced: {destination}. Back it up and remove it first. ({exc})") from exc
    if existing != files:
        raise ValueError(f"Existing destination differs: {destination}. Back it up and remove it first; no files were overwritten.")
    return True


def install(files: dict[str, bytes], targets: list[Path], dry_run: bool = False) -> list[str]:
    for relative in files:
        safe_relative_name(relative)
    # Preflight every target before installing either target in a --target both run.
    existing = [check_destination(target, files) for target in targets]
    messages = []
    for destination, same in zip(targets, existing):
        if same:
            messages.append("Already identical: " + str(destination))
            continue
        if dry_run:
            messages.append("Would install: " + str(destination))
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        intended_parent = destination.parent.resolve()
        temporary = Path(tempfile.mkdtemp(prefix=".company-goal-system-", dir=destination.parent))
        intended_temporary = temporary.resolve()
        try:
            for relative, content in files.items():
                path = temporary.joinpath(*relative.split("/"))
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
            # Exclusive creation also refuses an empty destination on POSIX, including races.
            destination.mkdir()
            for relative in files:
                path = destination.joinpath(*relative.split("/"))
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("xb") as output:
                    output.write(temporary.joinpath(*relative.split("/")).read_bytes())
        finally:
            if temporary.exists():
                if temporary.is_symlink() or temporary.resolve() != intended_temporary or temporary.resolve().parent != intended_parent or not temporary.name.startswith(".company-goal-system-"):
                    raise ValueError("Temporary cleanup path failed containment checks.")
                shutil.rmtree(temporary)
        messages.append("Installed: " + str(destination))
    return messages


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", required=True, choices=("codex", "claude", "both"))
    parser.add_argument("--scope", default="user", choices=("user", "project"))
    parser.add_argument("--project", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    try:
        files = collect_skill_files(SOURCE)
        for message in install(files, destinations(args.target, args.scope, args.project), args.dry_run):
            print(message)
    except (OSError, ValueError) as exc:
        print("ERROR: " + str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
