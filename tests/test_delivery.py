import hashlib
import json
from pathlib import Path

import pytest

from docsystem import mcp_server
from docsystem.cli import build_parser, delivery_map, doctor, validate
from docsystem.config import CONFIG_FILENAME, DEFAULT_CONFIG

POLICY = """
[profiles.roadmap]
document_types = ["roadmap"]
required_roles = ["completion"]
[profiles.roadmap.roles]
completion = ["completion-evidence", "completion"]

[traceability]
metadata_field = "delivers"
document_types = ["roadmap"]
evidence_role = "completion"
terminal_statuses = ["completed"]
"""


def _project(tmp_path: Path, *, policy: bool = True) -> Path:
    project = tmp_path / "project"
    root = project / "plan"
    (root / "architecture").mkdir(parents=True)
    (root / "roadmap").mkdir()
    config = (
        DEFAULT_CONFIG.replace("[areas]\n", '[areas]\nworkspace = "."\n')
        .replace('architecture = "architecture"\n', "")
        .replace('roadmap = "roadmap"\n', "")
    )
    if policy:
        config = config.replace("[traceability]\n\n", "") + POLICY
    (project / CONFIG_FILENAME).write_text(config, encoding="utf-8")
    (root / "README.md").write_text(
        "---\nid: DOC-000\nrevision: 1\ntype: index\nstatus: active\n---\n"
        "# Index\n\n[Contract](architecture/contract.md)\n"
        "[One](roadmap/one.md)\n[Two](roadmap/two.md)\n[Three](roadmap/three.md)\n",
        encoding="utf-8",
    )
    (root / "architecture" / "contract.md").write_text(
        "---\nid: DOC-001\nrevision: 1\ntype: architecture\nstatus: active\n---\n"
        '# Architecture\n\n<a id="contract"></a>\n## Contract\n\nPrivate body.\n',
        encoding="utf-8",
    )
    (root / "roadmap" / "one.md").write_text(
        "---\nid: RM-001\nrevision: 1\ntype: roadmap\nstatus: completed\n"
        "delivers: [DOC-001#contract]\n---\n# One\n\n"
        '<a id="completion-evidence"></a>\n## Evidence\n\nDone.\n',
        encoding="utf-8",
    )
    (root / "roadmap" / "two.md").write_text(
        "---\nid: RM-002\nrevision: 1\ntype: roadmap\nstatus: active\n"
        "delivers: [DOC-001#contract]\n---\n# Two\n\n"
        '<a id="completion"></a>\n## Completion\n\nPending.\n',
        encoding="utf-8",
    )
    (root / "roadmap" / "three.md").write_text(
        "---\nid: RM-003\nrevision: 1\ntype: roadmap\nstatus: active\n---\n"
        '# Three\n\n<a id="completion"></a>\n## Completion\n\nUntracked.\n',
        encoding="utf-8",
    )
    return project


def test_delivery_map_is_body_free_deterministic_and_gradual(
    tmp_path: Path, capsys
) -> None:
    project = _project(tmp_path)
    before = {
        path: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in project.rglob("*")
        if path.is_file()
    }
    assert delivery_map(project, json_output=True) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    assert "Private body" not in captured.out
    payload = json.loads(captured.out)
    assert payload["configured"] is True
    assert payload["valid"] is True
    assert payload["metadata_field"] == "delivers"
    assert payload["untracked_documents"] == ["RM-003"]
    assert payload["overlaps"] == ["DOC-001#contract"]
    assert payload["mappings"] == [
        {
            "source": "DOC-001#contract",
            "owner_id": "RM-001",
            "owner_path": "roadmap/one.md",
            "owner_status": "completed",
            "evidence": "RM-001#completion-evidence",
            "disposition": "delivered",
        },
        {
            "source": "DOC-001#contract",
            "owner_id": "RM-002",
            "owner_path": "roadmap/two.md",
            "owner_status": "active",
            "evidence": "RM-002#completion",
            "disposition": "active",
        },
    ]
    after = {
        path: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in project.rglob("*")
        if path.is_file()
    }
    assert after == before
    assert delivery_map(project, json_output=True) == 0
    assert capsys.readouterr().out == captured.out


@pytest.mark.parametrize(
    ("value", "code"),
    [
        ("DOC-001#contract", "invalid-delivery-field"),
        ("[]", "empty-delivery-field"),
        ("[DOC-001#contract, DOC-001#contract]", "duplicate-source-address"),
        ("[DOC-001]", "document-only-source"),
        ("[DOC-999#contract]", "unknown-source-document"),
        ("[DOC-001#missing]", "unknown-source-anchor"),
        ('["#contract"]', "invalid-source-address"),
        ("[RM-001#completion-evidence]", "self-delivery"),
        ("[1]", "invalid-source-address"),
    ],
)
def test_delivery_map_rejects_invalid_authored_mappings(
    tmp_path: Path, capsys, value: str, code: str
) -> None:
    project = _project(tmp_path)
    path = project / "plan" / "roadmap" / "one.md"
    text = path.read_text(encoding="utf-8")
    path.write_text(
        text.replace("[DOC-001#contract]", value), encoding="utf-8"
    )
    assert delivery_map(project, json_output=True) == 1
    payload = json.loads(capsys.readouterr().out)
    assert code in [item["code"] for item in payload["violations"]]
    assert validate(project) == 1
    validation = capsys.readouterr()
    assert validation.out == ""
    assert f"delivery {code}" in validation.err
    assert doctor(project) == 1
    capsys.readouterr()


def test_missing_or_ambiguous_evidence_role_is_a_violation(
    tmp_path: Path, capsys
) -> None:
    project = _project(tmp_path)
    path = project / "plan" / "roadmap" / "one.md"
    text = path.read_text(encoding="utf-8")
    path.write_text(text.replace('id="completion-evidence"', 'id="other"'), encoding="utf-8")
    assert delivery_map(project, json_output=True) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["violations"][0]["code"] == "missing-evidence-role"

    path.write_text(
        text + '\n<a id="completion"></a>\n## Also completion\n',
        encoding="utf-8",
    )
    assert delivery_map(project, json_output=True) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["violations"][0]["code"] == "ambiguous-evidence-role"


def test_absent_policy_and_cli_mcp_contract(tmp_path: Path, capsys) -> None:
    project = _project(tmp_path, policy=False)
    assert delivery_map(project, json_output=True) == 0
    assert json.loads(capsys.readouterr().out) == {
        "schema_version": 1,
        "configured": False,
        "valid": True,
        "metadata_field": None,
        "mappings": [],
        "untracked_documents": [],
        "overlaps": [],
        "violations": [],
    }
    args = build_parser().parse_args(
        ["delivery-map", "/project", "--json", "--source", "private"]
    )
    assert args.command == "delivery-map"
    assert args.workspace_source == "private"
    payload = mcp_server.delivery_map(str(_project(tmp_path / "mcp")))
    assert payload["overlaps"] == ["DOC-001#contract"]
    assert mcp_server.delivery_map in mcp_server._TOOLS
