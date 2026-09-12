import ast
from pathlib import Path

from docsystem.catalog import build_catalog
from docsystem.config import CONFIG_FILENAME, DEFAULT_CONFIG, ContextView, load_config
from docsystem.projection import build_projection, write_projection
from docsystem.retrieval import (
    build_packet_plans,
    context_selection,
    load_retrieval_state,
    ordered_selection,
    packet_sections,
    parse_selection,
    purpose_context_selection,
)


def _project(root: Path) -> None:
    (root / CONFIG_FILENAME).write_text(DEFAULT_CONFIG, encoding="utf-8")
    area = root / "plan" / "architecture"
    area.mkdir(parents=True)
    (area / "README.md").write_text(
        """\
---
id: DOC-001
revision: 2
---
# Architecture

Introduction.

## Overview

Overview text.

[Detail](detail.md)
""",
        encoding="utf-8",
    )
    (area / "detail.md").write_text(
        """\
---
id: DOC-002
revision: 1
depends_on: [DOC-001]
related: [DOC-001]
---
# Detail

Summary.

## Behavior

Behavior text.
""",
        encoding="utf-8",
    )


def test_loading_returns_structured_fallback_without_terminal_output(
    tmp_path: Path, capsys
) -> None:
    _project(tmp_path)
    config = load_config(tmp_path)

    state = load_retrieval_state(config)

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""
    assert state.serving_path == "direct"
    assert state.catalog is not None
    assert [item.code for item in state.diagnostics] == ["projection-fallback"]
    assert sorted(state.views) == ["DOC-001", "DOC-002"]


def test_projection_and_direct_views_are_semantically_identical(tmp_path: Path) -> None:
    _project(tmp_path)
    config = load_config(tmp_path)
    direct = load_retrieval_state(config)
    projection = build_projection(build_catalog(config), config)
    write_projection(config, projection)

    projected = load_retrieval_state(config)

    assert projected.serving_path == "projection"
    assert projected.catalog is None
    assert projected.diagnostics == ()
    assert projected.views == direct.views
    assert projected.incoming == direct.incoming


def test_selection_and_purpose_omissions_are_deterministic(tmp_path: Path) -> None:
    _project(tmp_path)
    state = load_retrieval_state(load_config(tmp_path))

    included, reasons = context_selection(
        state.views, "DOC-002", depth=1, include_related=False
    )
    assert ordered_selection(included, "DOC-002") == ["DOC-002", "DOC-001"]
    assert {item.relation for item in reasons["DOC-001"]} == {"depends_on"}
    assert parse_selection("DOC-001#overview") == ("DOC-001", "overview")

    purpose = ContextView(
        name="dependencies",
        tier=1,
        delivery="navigation",
        direction="forward",
        depth=1,
        relations=("depends_on",),
        layers=("authored",),
    )
    selected, _, omissions = purpose_context_selection(
        state.views, state.incoming, "DOC-002", purpose
    )
    assert ordered_selection(selected, "DOC-002") == ["DOC-002", "DOC-001"]
    assert [
        (item.direction, item.relation, item.peer_id, item.reason)
        for item in omissions
    ] == [("forward", "related", "DOC-001", "relation-filter")]


def test_packet_planning_is_available_without_cli_capture(tmp_path: Path) -> None:
    _project(tmp_path)
    config = load_config(tmp_path)
    state = load_retrieval_state(config)
    ordered = ["DOC-002", "DOC-001"]

    plans, mismatches, notes, omitted_count = build_packet_plans(
        state.views,
        ordered,
        assumed={"DOC-001": 2, "DOC-002": 9},
        since_manifest=None,
        generation_short=None,
    )

    assert plans["DOC-001"].coverage_state == "assumed-known"
    assert "DOC-002" not in plans
    assert mismatches == [
        {"id": "DOC-002", "declared_revision": 9, "current_revision": 1}
    ]
    assert notes == [
        "DOC-002: assumed known at revision 9, current 1 — content included"
    ]
    assert omitted_count == 1
    explicit, changed, omitted = packet_sections(
        config, state.views["DOC-002"], [], plans.get("DOC-002")
    )
    assert explicit == []
    assert changed == set()
    assert omitted == ["behavior"]


def test_retrieval_service_does_not_import_cli() -> None:
    path = Path(__file__).parents[1] / "src" / "docsystem" / "retrieval.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imported.update(
        node.module or ""
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    )
    assert "docsystem.cli" not in imported
