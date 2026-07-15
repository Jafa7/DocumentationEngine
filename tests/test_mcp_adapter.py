import io
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from docsystem import mcp_server
from docsystem.catalog import build_catalog
from docsystem.cli import agent_instructions as cli_agent_instructions
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


def adapter_workspace(tmp_path: Path) -> tuple[Path, Path]:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    project = adapter_project(workspace)
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
    return workspace, project


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
    assert packet["outline"] is False
    assert packet["documents"][0]["sections"] == [
        {"anchor": "target", "title": "Target", "level": 1, "lines": 7, "bytes": 54},
        {"anchor": "details", "title": "Details", "level": 2, "lines": 3, "bytes": 29},
    ]

    navigation = mcp_server.read_document(project, "DOC-002", navigation=True)
    assert navigation.endswith("# Target\n\nSummary line.\n")
    assert "## Details" not in navigation
    navigation_packet = mcp_server.read_document_packet(
        project, "DOC-002", navigation=True
    )
    assert navigation_packet == {
        "schema_version": 1,
        "text": navigation,
        "diagnostics": ["WARNING: projection absent; using direct Markdown"],
    }

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
    assert mcp_server.impact_packet(project, "DOC-001") == {
        "schema_version": 1,
        "text": table,
        "diagnostics": ["WARNING: projection absent; using direct Markdown"],
    }


def test_readiness_carries_not_ready_payload_instead_of_raising(
    tmp_path: Path,
) -> None:
    project = adapter_project(tmp_path)
    (project / "plan" / "orphan.md").write_text("# Orphan\n", encoding="utf-8")

    payload = mcp_server.readiness(str(project))
    assert payload["ready"] is False
    assert payload["blocking"]
    assert {issue["path"] for issue in payload["blocking"]} == {"orphan.md"}


def test_workspace_selection_is_forwarded_to_the_cli_contract(tmp_path: Path) -> None:
    workspace, project = adapter_workspace(tmp_path)
    anchor = str(tmp_path / "anchor")

    listing = mcp_server.workspace_list(anchor, workspace=str(workspace))
    assert listing["sources"] == [
        {
            "available": True,
            "name": "example-project",
            "reason": None,
            "visibility": "private",
        }
    ]

    catalog = mcp_server.catalog(
        anchor, source="example-project", workspace=str(workspace)
    )
    assert [item["path"] for item in catalog["documents"]] == [
        "README.md",
        "target.md",
    ]

    readiness = mcp_server.readiness(
        anchor, source="example-project", workspace=str(workspace)
    )
    assert readiness["source"] == "example-project"
    assert readiness["ready"] is True
    assert str(project) not in readiness["next_command"]


def test_workspace_arguments_are_omitted_exactly_when_not_requested(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    def invoke(arguments, *, allow_failure_payload=False):
        calls.append(arguments)
        return "{}", ""

    monkeypatch.setattr(mcp_server, "_invoke", invoke)

    assert mcp_server.catalog("/project") == {}
    assert calls.pop() == ["catalog", "/project", "--json"]

    assert mcp_server.catalog(
        "/project", source="example-project", workspace="/workspace"
    ) == {}
    assert calls.pop() == [
        "catalog",
        "/project",
        "--json",
        "--source",
        "example-project",
        "--workspace",
        "/workspace",
    ]


def test_workstream_tools_delegate_to_read_only_json_cli(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    def invoke(arguments, *, allow_failure_payload=False):
        calls.append(arguments)
        return '{"schema_version": 1}', ""

    monkeypatch.setattr(mcp_server, "_invoke", invoke)

    assert mcp_server.criteria("/project") == {"schema_version": 1}
    assert calls.pop() == ["criteria", "/project", "--json"]

    assert mcp_server.roadmap_status("/project", program="RM-001") == {
        "schema_version": 1
    }
    assert calls.pop() == [
        "roadmap",
        "status",
        "/project",
        "--json",
        "--program",
        "RM-001",
    ]

    assert mcp_server.roadmap_next("/project") == {"schema_version": 1}
    assert calls.pop() == ["roadmap", "next", "/project", "--json"]

    assert mcp_server.roadmap_explain(
        "/project", "federated-projection", source="docs"
    ) == {"schema_version": 1}
    assert calls.pop() == [
        "roadmap",
        "explain",
        "federated-projection",
        "/project",
        "--json",
        "--source",
        "docs",
    ]

    assert mcp_server.workstream(
        "/project", "DOC-002", "/tmp/record.json"
    ) == {"schema_version": 1}
    assert calls.pop() == [
        "workstream",
        "DOC-002",
        "/project",
        "--record",
        "/tmp/record.json",
        "--json",
    ]

    assert mcp_server.intake(
        "/project", "/tmp/intake.json"
    ) == {"schema_version": 1}
    assert calls.pop() == [
        "intake",
        "/project",
        "--request",
        "/tmp/intake.json",
        "--json",
    ]

    assert mcp_server.admission(
        "/project", "WS-001", "/tmp/admission.json"
    ) == {"schema_version": 1}
    assert calls.pop() == [
        "admission",
        "WS-001",
        "/project",
        "--request",
        "/tmp/admission.json",
        "--json",
    ]

    assert mcp_server.execution_handoff(
        "/project", "WS-001", "/tmp/admission.json", "/tmp/packet.json"
    ) == {"schema_version": 1}
    assert calls.pop() == [
        "execution-handoff",
        "WS-001",
        "/project",
        "--admission",
        "/tmp/admission.json",
        "--json",
        "--verify",
        "/tmp/packet.json",
    ]

    assert mcp_server.execution_result(
        "/project", "WS-001", "/tmp/packet.json", "/tmp/result.json"
    ) == {"schema_version": 1}
    assert calls.pop() == [
        "execution-result",
        "WS-001",
        "/project",
        "--packet",
        "/tmp/packet.json",
        "--result",
        "/tmp/result.json",
        "--json",
    ]

    assert mcp_server.lifecycle(
        "/project",
        "WS-001",
        "/tmp/admission.json",
        "/tmp/packet.json",
        "/tmp/result.json",
        "/tmp/record.json",
    ) == {"schema_version": 1}
    assert calls.pop() == [
        "lifecycle",
        "WS-001",
        "/project",
        "--admission",
        "/tmp/admission.json",
        "--packet",
        "/tmp/packet.json",
        "--result",
        "/tmp/result.json",
        "--record",
        "/tmp/record.json",
        "--json",
    ]

    assert mcp_server.finish_handoff(
        "/project",
        "DOC-002",
        workstream_record="/tmp/record.json",
        depth=2,
        include_related=True,
    ) == {"schema_version": 1}
    assert calls.pop() == [
        "finish",
        "DOC-002",
        "/project",
        "--depth",
        "2",
        "--json",
        "--include-related",
        "--workstream-record",
        "/tmp/record.json",
    ]


def test_federation_tools_delegate_to_read_only_json_cli(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    def invoke(arguments, *, allow_failure_payload=False):
        calls.append(arguments)
        return '{"schema_version": 1}', ""

    monkeypatch.setattr(mcp_server, "_invoke", invoke)

    assert mcp_server.federation_catalog("/project", "/workspace") == {
        "schema_version": 1
    }
    assert calls.pop() == [
        "federation",
        "catalog",
        "/project",
        "--json",
        "--workspace",
        "/workspace",
    ]

    assert mcp_server.federation_dependencies(
        "/project", "alpha::DOC-001", reverse=True
    ) == {"schema_version": 1}
    assert calls.pop() == [
        "federation",
        "dependencies",
        "alpha::DOC-001",
        "/project",
        "--json",
        "--reverse",
    ]

    assert mcp_server.federation_context(
        "/project",
        "alpha::DOC-001",
        depth=2,
        include_related=True,
        include=["beta::DOC-002#details"],
    ) == {"schema_version": 1}
    assert calls.pop() == [
        "federation",
        "context",
        "alpha::DOC-001",
        "/project",
        "--depth",
        "2",
        "--json",
        "--include-related",
        "--include",
        "beta::DOC-002#details",
    ]

    assert mcp_server.federation_references(
        "/project", "alpha::DOC-001#details", transitive=True
    ) == {"schema_version": 1}
    assert calls.pop() == [
        "federation",
        "references",
        "alpha::DOC-001#details",
        "/project",
        "--json",
        "--transitive",
    ]

    assert mcp_server.federation_impact("/project", "beta::DOC-002") == {
        "schema_version": 1
    }
    assert calls.pop() == [
        "federation",
        "impact",
        "beta::DOC-002",
        "/project",
        "--json",
    ]

    assert mcp_server.federation_index_status("/project", "/workspace") == {
        "schema_version": 1
    }
    assert calls.pop() == [
        "federation",
        "index",
        "/project",
        "--json",
        "--workspace",
        "/workspace",
    ]

    assert mcp_server.federation_changes("/project", "/workspace") == {
        "schema_version": 1
    }
    assert calls.pop() == [
        "federation",
        "changes",
        "/project",
        "--json",
        "--workspace",
        "/workspace",
    ]


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


def test_context_outline_reports_section_size_maps_without_content(
    tmp_path: Path,
) -> None:
    project = adapter_project(tmp_path)

    outline = mcp_server.context(str(project), "DOC-002", outline=True)
    assert outline["outline"] is True
    assert [item["id"] for item in outline["documents"]] == ["DOC-002", "DOC-001"]
    target_document = outline["documents"][0]
    assert set(target_document) == {
        "id",
        "path",
        "revision",
        "relations",
        "sections",
    }
    assert target_document["revision"] == 1
    assert target_document["sections"] == [
        {"anchor": "target", "title": "Target", "level": 1, "lines": 7, "bytes": 54},
        {"anchor": "details", "title": "Details", "level": 2, "lines": 3, "bytes": 29},
    ]
    assert outline["stats"] == {
        "included_documents": 2,
        "listed_sections": 3,
        "total_section_bytes": 110,
    }

    with pytest.raises(RuntimeError, match="cannot combine --outline"):
        mcp_server.context(str(project), "DOC-002", outline=True, anchor="details")
    with pytest.raises(ValueError, match="compact cannot combine with outline"):
        mcp_server.context(str(project), "DOC-002", outline=True, compact=True)

    compact = mcp_server.context(
        str(project), "DOC-002", anchor="details", compact=True
    )
    assert compact["compact"] is True
    assert "content_fragments" in compact["documents"][0]
    assert "navigation" not in compact["documents"][0]


def test_context_tool_supports_authored_purpose_views(tmp_path: Path) -> None:
    project = adapter_project(tmp_path)
    config_path = project / CONFIG_FILENAME
    config_path.write_text(
        config_path.read_text(encoding="utf-8")
        + """
[context.views.map]
tier = 1
delivery = "outline"
direction = "forward"
depth = 0
relations = []
layers = ["authored"]
""",
        encoding="utf-8",
    )

    packet = mcp_server.context(str(project), "DOC-002", view="map")
    assert packet["purpose_view"]["name"] == "map"
    assert packet["outline"] is True
    assert [item["id"] for item in packet["documents"]] == ["DOC-002"]
    assert packet["view_omissions"] == [
        {
            "source_id": "DOC-002",
            "direction": "forward",
            "relation": "depends_on",
            "peer_id": "DOC-001",
            "reason": "relation-filter",
        }
    ]

    with pytest.raises(ValueError, match="view cannot combine with depth"):
        mcp_server.context(str(project), "DOC-002", depth=1, view="map")


def test_text_packet_tools_surface_projection_fallback_diagnostics(
    tmp_path: Path,
) -> None:
    project = adapter_project(tmp_path)
    config = load_config(project)
    write_projection(config, build_projection(build_catalog(config), config))

    target = project / "plan" / "target.md"
    target.write_text(
        target.read_text(encoding="utf-8").replace(
            "Detailed content.", "Changed content."
        ),
        encoding="utf-8",
    )

    read_packet = mcp_server.read_document_packet(str(project), "DOC-002")
    assert read_packet["schema_version"] == 1
    assert read_packet["text"].startswith("---\nid: DOC-002\n")
    assert "Changed content." in read_packet["text"]
    assert read_packet["diagnostics"] == [
        "WARNING: projection stale; using direct Markdown"
    ]

    impact_packet = mcp_server.impact_packet(str(project), "DOC-001")
    assert impact_packet["schema_version"] == 1
    assert "# Impact analysis: DOC-001" in impact_packet["text"]
    assert impact_packet["diagnostics"] == [
        "WARNING: projection stale; using direct Markdown"
    ]


def test_context_assume_known_omits_declared_document(tmp_path: Path) -> None:
    project = str(adapter_project(tmp_path))

    packet = mcp_server.context(project, "DOC-002", assume_known=["DOC-002@1"])
    target_document = packet["documents"][0]
    assert target_document["id"] == "DOC-002"
    assert "navigation" not in target_document
    assert target_document["content_omitted"] == {
        "reason": "assumed-known",
        "declared_revision": 1,
    }
    assert packet["assume_known_mismatches"] == []
    assert packet["stats"]["assumed_known_omitted"] == 1

    mismatch = mcp_server.context(project, "DOC-002", assume_known=["DOC-002@9"])
    mismatch_document = mismatch["documents"][0]
    assert "navigation" in mismatch_document
    assert "content_omitted" not in mismatch_document
    assert mismatch["assume_known_mismatches"] == [
        {"id": "DOC-002", "declared_revision": 9, "current_revision": 1}
    ]
    assert mismatch["stats"]["assumed_known_omitted"] == 0


def test_context_since_delta_passes_generation_through(tmp_path: Path) -> None:
    project = adapter_project(tmp_path)
    config = load_config(project)
    generation = write_projection(
        config, build_projection(build_catalog(config), config)
    )

    target = project / "plan" / "target.md"
    target.write_text(
        target.read_text(encoding="utf-8").replace(
            "Detailed content.", "Changed content."
        ),
        encoding="utf-8",
    )

    packet = mcp_server.context(str(project), "DOC-002", since=generation)
    documents = {item["id"]: item for item in packet["documents"]}
    # "target" (the H1) is the complete truth signal: its slice spans the
    # whole document, so the edit inside "details" changes its hash too, even
    # though only "details" is served as a content block.
    assert documents["DOC-002"]["changed_sections"] == ["target", "details"]
    assert {
        section["anchor"] for section in documents["DOC-002"]["explicit_sections"]
    } == {"details"}
    assert documents["DOC-001"]["content_omitted"] == {
        "reason": "unchanged-since",
        "generation": generation[:12],
    }


def test_build_server_registers_every_read_only_tool() -> None:
    pytest.importorskip("mcp")
    server = mcp_server.build_server()
    assert server.name == "docsystem"


def test_agent_instructions_returns_the_cli_json_envelope(tmp_path: Path) -> None:
    project = adapter_project(tmp_path)

    payload = mcp_server.agent_instructions(str(project))
    assert payload["schema_version"] == 1
    assert payload["text"].startswith("## Documentation with Documentation Engine\n")
    assert "workspace -> ." in payload["text"]
    assert f"docsystem readiness {project} --json" in payload["text"]
    assert "docsystem roadmap next PROJECT --json" in payload["text"]

    buffer = io.StringIO()
    with redirect_stdout(buffer):
        assert cli_agent_instructions(project) == 0
    assert payload["text"] == buffer.getvalue()


def test_agent_instructions_reports_missing_configuration(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="configuration not found"):
        mcp_server.agent_instructions(str(tmp_path / "missing"))
