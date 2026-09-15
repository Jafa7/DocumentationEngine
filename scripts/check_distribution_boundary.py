#!/usr/bin/env python3
"""Fail closed when a wheel or sdist contains project-local state."""

from __future__ import annotations

import argparse
import sys
import tarfile
import zipfile
from collections.abc import Iterator
from pathlib import Path, PurePosixPath

PUBLIC_EXAMPLE_CONFIGS = {
    PurePosixPath("examples/generic-adopter/.docsystem.toml"),
    PurePosixPath("examples/provider-snapshots/.docsystem.toml"),
}
ALLOWED_HIDDEN_FILES = {PurePosixPath(".gitignore"), *PUBLIC_EXAMPLE_CONFIGS}


def _archive_files(path: Path) -> Iterator[tuple[str, bytes]]:
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            for member in sorted(archive.infolist(), key=lambda item: item.filename):
                if not member.is_dir():
                    yield member.filename, archive.read(member)
        return
    if tarfile.is_tarfile(path):
        with tarfile.open(path, "r:*") as archive:
            for member in sorted(archive.getmembers(), key=lambda item: item.name):
                if not member.isfile():
                    continue
                stream = archive.extractfile(member)
                if stream is None:
                    raise ValueError(f"cannot read archive member: {member.name}")
                yield member.name, stream.read()
        return
    raise ValueError("unsupported distribution archive")


def _logical_path(archive: Path, member: str) -> PurePosixPath:
    path = PurePosixPath(member)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("unsafe archive member path")
    if archive.name.endswith(".tar.gz"):
        expected_root = archive.name.removesuffix(".tar.gz")
        if len(path.parts) < 2 or path.parts[0] != expected_root:
            raise ValueError(f"sdist member outside expected root {expected_root!r}")
        path = PurePosixPath(*path.parts[1:])
    return path


def _forbidden_reason(path: PurePosixPath) -> str | None:
    if path in ALLOWED_HIDDEN_FILES:
        return None
    hidden = next((part for part in path.parts if part.startswith(".")), None)
    if hidden is not None:
        return f"hidden local path component {hidden!r}"
    name = path.name.casefold()
    if ".local." in name:
        return "conventionally local filename"
    return None


def inspect_archive(path: Path, sentinel: bytes | None = None) -> list[str]:
    """Return deterministic boundary violations for one distribution archive."""

    issues: list[str] = []
    logical_paths: set[PurePosixPath] = set()
    try:
        members = list(_archive_files(path))
    except (OSError, tarfile.TarError, zipfile.BadZipFile, ValueError) as error:
        return [f"{path.name}: {error}"]

    for member, content in members:
        try:
            logical = _logical_path(path, member)
        except ValueError as error:
            issues.append(f"{path.name}: {member}: {error}")
            continue
        logical_paths.add(logical)
        if reason := _forbidden_reason(logical):
            issues.append(f"{path.name}: {logical.as_posix()}: {reason}")
        if sentinel is not None and sentinel in content:
            issues.append(f"{path.name}: {logical.as_posix()}: sentinel content found")

    if path.name.endswith(".tar.gz"):
        for required in sorted(PUBLIC_EXAMPLE_CONFIGS, key=lambda item: item.as_posix()):
            if required not in logical_paths:
                issues.append(
                    f"{path.name}: {required.as_posix()}: required public example missing"
                )
    return sorted(set(issues))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archives", nargs="+", type=Path)
    parser.add_argument("--sentinel-file", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    sentinel = None
    if arguments.sentinel_file is not None:
        sentinel = arguments.sentinel_file.read_bytes()
        if not sentinel:
            print("ERROR: sentinel file must not be empty", file=sys.stderr)
            return 1

    issues = [
        issue
        for archive in arguments.archives
        for issue in inspect_archive(archive, sentinel)
    ]
    if issues:
        for issue in sorted(issues):
            print(f"ERROR: {issue}", file=sys.stderr)
        return 1
    print(f"Distribution boundary verified for {len(arguments.archives)} artifact(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
