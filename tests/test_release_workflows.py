"""Static invariants of the CI and release workflows, and of the release doc.

These tests do not run GitHub Actions. They assert the properties that make an
irreversible PyPI upload safe, and that a plausible-looking edit could silently
remove: the release trigger, per-job permissions, environment gating, the
tag/version gate, the build-once artifact flow, TestPyPI-before-PyPI ordering,
the pinned smoke interpreter, exact remote/local release-file equality, and the
single-index candidate download whose digest is checked against the local build.

Workflow assertions run against the *code* of each `run:` block with comments
stripped, so a reassuring comment can never satisfy them on its own.

`yaml` is already a runtime dependency of the engine, so no new test-only
dependency is introduced here.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = REPO_ROOT / ".github" / "workflows"
RELEASING_DOC = REPO_ROOT / "docs" / "releasing.md"

TESTPYPI_INDEX = "https://test.pypi.org/simple/"
PYPI_INDEX = "https://pypi.org/simple/"

NODE24_ACTION_REFS = {
    "actions/checkout": "v7",
    "astral-sh/setup-uv": "v7",
    "actions/upload-artifact": "v7",
    "actions/download-artifact": "v8",
}


def load_workflow(name: str) -> dict:
    document = yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    return document


@pytest.fixture(scope="module")
def ci() -> dict:
    return load_workflow("ci.yml")


@pytest.fixture(scope="module")
def release() -> dict:
    return load_workflow("release.yml")


def used_actions(workflow: dict) -> list[str]:
    return [
        step["uses"]
        for job in workflow["jobs"].values()
        for step in job["steps"]
        if "uses" in step
    ]


def steps_text(job: dict) -> str:
    return "\n".join(step.get("run", "") for step in job["steps"])


def steps_code(job: dict) -> str:
    """Every `run:` block of a job with comment lines removed.

    Assertions about behavior must be satisfied by the script itself, not by a
    comment that merely claims the script does the right thing.
    """
    lines = [
        line
        for line in steps_text(job).splitlines()
        if not line.lstrip().startswith("#")
    ]
    return "\n".join(lines)


def shell_commands(job: dict) -> list[str]:
    """The job's shell commands, with backslash continuations joined up."""
    joined = re.sub(r"\\\n\s*", " ", steps_code(job))
    return [line.strip() for line in joined.splitlines() if line.strip()]


def pip_commands(job: dict) -> list[str]:
    return [command for command in shell_commands(job) if re.search(r"\bpip\b", command)]


@pytest.mark.parametrize("name", ["ci.yml", "release.yml"])
def test_workflows_pin_node24_capable_action_majors(name: str) -> None:
    """Node 20 actions are being force-migrated; a one-major bump is not enough."""
    for ref in used_actions(load_workflow(name)):
        action, _, version = ref.partition("@")
        if action in NODE24_ACTION_REFS:
            assert version == NODE24_ACTION_REFS[action], f"{name}: {ref} is not a Node 24 major"


# --- CI -------------------------------------------------------------------


def test_ci_declares_least_privilege_permissions(ci: dict) -> None:
    assert ci["permissions"] == {"contents": "read"}


def test_ci_keeps_every_gate_and_the_adoption_walkthrough(ci: dict) -> None:
    body = steps_text(ci["jobs"]["check"])
    for gate in (
        "uv run pytest",
        "uv run ruff check .",
        "uv lock --check",
        "./scripts/installed_cli_smoke.sh",
        "examples/generic-adopter",
        "python -m docsystem migrate",
        "python -m docsystem context DOC-002",
    ):
        assert gate in body, f"CI lost the {gate!r} gate"


def test_ci_checks_the_sdist_that_the_smoke_test_never_builds(ci: dict) -> None:
    body = steps_text(ci["jobs"]["check"])
    assert "uv build" in body
    assert "documentation_engine-${version}.tar.gz" in body
    assert "twine check --strict" in body


def test_ci_runs_the_cli_utf8_contract_on_windows(ci: dict) -> None:
    windows = ci["jobs"]["windows"]
    assert windows["runs-on"] == "windows-latest"
    assert windows["env"]["PYTHONUTF8"] == "0"
    body = steps_text(windows)
    for contract in (
        "uv run pytest",
        "Remove-Item Env:PYTHONIOENCODING",
        "uv build --wheel",
        "docsystem.exe",
        "--version",
        "context DOC-001",
        "ConvertFrom-Json",
        'read DOC-001 $project --anchor "отсутствует"',
    ):
        assert contract in body, f"Windows CI lost the {contract!r} contract"


# --- Release: trigger and permissions -------------------------------------


def test_release_triggers_only_on_version_tags(release: dict) -> None:
    # `on` is parsed by PyYAML 1.1 rules as the boolean True.
    triggers = release[True]
    assert set(triggers) == {"push"}, "the release workflow must have exactly one trigger"
    assert triggers["push"] == {"tags": ["v*"]}
    assert "workflow_dispatch" not in triggers, "a manual run could burn a version number"
    assert "branches" not in triggers["push"]


def test_release_denies_permissions_by_default(release: dict) -> None:
    assert release["permissions"] == {}


def test_release_grants_contents_read_only_to_the_build_job(release: dict) -> None:
    for name, job in release["jobs"].items():
        grants_contents = job.get("permissions", {}).get("contents")
        if name == "build":
            assert grants_contents == "read"
        else:
            assert grants_contents is None, f"job {name!r} must not read repository contents"


def test_release_grants_oidc_only_to_publishing_jobs(release: dict) -> None:
    oidc = {
        name
        for name, job in release["jobs"].items()
        if job.get("permissions", {}).get("id-token") == "write"
    }
    assert oidc == {"testpypi", "pypi"}


def test_release_never_requests_write_access_beyond_oidc(release: dict) -> None:
    """Tag and GitHub Release creation stay outside this workflow."""
    granted = [
        release["permissions"],
        *(job.get("permissions", {}) for job in release["jobs"].values()),
    ]
    writable = {
        scope
        for permissions in granted
        for scope, level in permissions.items()
        if level == "write"
    }
    assert writable == {"id-token"}, "the only write scope the release may hold is OIDC"


def test_release_publishes_through_approval_gated_environments(release: dict) -> None:
    assert release["jobs"]["testpypi"]["environment"] == "testpypi"
    assert release["jobs"]["pypi"]["environment"] == "pypi"


# --- Release: gate, build-once, ordering ----------------------------------


def test_release_verifies_tag_version_before_any_upload(release: dict) -> None:
    build = steps_text(release["jobs"]["build"])
    assert 'tag_version="${TAG_NAME#v}"' in build
    assert 'if [ "$tag_version" != "$pkg_version" ]; then' in build
    # The tag name reaches the shell as an environment variable, never as a
    # `${{ }}` expansion spliced into the script.
    gate = next(step for step in release["jobs"]["build"]["steps"] if step.get("id") == "gate")
    assert gate["env"] == {"TAG_NAME": "${{ github.ref_name }}"}


def test_release_gate_runs_the_project_checks(release: dict) -> None:
    build = steps_text(release["jobs"]["build"])
    for gate in ("uv run pytest", "uv run ruff check .", "uv lock --check", "installed_cli_smoke"):
        assert gate in build


def test_release_builds_once_and_feeds_both_indexes_the_same_artifact(release: dict) -> None:
    jobs = release["jobs"]
    build = steps_text(jobs["build"])
    assert "rm -rf dist" in build, "the build must not pick up stale local artifacts"
    assert "uv build --out-dir dist" in build
    assert "rm -f dist/.gitignore" in build, "uv's generated marker is not a release artifact"

    uploads = [step for step in jobs["build"]["steps"] if "upload-artifact" in step.get("uses", "")]
    assert len(uploads) == 1
    assert uploads[0]["with"]["if-no-files-found"] == "error"
    artifact = uploads[0]["with"]["name"]

    for name in ("testpypi", "testpypi-smoke", "pypi"):
        job = jobs[name]
        downloads = [
            step for step in job["steps"] if "download-artifact" in step.get("uses", "")
        ]
        assert len(downloads) == 1, f"job {name!r} must consume the built artifact exactly once"
        assert downloads[0]["with"]["name"] == artifact
        assert "uv build" not in steps_text(job), f"job {name!r} must not rebuild the distribution"


def test_release_publishes_to_testpypi_and_smokes_it_before_pypi(release: dict) -> None:
    jobs = release["jobs"]
    assert jobs["testpypi"]["needs"] == "build"
    assert set(jobs["testpypi-smoke"]["needs"]) == {"build", "testpypi"}
    assert set(jobs["pypi"]["needs"]) == {"build", "testpypi-smoke"}


def test_release_targets_testpypi_only_from_the_testpypi_job(release: dict) -> None:
    jobs = release["jobs"]
    testpypi = next(
        step for step in jobs["testpypi"]["steps"] if "gh-action-pypi-publish" in step["uses"]
    )
    assert testpypi["with"]["repository-url"] == "https://test.pypi.org/legacy/"
    assert testpypi["with"]["skip-existing"] is True

    pypi = next(
        step for step in jobs["pypi"]["steps"] if "gh-action-pypi-publish" in step["uses"]
    )
    # Production must target the default index, must fail on an existing
    # version rather than skipping it, and must keep default attestations.
    assert "with" not in pypi


# --- Release: TestPyPI integrity and install smoke ------------------------


def test_testpypi_smoke_compares_published_digests_with_the_built_artifact(release: dict) -> None:
    smoke = steps_code(release["jobs"]["testpypi-smoke"])
    assert "https://test.pypi.org/pypi/{dist_name}/{version}/json" in smoke
    assert "hashlib.sha256" in smoke
    assert 'entry["digests"]["sha256"]' in smoke


def test_testpypi_smoke_requires_remote_and_local_release_files_to_be_exactly_equal(
    release: dict,
) -> None:
    """Missing files, differing digests *and* extra remote files must all fail.

    A subset check would pass a version that TestPyPI serves alongside a file
    this run never built.
    """
    smoke = steps_code(release["jobs"]["testpypi-smoke"])
    assert "if published != local:" in smoke, "the file mapping comparison must be an equality"
    # Both directions of the difference are reported, so neither can be dropped
    # without this failing.
    assert "set(local) - set(published)" in smoke, "missing published files must be detected"
    assert "set(published) - set(local)" in smoke, "extra remote files must be detected"
    assert "is an extra file that this run did not build" in smoke
    assert "error: TestPyPI release files are not exactly the files this run built" in smoke


def test_testpypi_smoke_waits_for_partial_release_file_propagation(release: dict) -> None:
    """A 200 response may precede all files becoming visible on the index."""
    smoke = steps_code(release["jobs"]["testpypi-smoke"])
    assert "if published == local:" in smoke
    assert "waiting for remaining TestPyPI" in smoke
    assert "TestPyPI serves conflicting files for this release version" in smoke


def test_testpypi_smoke_never_resolves_a_package_name_across_both_indexes(release: dict) -> None:
    """Pip gives no strict priority between two indexes; mixing them is confusion."""
    smoke = steps_code(release["jobs"]["testpypi-smoke"])
    assert "--extra-index-url" not in smoke, "a second index makes the candidate source ambiguous"
    for command in pip_commands(release["jobs"]["testpypi-smoke"]):
        assert not (TESTPYPI_INDEX in command and PYPI_INDEX in command), (
            f"pip command resolves against both indexes at once: {command}"
        )
    # The candidate is resolved by name from TestPyPI only, never from PyPI.
    by_name = [
        command
        for command in pip_commands(release["jobs"]["testpypi-smoke"])
        if '"${DIST_NAME}==${VERSION}"' in command
    ]
    assert by_name, "the smoke must pin the exact candidate version"
    for command in by_name:
        assert PYPI_INDEX not in command


def test_testpypi_smoke_downloads_the_candidate_wheel_from_testpypi_alone(release: dict) -> None:
    job = release["jobs"]["testpypi-smoke"]
    downloads = [command for command in pip_commands(job) if "pip download" in command]
    assert len(downloads) == 1, "the candidate must be fetched exactly once"
    download = downloads[0]
    for token in (
        f"--index-url {TESTPYPI_INDEX}",
        "--no-cache-dir",
        "--no-deps",
        "--only-binary=:all:",
        '--dest "$CANDIDATE_DIR"',
        '"${DIST_NAME}==${VERSION}"',
    ):
        assert token in download, f"candidate download lost {token!r}: {download}"
    # A fresh directory, so a stale file can never be mistaken for the candidate.
    assert 'rm -rf "$CANDIDATE_DIR"' in steps_code(job)


def test_testpypi_smoke_verifies_the_downloaded_wheel_against_the_local_build(
    release: dict,
) -> None:
    smoke = steps_code(release["jobs"]["testpypi-smoke"])
    assert 'candidate_digest = hashlib.sha256(candidate.read_bytes()).hexdigest()' in smoke
    assert 'built_digest = hashlib.sha256(built.read_bytes()).hexdigest()' in smoke
    assert "if candidate_digest != built_digest:" in smoke, (
        "the downloaded wheel must be compared with the wheel this run built"
    )
    # ...and with the digest TestPyPI already reported for it.
    assert 'if published[candidate.name] != candidate_digest:' in smoke
    assert 'if candidate.suffix != ".whl":' in smoke


def test_testpypi_smoke_installs_the_verified_wheel_file_with_pypi_for_dependencies(
    release: dict,
) -> None:
    job = release["jobs"]["testpypi-smoke"]
    installs = [command for command in pip_commands(job) if "pip install" in command]
    assert len(installs) == 1, "only the verified wheel file may be installed"
    install = installs[0]
    assert '"$CANDIDATE_WHEEL"' in install, "the install must consume the verified wheel file"
    assert f"--index-url {PYPI_INDEX}" in install, "dependencies resolve from PyPI only"
    assert "test.pypi.org" not in install

    smoke = steps_code(job)
    assert '"$SMOKE_VENV/bin/docsystem" init .' in smoke
    assert '"$SMOKE_VENV/bin/docsystem" doctor .' in smoke


def test_testpypi_smoke_pins_its_own_python_312(release: dict) -> None:
    """`ubuntu-latest` moves; the system `python3` must not decide what we test."""
    job = release["jobs"]["testpypi-smoke"]
    setups = [
        step
        for step in job["steps"]
        if "astral-sh/setup-uv" in step.get("uses", "")
    ]
    assert len(setups) == 1, "the smoke job must set up its interpreter explicitly"
    assert setups[0]["with"]["python-version"] == "3.12"
    assert setups[0]["with"]["enable-cache"] is False

    code = steps_code(job)
    assert 'uv venv "$SMOKE_VENV" --python 3.12' in code
    assert "python3" not in code, "the smoke must not fall back to the runner's system interpreter"


# --- Release documentation ------------------------------------------------


def test_releasing_doc_never_permits_moving_or_reusing_a_pushed_tag() -> None:
    # Markdown wraps and emphasises; compare against a flat, unstyled rendering
    # so a line break or a pair of asterisks cannot hide a missing rule.
    doc = re.sub(r"[\s*]+", " ", RELEASING_DOC.read_text(encoding="utf-8").lower())

    assert "a pushed release tag is never moved and never reused" in doc
    for permitted in ("re-tag", "retag", "move the tag", "moving the tag", "reuse the tag"):
        assert permitted not in doc, f"docs/releasing.md must not mention {permitted!r} at all"

    # A rerun is only ever a transient-infrastructure remedy, on the same commit
    # and the same bytes...
    assert "transient infrastructure failure" in doc
    assert "the same immutable commit" in doc
    assert "identical artifacts" in doc
    # ...any source or artifact change is a new version and a new tag...
    assert "bump `__version__`" in doc
    assert "push a new tag" in doc
    # ...and once a version has reached an index, recovery is yank plus a new one.
    assert "yank the bad version and publish a new one" in doc
