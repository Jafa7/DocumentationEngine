import json
from pathlib import Path

import pytest

from docsystem.catalog import build_catalog, validate_metadata
from docsystem.cli import (
    context,
    federation_catalog,
    federation_changes,
    federation_context,
    federation_dependencies,
    federation_impact,
    federation_index,
    federation_references,
    main,
)
from docsystem.config import CONFIG_FILENAME, DEFAULT_CONFIG, load_config
from docsystem.federation import (
    FederationError,
    build_federated_catalog,
    parse_qualified_address,
)
from docsystem.projection import build_projection, write_projection
from docsystem.workspace import WORKSPACE_FILENAME, load_workspace

PROJECT_CONFIG = DEFAULT_CONFIG.replace(
    "[areas]\n", '[areas]\ndocumentation = "."\n'
)


def write_source(
    root: Path,
    document_id: str,
    *,
    depends_on: tuple[str, ...] = (),
    validated_against: tuple[str, ...] = (),
    revision: int = 1,
) -> None:
    root.mkdir(parents=True)
    (root / CONFIG_FILENAME).write_text(PROJECT_CONFIG, encoding="utf-8")
    plan = root / "plan"
    plan.mkdir()
    relation = (
        "depends_on: [" + ", ".join(depends_on) + "]\n" if depends_on else ""
    )
    pins = (
        "validated_against: [" + ", ".join(validated_against) + "]\n"
        if validated_against
        else ""
    )
    (plan / "README.md").write_text(
        "---\n"
        f"id: {document_id}\n"
        f"revision: {revision}\n"
        f"{relation}"
        f"{pins}"
        "---\n\n"
        f"# {document_id}\n\n"
        "Introduction.\n\n"
        "## Details\n\n"
        f"Complete source body for {document_id}.\n\n"
        "## History\n\n"
        "Historical body.\n",
        encoding="utf-8",
    )


def make_workspace(
    tmp_path: Path,
    *,
    alpha_relation: str = "beta::DOC-001",
    same_ids: bool = True,
) -> Path:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / WORKSPACE_FILENAME).write_text(
        "version = 1\n\n"
        "[[sources]]\n"
        'name = "alpha"\n'
        'root = "sources/alpha"\n'
        'visibility = "private"\n\n'
        "[[sources]]\n"
        'name = "beta"\n'
        'root = "sources/beta"\n'
        'visibility = "public"\n',
        encoding="utf-8",
    )
    write_source(
        workspace / "sources" / "alpha",
        "DOC-001",
        depends_on=(alpha_relation,),
    )
    write_source(
        workspace / "sources" / "beta",
        "DOC-001" if same_ids else "DOC-002",
        revision=2,
    )
    return workspace


def test_qualified_address_contract() -> None:
    assert parse_qualified_address("alpha::DOC-001#details").text == (
        "alpha::DOC-001#details"
    )
    with pytest.raises(FederationError, match="malformed qualified address"):
        parse_qualified_address("DOC-001")
    with pytest.raises(FederationError, match="malformed qualified address"):
        parse_qualified_address("Alpha::DOC-001")
    with pytest.raises(FederationError, match="malformed qualified address"):
        parse_qualified_address("alpha::DOC-001#details", allow_anchor=False)


def test_federation_qualifies_duplicate_local_ids_and_resolves_edge(
    tmp_path: Path,
) -> None:
    workspace = make_workspace(tmp_path)
    catalog = build_federated_catalog(load_workspace(workspace))

    assert [item.address.document for item in catalog.documents] == [
        "alpha::DOC-001",
        "beta::DOC-001",
    ]
    assert [
        (edge.relation, edge.source.document, edge.target.document)
        for edge in catalog.edges
    ] == [("depends_on", "alpha::DOC-001", "beta::DOC-001")]


def test_single_source_graph_keeps_federated_reference_as_visible_boundary(
    tmp_path: Path,
) -> None:
    workspace = make_workspace(tmp_path)
    alpha = workspace / "sources" / "alpha"
    config = load_config(alpha)
    catalog = build_catalog(config)

    issues = validate_metadata(catalog, config)
    assert [
        (item.severity, item.category, item.affects_graph, item.target_id)
        for item in issues
    ] == [
        ("warning", "federation-boundary", True, "beta::DOC-001")
    ]
    assert catalog.relation_migrations == ()
    assert catalog.relation_boundaries == ()


def test_single_source_projection_preserves_federated_boundary(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    workspace = make_workspace(tmp_path)
    alpha = workspace / "sources" / "alpha"
    config = load_config(alpha)

    assert context(alpha, "DOC-001", json_output=True) == 0
    direct = capsys.readouterr()
    assert direct.err == "WARNING: projection absent; using direct Markdown\n"
    payload = json.loads(direct.out)
    assert payload["boundaries"] == [
        {
            "reason": "requires workspace federation",
            "relation": "depends_on",
            "source_id": "DOC-001",
            "value": "beta::DOC-001",
        }
    ]

    write_projection(config, build_projection(build_catalog(config), config))
    assert context(alpha, "DOC-001", json_output=True) == 0
    projected = capsys.readouterr()
    assert projected.err == ""
    assert projected.out == direct.out


def test_federated_catalog_json_is_deterministic(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    workspace = make_workspace(tmp_path)

    assert federation_catalog(tmp_path, workspace_option=workspace, json_output=True) == 0
    first = capsys.readouterr()
    assert federation_catalog(tmp_path, workspace_option=workspace, json_output=True) == 0
    second = capsys.readouterr()

    assert first == second
    payload = json.loads(first.out)
    assert payload["complete"] is True
    assert payload["edge_count"] == 1
    assert payload["boundary_count"] == 0
    assert payload["migration_count"] == 0
    assert [item["address"] for item in payload["documents"]] == [
        "alpha::DOC-001",
        "beta::DOC-001",
    ]
    assert str(tmp_path) not in first.out


def test_federated_projection_serves_byte_identical_context_without_reparsing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace = make_workspace(tmp_path)

    assert federation_context(
        tmp_path, "alpha::DOC-001#details", workspace_option=workspace
    ) == 0
    direct = capsys.readouterr()
    assert federation_index(tmp_path, workspace_option=workspace, write=True) == 0
    capsys.readouterr()

    def unexpected_direct_build(*_args, **_kwargs):
        raise AssertionError("current projection must skip direct federation parsing")

    monkeypatch.setattr(
        "docsystem.cli.build_federated_catalog", unexpected_direct_build
    )
    assert federation_context(
        tmp_path, "alpha::DOC-001#details", workspace_option=workspace
    ) == 0
    projected = capsys.readouterr()

    assert projected == direct


def test_stale_federated_projection_falls_back_once_and_reports_changes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    workspace = make_workspace(tmp_path)
    assert federation_index(tmp_path, workspace_option=workspace, write=True) == 0
    capsys.readouterr()
    source = workspace / "sources" / "alpha" / "plan" / "README.md"
    source.write_text(
        source.read_text(encoding="utf-8") + "\nNew source detail.\n",
        encoding="utf-8",
    )

    assert federation_context(
        tmp_path, "alpha::DOC-001", workspace_option=workspace, json_output=True
    ) == 0
    fallback = capsys.readouterr()
    assert fallback.err == (
        "WARNING: federated projection stale: source alpha changed; "
        "using direct Markdown\n"
    )
    assert json.loads(fallback.out)["complete"] is True

    assert federation_changes(
        tmp_path, workspace_option=workspace, json_output=True
    ) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "changes": [{"kind": "modified", "source": "alpha"}],
        "schema_version": 1,
        "status": "compared",
    }


def test_federated_dependencies_forward_and_reverse(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    workspace = make_workspace(tmp_path)

    assert federation_dependencies(
        tmp_path, "alpha::DOC-001", workspace_option=workspace
    ) == 0
    assert capsys.readouterr().out == "depends_on\tbeta::DOC-001\n"

    assert federation_dependencies(
        tmp_path, "beta::DOC-001", workspace_option=workspace, reverse=True
    ) == 0
    assert capsys.readouterr().out == "depends_on\talpha::DOC-001\n"


def test_federated_references_qualify_section_graph_and_cross_source_edge(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    workspace = make_workspace(tmp_path)

    assert federation_references(
        tmp_path,
        "alpha::DOC-001",
        workspace_option=workspace,
        json_output=True,
    ) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["complete"] is True
    assert {
        (item["relation"], item["authority"], item["address"])
        for item in payload["results"]
    } == {
        ("contains", "generated", "alpha::DOC-001#details"),
        ("contains", "generated", "alpha::DOC-001#doc-001"),
        ("contains", "generated", "alpha::DOC-001#history"),
        ("depends_on", "authored", "beta::DOC-001"),
    }

    assert federation_references(
        tmp_path,
        "beta::DOC-001",
        workspace_option=workspace,
        reverse=True,
        json_output=True,
    ) == 0
    reverse = json.loads(capsys.readouterr().out)
    assert [item["address"] for item in reverse["results"]] == [
        "alpha::DOC-001"
    ]


def test_federated_context_preserves_navigation_section_and_omissions(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    workspace = make_workspace(tmp_path)

    assert federation_context(
        tmp_path,
        "alpha::DOC-001#details",
        workspace_option=workspace,
        depth=1,
        json_output=True,
    ) == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert payload["complete"] is True
    assert payload["projection"] == "direct-markdown"
    assert [item["address"] for item in payload["documents"]] == [
        "alpha::DOC-001",
        "beta::DOC-001",
    ]
    target = payload["documents"][0]
    assert "Introduction." in target["navigation"]
    assert "Complete source body" in target["explicit_sections"][0]["content"]
    assert target["omitted_h2"] == ["history"]
    assert payload["documents"][1]["omitted_h2"] == ["details", "history"]


def test_federated_context_explicit_cross_source_include(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    workspace = make_workspace(tmp_path)

    assert federation_context(
        tmp_path,
        "alpha::DOC-001",
        workspace_option=workspace,
        depth=0,
        includes=("beta::DOC-001#history",),
        json_output=True,
    ) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["documents"][1]["relations"] == ["explicit"]
    assert "Historical body." in payload["documents"][1]["explicit_sections"][0][
        "content"
    ]


def test_federated_context_reports_depth_omission(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    workspace = make_workspace(tmp_path)

    assert federation_context(
        tmp_path,
        "alpha::DOC-001",
        workspace_option=workspace,
        depth=0,
        json_output=True,
    ) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["omitted_relations"] == [
        {
            "reason": "depth-or-explicit-boundary",
            "relation": "depends_on",
            "source": "alpha::DOC-001",
            "target": "beta::DOC-001",
        }
    ]


def test_federated_context_accepts_multiple_sections_from_one_document(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    workspace = make_workspace(tmp_path)

    assert federation_context(
        tmp_path,
        "alpha::DOC-001",
        workspace_option=workspace,
        depth=0,
        includes=("beta::DOC-001#details", "beta::DOC-001#history"),
        json_output=True,
    ) == 0
    payload = json.loads(capsys.readouterr().out)
    sections = payload["documents"][1]["explicit_sections"]
    assert [item["anchor"] for item in sections] == ["details", "history"]
    assert payload["documents"][1]["omitted_h2"] == []


def test_federated_impact_reports_cross_source_downstream(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    workspace = make_workspace(tmp_path)

    assert federation_impact(
        tmp_path, "beta::DOC-001", workspace_option=workspace, json_output=True
    ) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["complete"] is True
    assert payload["edges"] == [
        {
            "classification": "semantic",
            "downstream": "alpha::DOC-001",
            "expected_revision": None,
            "relation": "depends_on",
        }
    ]


def test_cross_source_revision_pin_is_not_a_legacy_warning_and_reports_stale(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    workspace = make_workspace(tmp_path)
    alpha = workspace / "sources" / "alpha"
    write_source_content = alpha / "plan" / "README.md"
    text = write_source_content.read_text(encoding="utf-8").replace(
        "depends_on: [beta::DOC-001]",
        "validated_against: [beta::DOC-001@1]",
    )
    write_source_content.write_text(text, encoding="utf-8")

    assert federation_context(
        tmp_path,
        "alpha::DOC-001",
        workspace_option=workspace,
        json_output=True,
    ) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["freshness"] == [
        {
            "classification": "stale",
            "current_revision": 2,
            "pinned_revision": 1,
            "source": "alpha::DOC-001",
            "target": "beta::DOC-001",
        }
    ]


@pytest.mark.parametrize(
    ("relation", "message"),
    [
        ("unknown::DOC-001", "unknown-source"),
        ("beta::DOC-999", "unknown-document"),
        ("beta::DOC-001#details", "source::stable-ID syntax"),
        ("alpha::DOC-001", "self-reference"),
    ],
)
def test_invalid_cross_source_edge_fails_without_partial_stdout(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    relation: str,
    message: str,
) -> None:
    workspace = make_workspace(tmp_path, alpha_relation=relation)

    assert federation_context(
        tmp_path, "alpha::DOC-001", workspace_option=workspace
    ) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert message in captured.err


def test_external_resource_boundary_remains_visible_without_becoming_an_edge(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    workspace = make_workspace(
        tmp_path, alpha_relation="https://example.com/external-spec"
    )

    assert federation_context(
        tmp_path,
        "alpha::DOC-001",
        workspace_option=workspace,
        json_output=True,
    ) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["documents"][0]["address"] == "alpha::DOC-001"
    assert payload["boundaries"] == [
        {
            "reason": "external URL",
            "relation": "depends_on",
            "source": "alpha::DOC-001",
            "value": "https://example.com/external-spec",
        }
    ]


def test_resolved_legacy_relation_remains_visible_as_migration_evidence(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    workspace = make_workspace(tmp_path)
    alpha = workspace / "sources" / "alpha"
    config_path = alpha / CONFIG_FILENAME
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace(
            'legacy_paths = "strict"',
            'legacy_paths = "resolve-with-warning"',
        ),
        encoding="utf-8",
    )
    index = alpha / "plan" / "README.md"
    index.write_text(
        index.read_text(encoding="utf-8")
        .replace("depends_on: [beta::DOC-001]", "depends_on: [target.md]")
        .replace("Introduction.", "Introduction.\n\n[Target](target.md)"),
        encoding="utf-8",
    )
    (alpha / "plan" / "target.md").write_text(
        "---\nid: DOC-002\nrevision: 1\n---\n# Target\n",
        encoding="utf-8",
    )

    assert federation_context(
        tmp_path,
        "alpha::DOC-001",
        workspace_option=workspace,
        json_output=True,
    ) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["migrations"] == [
        {
            "relation": "depends_on",
            "source": "alpha::DOC-001",
            "target": "alpha::DOC-002",
            "value": "target.md",
        }
    ]


def test_unavailable_source_blocks_complete_query_without_leaking_path(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    workspace = make_workspace(tmp_path)
    (workspace / "sources" / "beta" / CONFIG_FILENAME).unlink()

    assert federation_catalog(tmp_path, workspace_option=workspace) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == (
        "ERROR: workspace source is unavailable: beta (missing-configuration)\n"
    )
    assert str(tmp_path) not in captured.err


def test_empty_workspace_is_not_reported_as_a_complete_federation(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / WORKSPACE_FILENAME).write_text("version = 1\n", encoding="utf-8")

    assert federation_catalog(tmp_path, workspace_option=workspace) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == (
        "ERROR: workspace federation requires at least one source\n"
    )


def test_cross_source_semantic_cycle_fails_closed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    workspace = make_workspace(tmp_path)
    beta = workspace / "sources" / "beta" / "plan" / "README.md"
    beta.write_text(
        beta.read_text(encoding="utf-8").replace(
            "revision: 2\n", "revision: 2\ndepends_on: [alpha::DOC-001]\n"
        ),
        encoding="utf-8",
    )

    assert federation_context(
        tmp_path, "alpha::DOC-001", workspace_option=workspace
    ) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == (
        "ERROR: federated depends_on cycle: alpha::DOC-001, beta::DOC-001\n"
    )


def test_local_and_qualified_pin_conflict_fails_closed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    workspace = make_workspace(tmp_path)
    alpha_plan = workspace / "sources" / "alpha" / "plan"
    index = alpha_plan / "README.md"
    index.write_text(
        index.read_text(encoding="utf-8")
        .replace(
            "depends_on: [beta::DOC-001]",
            "validated_against: [DOC-002@1, alpha::DOC-002@2]",
        )
        .replace("Introduction.", "Introduction.\n\n[Target](target.md)"),
        encoding="utf-8",
    )
    (alpha_plan / "target.md").write_text(
        "---\nid: DOC-002\nrevision: 2\n---\n# Target\n",
        encoding="utf-8",
    )

    assert federation_context(
        tmp_path, "alpha::DOC-001", workspace_option=workspace
    ) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == (
        "ERROR: federated validated_against pin conflict: "
        "alpha::DOC-001 -> alpha::DOC-002 uses revisions 1, 2\n"
    )


def test_federation_cli_parser_routes_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace = make_workspace(tmp_path)
    monkeypatch.setattr(
        "sys.argv",
        [
            "docsystem",
            "federation",
            "context",
            "alpha::DOC-001",
            str(tmp_path),
            "--workspace",
            str(workspace),
            "--json",
        ],
    )

    assert main() == 0
    assert json.loads(capsys.readouterr().out)["target"] == "alpha::DOC-001"


def test_federation_cli_parser_routes_projection_commands(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace = make_workspace(tmp_path)
    monkeypatch.setattr(
        "sys.argv",
        [
            "docsystem",
            "federation",
            "index",
            str(tmp_path),
            "--workspace",
            str(workspace),
            "--write",
            "--json",
        ],
    )
    assert main() == 0
    assert json.loads(capsys.readouterr().out)["status"] == "written"

    monkeypatch.setattr(
        "sys.argv",
        [
            "docsystem",
            "federation",
            "changes",
            str(tmp_path),
            "--workspace",
            str(workspace),
            "--json",
        ],
    )
    assert main() == 0
    assert json.loads(capsys.readouterr().out) == {
        "changes": [],
        "schema_version": 1,
        "status": "compared",
    }
