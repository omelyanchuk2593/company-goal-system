#!/usr/bin/env python3
"""Build deterministic .zip and .skill archives containing only skill files."""

from __future__ import annotations

import argparse
import io
import os
from pathlib import Path, PurePosixPath
import sys
import zipfile


ROOT = Path(__file__).resolve().parents[1]
SKILL_NAME = "company-goal-system"
SOURCE = ROOT / "skills" / SKILL_NAME
ALLOWED_DIRS = {"references", "assets", "scripts", "evals", "agents"}
ALLOWED_SUFFIXES = {".md", ".csv", ".json", ".py", ".yaml", ".yml", ".txt"}


def safe_relative_name(name: str) -> str:
    """Refuse ambiguous or escaping archive members, including Windows paths."""
    path = PurePosixPath(name)
    if not name or "\\" in name or ":" in name or path.is_absolute() or any(part in {"", ".", ".."} for part in name.split("/")):
        raise ValueError("Unsafe relative path: " + name)
    return path.as_posix()


def collect_skill_files(source: Path = SOURCE) -> dict[str, bytes]:
    source = Path(source)
    if source.is_symlink() or not source.is_dir():
        raise ValueError("Skill source must be a real directory: " + str(source))
    files = {}
    for directory, directories, names in os.walk(source, followlinks=False):
        directory_path = Path(directory)
        for name in list(directories):
            child = directory_path / name
            if child.is_symlink():
                raise ValueError("Symlinks are not release files: " + str(child))
            if name == "__pycache__":
                directories.remove(name)
        for name in sorted(names):
            path = directory_path / name
            if path.is_symlink():
                raise ValueError("Symlinks are not release files: " + str(path))
            if path.suffix in {".pyc", ".pyo"} or name == ".DS_Store":
                continue
            relative = safe_relative_name(path.relative_to(source).as_posix())
            parts = PurePosixPath(relative).parts
            allowed = relative == "SKILL.md" or (len(parts) > 1 and parts[0] in ALLOWED_DIRS and path.suffix.lower() in ALLOWED_SUFFIXES)
            if not allowed:
                raise ValueError("File is outside the skill release allowlist: " + relative)
            files[relative] = path.read_bytes()
    if "SKILL.md" not in files:
        raise ValueError("Skill source is missing SKILL.md.")
    return dict(sorted(files.items()))


def archive_bytes(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, content in sorted(files.items()):
            safe_name = safe_relative_name(name)
            info = zipfile.ZipInfo(SKILL_NAME + "/" + safe_name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, content, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    return buffer.getvalue()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "dist")
    args = parser.parse_args(argv)
    try:
        files = collect_skill_files()
        payload = archive_bytes(files)
        args.output_dir.mkdir(parents=True, exist_ok=True)
        for extension in ("zip", "skill"):
            target = args.output_dir / f"{SKILL_NAME}.{extension}"
            target.write_bytes(payload)
            print(f"Created {target} ({len(files)} files)")
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        print("ERROR: " + str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
