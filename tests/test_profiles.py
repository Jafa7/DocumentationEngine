import hashlib
import json
from pathlib import Path

import pytest

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


def _relation_project(
    tmp_path: Path,
    metadata: str,
    *,
    allowed_relations: str | None = "[]",
    required_metadata: str | None = None,
) -> Path:
    project = tmp_path / "project"
    root = project / "plan"
    root.mkdir(parents=True)
    config = DEFAULT_CONFIG.replace(
        "[areas]\n", '[areas]\nworkspace = "."\n'
    ).replace('roadmap = "roadmap"\n', "")
    config += (
        '\n[profiles.spec]\ndocument_types = ["spec"]\n'
        'history_mode = "living"\n'
    )
    if required_metadata is not None:
        config += f'required_metadata = ["{required_metadata}"]\n'
    if allowed_relations is not None:
        config += f"allowed_relations = {allowed_relations}\n"
    (project / CONFIG_FILENAME).write_text(config, encoding="utf-8")
    (root / "README.md").write_text(
        "---\nid: DOC-001\nrevision: 1\n---\n# Index\n\n[Item](item.md)\n",
        encoding="utf-8",
    )
    (root / "item.md").write_text(
        f"---\nid: DOC-002\nrevision: 1\ntype: spec\n{metadata}---\n# Item\n",
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


@pytest.mark.parametrize(
    ("metadata", "relation"),
    [
        pytest.param("depends_on: [DOC-001]\n", "depends_on", id="local"),
        pytest.param(
            'depends_on: ["peer::DOC-001"]\n', "depends_on", id="qualified"
        ),
        pytest.param(
            'validated_against: ["peer::DOC-001@1"]\n',
            "validated_against",
            id="qualified-pinned",
        ),
        pytest.param(
            'related: ["https://example.test/reference"]\n',
            "related",
            id="legacy-boundary",
        ),
    ],
)
def test_empty_relation_allowlist_rejects_every_authored_target_form(
    tmp_path: Path, capsys, metadata: str, relation: str
) -> None:
    project = _relation_project(tmp_path, metadata)

    assert profile_check(project, json_output=True) == 1
    captured = capsys.readouterr()
    assert captured.err == ""
    payload = json.loads(captured.out)
    assert payload["valid"] is False
    assert [(item["code"], item["subject"]) for item in payload["violations"]] == [
        ("relation-not-allowed", relation)
    ]

    assert validate(project) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert f"profile spec: relation-not-allowed ({relation})" in captured.err


@pytest.mark.parametrize(
    ("metadata", "required_metadata"),
    [
        pytest.param(
            'depends_on: ["peer::DOC-001"]\n', "depends_on", id="qualified"
        ),
        pytest.param(
            'validated_against: ["peer::DOC-001@1"]\n',
            "validated_against",
            id="qualified-pinned",
        ),
    ],
)
def test_valid_qualified_relation_satisfies_required_metadata(
    tmp_path: Path,
    capsys,
    metadata: str,
    required_metadata: str,
) -> None:
    project = _relation_project(
        tmp_path,
        metadata,
        allowed_relations=None,
        required_metadata=required_metadata,
    )

    assert profile_check(project, json_output=True) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    assert json.loads(captured.out)["valid"] is True
    assert validate(project) == 0
    captured = capsys.readouterr()
    assert captured.out == "Markdown navigation is valid.\n"
    assert "missing-metadata" not in captured.err


def test_empty_or_malformed_qualified_relation_does_not_satisfy_requirement(
    tmp_path: Path, capsys
) -> None:
    empty = _relation_project(
        tmp_path / "empty",
        "depends_on: []\n",
        allowed_relations=None,
        required_metadata="depends_on",
    )
    assert profile_check(empty, json_output=True) == 1
    payload = json.loads(capsys.readouterr().out)
    assert [(item["code"], item["subject"]) for item in payload["violations"]] == [
        ("missing-metadata", "depends_on")
    ]

    malformed = _relation_project(
        tmp_path / "malformed",
        'depends_on: ["peer::not-an-id"]\n',
        allowed_relations=None,
        required_metadata="depends_on",
    )
    assert profile_check(malformed, json_output=True) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "must use source::stable-ID syntax" in captured.err


def test_selected_source_mcp_profile_check_returns_qualified_violation(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _relation_project(workspace, 'depends_on: ["peer::DOC-001"]\n')
    (workspace / "workspace.toml").write_text(
        """\
version = 1

[[sources]]
name = "example-project"
root = "project"
visibility = "private"
""",
        encoding="utf-8",
    )

    payload = mcp_server.profile_check(
        str(tmp_path / "anchor"),
        source="example-project",
        workspace=str(workspace),
    )

    assert payload["valid"] is False
    assert [(item["code"], item["subject"]) for item in payload["violations"]] == [
        ("relation-not-allowed", "depends_on")
    ]
