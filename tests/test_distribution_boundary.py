from __future__ import annotations

import io
import runpy
import subprocess
import sys
import tarfile
import tomllib
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CHECKER = REPO_ROOT / "scripts" / "check_distribution_boundary.py"
inspect_archive = runpy.run_path(str(CHECKER), run_name="distribution_boundary_checker")[
    "inspect_archive"
]


def _write_sdist(path: Path, files: dict[str, bytes]) -> None:
    with tarfile.open(path, "w:gz") as archive:
        for name, content in sorted(files.items()):
            member = tarfile.TarInfo(f"documentation_engine-0.6.1/{name}")
            member.size = len(content)
            archive.addfile(member, io.BytesIO(content))


def test_packaging_config_is_an_explicit_public_allowlist() -> None:
    config = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    build = config["tool"]["hatch"]["build"]
    sdist = build["targets"]["sdist"]

    assert build["exclude"] == ["**/.*", "**/*.local.*"]
    assert set(sdist["only-include"]) == {
        ".gitignore",
        "CHANGELOG.md",
        "CONTRIBUTING.md",
        "LICENSE",
        "README.md",
        "SECURITY.md",
        "docs",
        "examples",
        "pyproject.toml",
        "scripts",
        "src",
        "tests",
        "uv.lock",
    }
    assert sdist["force-include"] == {
        "examples/generic-adopter/.docsystem.toml": (
            "examples/generic-adopter/.docsystem.toml"
        ),
        "examples/provider-snapshots/.docsystem.toml": (
            "examples/provider-snapshots/.docsystem.toml"
        ),
    }


def test_clean_wheel_and_sdist_pass_with_public_example_configs(tmp_path: Path) -> None:
    wheel = tmp_path / "documentation_engine-0.6.1-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("docsystem/__init__.py", b"safe")
    sdist = tmp_path / "documentation_engine-0.6.1.tar.gz"
    _write_sdist(
        sdist,
        {
            "pyproject.toml": b"safe",
            "examples/generic-adopter/.docsystem.toml": b"public",
            "examples/provider-snapshots/.docsystem.toml": b"public",
        },
    )

    assert inspect_archive(wheel) == []
    assert inspect_archive(sdist) == []


def test_hidden_local_paths_and_sentinel_content_fail_closed(tmp_path: Path) -> None:
    sentinel = b"unique-private-sentinel"
    wheel = tmp_path / "documentation_engine-0.6.1-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("docsystem/.agents/local/secret.txt", sentinel)
        archive.writestr("docsystem/settings.local.json", b"private")

    assert inspect_archive(wheel, sentinel) == [
        "documentation_engine-0.6.1-py3-none-any.whl: "
        "docsystem/.agents/local/secret.txt: hidden local path component '.agents'",
        "documentation_engine-0.6.1-py3-none-any.whl: "
        "docsystem/.agents/local/secret.txt: sentinel content found",
        "documentation_engine-0.6.1-py3-none-any.whl: "
        "docsystem/settings.local.json: conventionally local filename",
    ]


def test_sdist_member_outside_version_root_fails_closed(tmp_path: Path) -> None:
    sdist = tmp_path / "documentation_engine-0.6.1.tar.gz"
    with tarfile.open(sdist, "w:gz") as archive:
        content = b"private"
        member = tarfile.TarInfo(".agents/secret.txt")
        member.size = len(content)
        archive.addfile(member, io.BytesIO(content))

    issues = inspect_archive(sdist)
    assert issues == [
        "documentation_engine-0.6.1.tar.gz: .agents/secret.txt: "
        "sdist member outside expected root 'documentation_engine-0.6.1'",
        "documentation_engine-0.6.1.tar.gz: "
        "examples/generic-adopter/.docsystem.toml: required public example missing",
        "documentation_engine-0.6.1.tar.gz: "
        "examples/provider-snapshots/.docsystem.toml: required public example missing",
    ]


def test_checker_cli_rejects_missing_public_config_and_writes_no_stdout(
    tmp_path: Path,
) -> None:
    sdist = tmp_path / "documentation_engine-0.6.1.tar.gz"
    _write_sdist(sdist, {"pyproject.toml": b"safe"})

    result = subprocess.run(
        [sys.executable, str(CHECKER), str(sdist)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert result.stdout == ""
    assert "required public example missing" in result.stderr
