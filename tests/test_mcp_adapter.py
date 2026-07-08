from pathlib import Path

import pytest

from docsystem import mcp_server
from docsystem.catalog import build_catalog
from docsystem.config import CONFIG_FILENAME, DEFAULT_CONFIG, load_config
from docsystem.projection import build_projection, write_projection


def adapter_project(tmp_path: Path) -> Path:
    config = DEFAULT_CONFIG.replace("[areas]\n", '[areas]\nworkspace = "."\n')
    project = tmp_path / "project"
    root = project / "plan"
    root.mkdir(parents=True)
    (project / CONFIG_FILENAME).write_text(config, encoding="utf-8")
    (root / "README.md").write_text(
        "---\nid: DOC-001\nrevision: 1\n---\n# Index\n[Target](target.md)\n",
        encoding="utf-8",
    )
    (root / "target.md").write_text(
        """\
---
id: DOC-002
revision: 1
depends_on: [DOC-001]
---
# Target

Summary line.

## Details

Detailed content.
""",
        encoding="utf-8",
    )
    return project


def test_tools_return_structured_payloads_from_the_cli_contract(
    tmp_path: Path,
) -> None:
    project = str(adapter_project(tmp_path))

    readiness = mcp_server.readiness(project)
    assert readiness["schema_version"] == 1
    assert readiness["ready"] is True

    catalog = mcp_server.catalog(project)
    assert [item["path"] for item in catalog["documents"]] == [
        "README.md",
        "target.md",
    ]
    memberships = mcp_server.catalog(project, explain=True)
    assert all(item["state"] == "included" for item in memberships["memberships"])

    packet = mcp_server.context(project, "DOC-002")
    assert packet["target"] == "DOC-002"
    assert [item["id"] for item in packet["documents"]] == ["DOC-002", "DOC-001"]
    assert packet["stats"]["included_documents"] == 2

    navigation = mcp_server.read_document(project, "DOC-002", navigation=True)
    assert navigation.endswith("# Target\n\nSummary line.\n")
    assert "## Details" not in navigation

    rows = mcp_server.dependencies(project, "DOC-002")
    assert rows == [
        {"relation": "depends_on", "peer_id": "DOC-001", "expected_revision": None}
    ]
    reverse = mcp_server.dependencies(project, "DOC-001", reverse=True)
    assert reverse == [
        {"relation": "depends_on", "peer_id": "DOC-002", "expected_revision": None}
    ]

    report = mcp_server.migration_report(project)
    assert report == {"schema_version": 1, "resolved": [], "boundaries": []}

    changed = mcp_server.changes(project)
    assert changed["status"] == "absent"

    table = mcp_server.impact(project, "DOC-001")
    assert "# Impact analysis: DOC-001" in table
    assert "| `DOC-002` | depends_on |" in table


def test_readiness_carries_not_ready_payload_instead_of_raising(
    tmp_path: Path,
) -> None:
    project = adapter_project(tmp_path)
    (project / "plan" / "orphan.md").write_text("# Orphan\n", encoding="utf-8")

    payload = mcp_server.readiness(str(project))
    assert payload["ready"] is False
    assert payload["blocking"]
    assert {issue["path"] for issue in payload["blocking"]} == {"orphan.md"}


def test_cli_errors_surface_as_exceptions(tmp_path: Path) -> None:
    project = adapter_project(tmp_path)

    with pytest.raises(RuntimeError, match="configuration not found"):
        mcp_server.readiness(str(tmp_path / "missing"))
    with pytest.raises(RuntimeError, match="document ID not found: DOC-999"):
        mcp_server.read_document(str(project), "DOC-999")


def test_context_surfaces_projection_fallback_diagnostics(tmp_path: Path) -> None:
    project = adapter_project(tmp_path)
    config = load_config(project)
    write_projection(config, build_projection(build_catalog(config), config))

    # A fresh projection is served on the fast path with no diagnostics.
    fresh = mcp_server.context(str(project), "DOC-002")
    assert fresh["target"] == "DOC-002"
    assert "diagnostics" not in fresh

    # Editing Markdown makes the projection stale; the CLI falls back to direct
    # Markdown on a successful exit, and the warning must reach the client.
    target = project / "plan" / "target.md"
    target.write_text(
        target.read_text(encoding="utf-8").replace(
            "Detailed content.", "Changed content."
        ),
        encoding="utf-8",
    )
    stale = mcp_server.context(str(project), "DOC-002")
    assert stale["target"] == "DOC-002"
    assert stale["diagnostics"] == [
        "WARNING: projection stale; using direct Markdown"
    ]


def test_build_server_registers_every_read_only_tool() -> None:
    pytest.importorskip("mcp")
    server = mcp_server.build_server()
    assert server.name == "docsystem"
