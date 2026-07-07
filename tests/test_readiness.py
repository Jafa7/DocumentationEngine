import json
import os
import subprocess
import sys
from pathlib import Path

from docsystem.catalog import build_catalog
from docsystem.cli import index_projection, readiness
from docsystem.config import CONFIG_FILENAME, DEFAULT_CONFIG, load_config
from docsystem.readiness import evaluate_readiness


def minimal_project(tmp_path: Path) -> Path:
    config = DEFAULT_CONFIG.replace("[areas]\n", '[areas]\nworkspace = "."\n')
    (tmp_path / CONFIG_FILENAME).write_text(config, encoding="utf-8")
    root = tmp_path / "plan"
    root.mkdir()
    (root / "README.md").write_text(
        "---\nid: DOC-001\nrevision: 1\n---\n# Index\n", encoding="utf-8"
    )
    return root


def test_readiness_reports_missing_documentation_root(tmp_path: Path, capsys) -> None:
    (tmp_path / CONFIG_FILENAME).write_text(DEFAULT_CONFIG, encoding="utf-8")

    assert readiness(tmp_path) == 1
    captured = capsys.readouterr()
    assert "Next safe command: docsystem init" in captured.out
    assert "documentation root does not exist" not in captured.out
    assert "ERROR: documentation root does not exist" in captured.err


def test_readiness_distinguishes_blocking_errors_from_advisory_categories(
    tmp_path: Path, capsys
) -> None:
    (tmp_path / CONFIG_FILENAME).write_text(DEFAULT_CONFIG, encoding="utf-8")
    root = tmp_path / "plan"
    root.mkdir()
    (root / "orphan.md").write_text("# Orphan\n", encoding="utf-8")

    assert readiness(tmp_path) == 1
    captured = capsys.readouterr()
    assert "Blocking structural/configuration errors: 1" in captured.out
    assert "Next safe command: docsystem doctor" in captured.out
    assert "Markdown is not mapped to a configured area" not in captured.out
    assert "Markdown is not mapped to a configured area" in captured.err


def test_readiness_reports_stale_pins_as_advisory_not_blocking(
    tmp_path: Path, capsys
) -> None:
    root = minimal_project(tmp_path)
    (root / "README.md").write_text(
        (root / "README.md")
        .read_text(encoding="utf-8")
        .replace("revision: 1", "revision: 2")
        .replace("# Index\n", "# Index\n[Review](review.md)\n"),
        encoding="utf-8",
    )
    (root / "review.md").write_text(
        "---\nid: DOC-002\nrevision: 1\nvalidated_against: [DOC-001@1]\n---\n# Review\n",
        encoding="utf-8",
    )

    assert readiness(tmp_path) == 0
    captured = capsys.readouterr()
    assert "Blocking structural/configuration errors: 0" in captured.out
    assert "Stale freshness pins: 1" in captured.out
    assert "DOC-001@1 is stale" not in captured.out
    assert "DOC-001@1 is stale" in captured.err


def test_readiness_projection_state_transitions(tmp_path: Path, capsys) -> None:
    minimal_project(tmp_path)

    assert readiness(tmp_path) == 0
    assert "Projection: absent" in capsys.readouterr().out

    assert index_projection(tmp_path, write=True) == 0
    capsys.readouterr()
    assert readiness(tmp_path) == 0
    assert "Projection: current" in capsys.readouterr().out

    config = load_config(tmp_path)
    catalog = build_catalog(config)
    report = evaluate_readiness(config, catalog)
    assert report.projection_state == "current"
    assert report.ready is True
    assert report.next_command(str(tmp_path)) == f"docsystem context DOCUMENT_ID {tmp_path}"


def test_readiness_json_reports_missing_documentation_root(
    tmp_path: Path, capsys
) -> None:
    (tmp_path / CONFIG_FILENAME).write_text(DEFAULT_CONFIG, encoding="utf-8")

    assert readiness(tmp_path, json_output=True) == 1
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload == {
        "schema_version": 1,
        "documentation_root_exists": False,
        "ready": False,
        "next_command": f"docsystem init {tmp_path}",
    }
    assert captured.err == ""


def test_readiness_json_carries_full_detail_without_parsing_stderr(
    tmp_path: Path, capsys
) -> None:
    (tmp_path / CONFIG_FILENAME).write_text(DEFAULT_CONFIG, encoding="utf-8")
    root = tmp_path / "plan"
    root.mkdir()
    (root / "orphan.md").write_text("# Orphan\n", encoding="utf-8")

    assert readiness(tmp_path, json_output=True) == 1
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["schema_version"] == 1
    assert payload["documentation_root_exists"] is True
    assert payload["ready"] is False
    assert len(payload["blocking"]) == 1
    blocking = payload["blocking"][0]
    assert blocking["path"] == "orphan.md"
    assert "Markdown is not mapped to a configured area" in blocking["message"]
    assert blocking["severity"] == "error"
    assert payload["resolvable_migrations"] == []
    assert payload["boundaries"] == []
    assert payload["stale_pins"] == []
    assert payload["projection"] == {
        "state": "absent",
        "reason": "projection absent",
    }
    assert payload["next_command"] == f"docsystem doctor {tmp_path}"


def test_cli_readiness_sends_blocking_diagnostics_to_stderr_not_stdout(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / CONFIG_FILENAME).write_text(DEFAULT_CONFIG, encoding="utf-8")
    root = project_root / "plan"
    root.mkdir()
    (root / "orphan.md").write_text("# Orphan\n", encoding="utf-8")

    repo_src = Path(__file__).resolve().parents[1] / "src"
    env = dict(os.environ, PYTHONPATH=str(repo_src))
    unrelated_cwd = tmp_path / "unrelated-cwd"
    unrelated_cwd.mkdir()
    result = subprocess.run(
        [sys.executable, "-m", "docsystem", "readiness", str(project_root)],
        cwd=unrelated_cwd,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "Blocking structural/configuration errors: 1" in result.stdout
    assert "Next safe command: docsystem doctor" in result.stdout
    assert "Markdown is not mapped to a configured area" not in result.stdout
    assert "Markdown is not mapped to a configured area" in result.stderr
    assert list(unrelated_cwd.iterdir()) == []
