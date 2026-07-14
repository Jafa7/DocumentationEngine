import json
from pathlib import Path

from docsystem import mcp_server
from docsystem.cli import build_parser, metadata_inventory
from docsystem.config import CONFIG_FILENAME, DEFAULT_CONFIG


def _project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    root = project / "plan"
    root.mkdir(parents=True)
    config = DEFAULT_CONFIG.replace(
        "[areas]\n", '[areas]\nworkspace = "."\n'
    ).replace(
        'legacy_paths = "strict"', 'legacy_paths = "resolve-with-warning"'
    )
    (project / CONFIG_FILENAME).write_text(config, encoding="utf-8")
    (root / "index.md").write_text(
        """\
---
id: DOC-001
revision: 2
type: index
status: active
depends_on: [DOC-002]
related: [https://example.com/reference]
owner: private-team
weight: 1
labels: [alpha, beta]
policy:
  tier: internal
---
# Index

[Guide](guide.md)
""",
        encoding="utf-8",
    )
    (root / "guide.md").write_text(
        """\
---
id: DOC-002
revision: 1
type: guide
status: draft
owner: another-private-team
weight: high
reviewed: 2026-07-14
---
# Guide
""",
        encoding="utf-8",
    )
    return project


def test_default_inventory_is_body_free_and_hides_additional_values(
    tmp_path: Path, capsys
) -> None:
    project = _project(tmp_path)
    assert metadata_inventory(project, json_output=True) == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert payload["schema_version"] == 1
    assert payload["document_count"] == 2
    assert "private-team" not in captured.out
    assert "another-private-team" not in captured.out
    assert "values" not in payload
    fields = {item["name"]: item for item in payload["fields"]}
    assert fields["owner"] == {
        "name": "owner",
        "category": "additional",
        "present_documents": 2,
        "missing_documents": 0,
        "observed_types": ["string"],
        "document_types": ["guide", "index"],
        "type_conflict": False,
    }
    assert fields["weight"]["observed_types"] == ["integer", "string"]
    assert fields["weight"]["type_conflict"] is True
    assert fields["policy"]["observed_types"] == ["mapping"]
    assert fields["labels"]["observed_types"] == ["sequence"]
    assert fields["reviewed"]["observed_types"] == ["date"]

    documents = {item["id"]: item for item in payload["documents"]}
    assert documents["DOC-001"]["graph"]["outgoing"] == 2
    assert documents["DOC-002"]["graph"]["incoming"] == 2
    assert documents["DOC-001"]["graph"]["boundaries"] >= 1
    assert documents["DOC-001"]["additional_fields"] == [
        "labels",
        "owner",
        "policy",
        "weight",
    ]


def test_explicit_field_drilldown_is_deterministic(tmp_path: Path, capsys) -> None:
    project = _project(tmp_path)
    assert metadata_inventory(
        project, field_name="owner", show_values=True, json_output=True
    ) == 0
    payload = json.loads(capsys.readouterr().out)
    assert [item["name"] for item in payload["fields"]] == ["owner"]
    assert payload["values"] == [
        {
            "id": "DOC-001",
            "path": "index.md",
            "type": "string",
            "value": "private-team",
        },
        {
            "id": "DOC-002",
            "path": "guide.md",
            "type": "string",
            "value": "another-private-team",
        },
    ]


def test_inventory_rejects_ambiguous_or_invalid_requests(
    tmp_path: Path, capsys
) -> None:
    project = _project(tmp_path)
    assert metadata_inventory(project, show_values=True, json_output=True) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "--values requires --field" in captured.err

    assert metadata_inventory(project, field_name="missing", json_output=True) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "metadata field not found: missing" in captured.err

    (project / "plan" / "guide.md").write_text(
        "---\nid: DOC-002\nrevision: 1\ndepends_on: [DOC-999]\n---\n# Guide\n",
        encoding="utf-8",
    )
    assert metadata_inventory(project, json_output=True) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "references unknown ID DOC-999" in captured.err


def test_inventory_cli_parser_and_mcp_contract(tmp_path: Path) -> None:
    args = build_parser().parse_args(
        [
            "metadata-inventory",
            "/project",
            "--field",
            "owner",
            "--values",
            "--json",
            "--source",
            "private",
        ]
    )
    assert args.command == "metadata-inventory"
    assert args.project == Path("/project")
    assert args.field_name == "owner"
    assert args.values is True
    assert args.json_output is True
    assert args.workspace_source == "private"

    project = _project(tmp_path)
    payload = mcp_server.metadata_inventory(
        str(project), field="owner", values=True
    )
    assert [item["id"] for item in payload["values"]] == ["DOC-001", "DOC-002"]
    assert mcp_server.metadata_inventory in mcp_server._TOOLS

    empty = mcp_server.metadata_inventory(
        str(project), field="validated_against", values=True
    )
    assert empty["values"] == []
