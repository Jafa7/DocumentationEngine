import hashlib
import json
from pathlib import Path

from docsystem import mcp_server
from docsystem.cli import build_parser, doctor, profile_check, validate
from docsystem.config import CONFIG_FILENAME, DEFAULT_CONFIG

PROFILE_CONFIG = """
[profiles.roadmap]
document_types = ["roadmap"]
history_mode = "immutable-after-state"
required_metadata = ["status", "owner"]
required_roles = ["outcome", "acceptance"]
allowed_relations = ["depends_on"]
allowed_statuses = ["active", "completed"]

[profiles.roadmap.roles]
outcome = ["outcome", "product-outcome"]
acceptance = ["acceptance"]
"""


def _project(tmp_path: Path, *, profiles: bool = True, valid: bool = False) -> Path:
    project = tmp_path / "project"
    root = project / "plan"
    (root / "roadmap").mkdir(parents=True)
    config = DEFAULT_CONFIG.replace(
        "[areas]\n", '[areas]\nworkspace = "."\n'
    ).replace('roadmap = "roadmap"\n', "")
    if profiles:
        config += PROFILE_CONFIG
    (project / CONFIG_FILENAME).write_text(config, encoding="utf-8")
    (root / "README.md").write_text(
        "---\nid: DOC-001\nrevision: 1\ntype: index\nstatus: active\n---\n"
        "# Index\n\n[Roadmap](roadmap/item.md)\n",
        encoding="utf-8",
    )
    if valid:
        metadata = (
            "status: active\nowner: docs-team\ndepends_on: [DOC-001]\n"
        )
        sections = (
            '<a id="product-outcome"></a>\n## Outcome title\n\nGoal.\n\n'
            '<a id="acceptance"></a>\n## Acceptance\n\nChecks.\n'
        )
    else:
        metadata = "status: proposed\nrelated: [DOC-001]\n"
        sections = '<a id="product-outcome"></a>\n## Outcome title\n\nGoal.\n'
    (root / "roadmap" / "item.md").write_text(
        f"---\nid: RM-001\nrevision: 1\ntype: roadmap\n{metadata}---\n"
        f"# Roadmap\n\n{sections}",
        encoding="utf-8",
    )
    return project


def test_profile_check_reports_semantic_violations_without_bodies_or_writes(
    tmp_path: Path, capsys
) -> None:
    project = _project(tmp_path)
    before = {
        path: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in project.rglob("*")
        if path.is_file()
    }
    assert profile_check(project, json_output=True) == 1
    captured = capsys.readouterr()
    assert captured.err == ""
    assert "Goal." not in captured.out
    payload = json.loads(captured.out)
    assert payload["valid"] is False
    assert payload["unprofiled_documents"] == ["DOC-001"]
    assert payload["profiles"] == [
        {
            "name": "roadmap",
            "document_types": ["roadmap"],
            "history_mode": "immutable-after-state",
            "documents": 1,
            "violations": 4,
        }
    ]
    assert [item["code"] for item in payload["violations"]] == [
        "missing-metadata",
        "missing-role",
        "relation-not-allowed",
        "status-not-allowed",
    ]
    assert payload["documents"][1] == {
        "id": "RM-001",
        "path": "roadmap/item.md",
        "type": "roadmap",
        "status": "proposed",
        "profile": "roadmap",
        "history_mode": "immutable-after-state",
        "valid": False,
    }
    after = {
        path: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in project.rglob("*")
        if path.is_file()
    }
    assert after == before

    assert validate(project) == 1
    validation = capsys.readouterr()
    assert validation.out == ""
    assert "profile roadmap: missing-metadata (owner)" in validation.err
    assert doctor(project) == 1
    diagnosis = capsys.readouterr()
    assert diagnosis.out == ""
    assert "profile roadmap: missing-role (acceptance)" in diagnosis.err


def test_profile_alias_can_satisfy_role_and_valid_report_is_deterministic(
    tmp_path: Path, capsys
) -> None:
    project = _project(tmp_path, valid=True)
    assert profile_check(project) == 0
    first = capsys.readouterr()
    assert first.err == ""
    assert "summary\tvalid\ttrue" in first.out
    assert "profile\troadmap" in first.out
    assert "document\tRM-001" in first.out
    assert "Goal." not in first.out
    assert profile_check(project) == 0
    assert capsys.readouterr().out == first.out


def test_absent_registry_is_backward_compatible_and_catalog_errors_fail_closed(
    tmp_path: Path, capsys
) -> None:
    project = _project(tmp_path, profiles=False, valid=True)
    assert profile_check(project, json_output=True) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["valid"] is True
    assert payload["profiles"] == []
    assert payload["unprofiled_documents"] == ["DOC-001", "RM-001"]

    (project / "plan" / "roadmap" / "item.md").write_text(
        "---\nid: RM-001\nrevision: 1\ndepends_on: [DOC-999]\n---\n# Bad\n",
        encoding="utf-8",
    )
    assert profile_check(project, json_output=True) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "references unknown ID DOC-999" in captured.err


def test_profile_check_parser_workspace_selection_and_mcp_payload(
    tmp_path: Path,
) -> None:
    args = build_parser().parse_args(
        ["profile-check", "/project", "--json", "--source", "private"]
    )
    assert args.command == "profile-check"
    assert args.project == Path("/project")
    assert args.json_output is True
    assert args.workspace_source == "private"

    project = _project(tmp_path)
    payload = mcp_server.profile_check(str(project))
    assert payload["valid"] is False
    assert [item["id"] for item in payload["violations"]] == [
        "RM-001",
        "RM-001",
        "RM-001",
        "RM-001",
    ]
    assert mcp_server.profile_check in mcp_server._TOOLS
