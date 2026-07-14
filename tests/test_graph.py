from pathlib import Path

from docsystem.catalog import build_catalog
from docsystem.config import CONFIG_FILENAME, DEFAULT_CONFIG, ProjectConfig, load_config
from docsystem.graph import (
    AUTHORED,
    GENERATED,
    Address,
    GraphEdge,
    build_reference_graph,
    detect_cycles,
    graph_validation_issues,
    parse_address,
    traverse_reasons,
)


def configured_project(tmp_path: Path) -> ProjectConfig:
    (tmp_path / CONFIG_FILENAME).write_text(DEFAULT_CONFIG, encoding="utf-8")
    (tmp_path / "plan").mkdir()
    return load_config(tmp_path)


def write(config: ProjectConfig, relative: str, text: str) -> None:
    path = config.documentation_root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


# --- Address parsing --------------------------------------------------------


def test_parse_address_splits_document_and_anchor() -> None:
    address = parse_address("DOC-010#install")
    assert address.document_id == "DOC-010"
    assert address.anchor == "install"
    assert address.text == "DOC-010#install"


def test_parse_address_accepts_bare_document_id() -> None:
    address = parse_address("DOC-010")
    assert address == Address("DOC-010", None)
    assert address.text == "DOC-010"


def test_parse_address_rejects_empty_document_or_anchor() -> None:
    import pytest

    with pytest.raises(ValueError):
        parse_address("#install")
    with pytest.raises(ValueError):
        parse_address("DOC-010#")
    with pytest.raises(ValueError):
        parse_address("")


# --- Identity, nearest-section mapping, and link resolution -----------------


def test_relative_fragment_and_directory_index_resolve_to_canonical_addresses(
    tmp_path: Path,
) -> None:
    config = configured_project(tmp_path)
    write(
        config,
        "architecture/a.md",
        "---\nid: DOC-001\nrevision: 1\n---\n\n"
        "# Doc A\n\n<a id=\"intro\"></a>\n## Introduction\n\n"
        "See [setup](../roadmap/b.md#setup) and the [roadmap dir](../roadmap/).\n",
    )
    write(
        config,
        "roadmap/README.md",
        "---\nid: DOC-101\nrevision: 1\n---\n\n# Roadmap\n\n[Doc B](b.md)\n",
    )
    write(
        config,
        "roadmap/b.md",
        "---\nid: DOC-002\nrevision: 1\n---\n\n# Doc B\n\n"
        '<a id="setup"></a>\n## Setup\n\nBody.\n',
    )
    catalog = build_catalog(config)
    graph = build_reference_graph(catalog, config)

    forward = graph.forward(Address("DOC-001", "intro"))
    targets = {(edge.relation, edge.target) for edge in forward if edge.relation == "references"}
    assert (("references", Address("DOC-002", "setup"))) in targets
    assert (("references", Address("DOC-101", None))) in targets


def test_percent_decoded_fragment_and_reference_style_link_resolve(tmp_path: Path) -> None:
    config = configured_project(tmp_path)
    write(
        config,
        "architecture/a.md",
        "---\nid: DOC-001\nrevision: 1\n---\n\n# Doc A\n\n"
        "See [ref][b-setup].\n\n[b-setup]: b.md#set%2Dup\n",
    )
    write(
        config,
        "architecture/b.md",
        "---\nid: DOC-002\nrevision: 1\n---\n\n# Doc B\n\n"
        "<a id=\"set-up\"></a>\n## Set up\n\nBody.\n",
    )
    catalog = build_catalog(config)
    graph = build_reference_graph(catalog, config)

    forward = graph.forward(Address("DOC-001", "doc-a"))
    targets = {edge.target for edge in forward if edge.relation == "references"}
    assert Address("DOC-002", "set-up") in targets


def test_same_document_fragment_resolves_without_a_path(tmp_path: Path) -> None:
    config = configured_project(tmp_path)
    write(
        config,
        "architecture/a.md",
        "---\nid: DOC-001\nrevision: 1\n---\n\n# Doc A\n\n"
        "<a id=\"top\"></a>\n## Top\n\n[back to top](#top)\n",
    )
    catalog = build_catalog(config)
    graph = build_reference_graph(catalog, config)

    forward = graph.forward(Address("DOC-001", "top"))
    assert any(
        edge.relation == "references" and edge.target == Address("DOC-001", "top")
        for edge in forward
    )


def test_fenced_links_and_images_do_not_create_reference_edges(tmp_path: Path) -> None:
    config = configured_project(tmp_path)
    write(
        config,
        "architecture/a.md",
        "---\nid: DOC-001\nrevision: 1\n---\n\n# Doc A\n\n"
        "<a id=\"intro\"></a>\n## Introduction\n\n"
        "```\n[fenced](b.md)\n```\n\n"
        "![image](b.md)\n\n"
        "[real link](b.md)\n",
    )
    write(
        config,
        "architecture/b.md",
        "---\nid: DOC-002\nrevision: 1\n---\n\n# Doc B\n\nBody.\n",
    )
    catalog = build_catalog(config)
    graph = build_reference_graph(catalog, config)

    forward = [
        edge
        for edge in graph.forward(Address("DOC-001", "intro"))
        if edge.relation == "references"
    ]
    assert len(forward) == 1
    assert forward[0].target == Address("DOC-002", None)


# --- Boundaries --------------------------------------------------------------


def test_external_resource_outside_root_and_unknown_document_boundaries(
    tmp_path: Path,
) -> None:
    config = configured_project(tmp_path)
    write(
        config,
        "architecture/a.md",
        "---\nid: DOC-001\nrevision: 1\n---\n\n# Doc A\n\n"
        "<a id=\"intro\"></a>\n## Introduction\n\n"
        "[external](https://example.com/x)\n"
        "[resource](diagram.png)\n"
        "[outside](../../../etc/passwd.md)\n"
        "[unknown](missing.md)\n",
    )
    catalog = build_catalog(config)
    graph = build_reference_graph(catalog, config)

    categories = {
        boundary.category for boundary in graph.boundaries_from(Address("DOC-001", "intro"))
    }
    assert categories == {"external", "resource", "outside-root", "unknown-document"}


def test_missing_anchor_is_a_visible_boundary_not_an_invented_edge(tmp_path: Path) -> None:
    config = configured_project(tmp_path)
    write(
        config,
        "architecture/a.md",
        "---\nid: DOC-001\nrevision: 1\n---\n\n# Doc A\n\n"
        "<a id=\"intro\"></a>\n## Introduction\n\n[broken](b.md#renamed)\n",
    )
    write(
        config,
        "architecture/b.md",
        "---\nid: DOC-002\nrevision: 1\n---\n\n# Doc B\n\n"
        '<a id="setup"></a>\n## Setup\n\nBody.\n',
    )
    catalog = build_catalog(config)
    graph = build_reference_graph(catalog, config)

    boundaries = graph.boundaries_from(Address("DOC-001", "intro"))
    assert len(boundaries) == 1
    assert boundaries[0].category == "missing-anchor"
    assert "DOC-002#renamed" in boundaries[0].reason

    issues = graph_validation_issues(catalog, config)
    assert any(
        issue.category == "dead-reference" and "renamed" in issue.message for issue in issues
    )


def test_fragment_marker_without_anchor_is_a_malformed_visible_boundary(
    tmp_path: Path,
) -> None:
    config = configured_project(tmp_path)
    write(
        config,
        "architecture/a.md",
        "---\nid: DOC-001\nrevision: 1\n---\n\n# Doc A\n\n[broken](#)\n",
    )

    graph = build_reference_graph(build_catalog(config), config)

    assert len(graph.boundaries) == 1
    assert graph.boundaries[0].category == "malformed"
    assert graph.boundaries[0].raw_target == "#"


def test_explicit_anchor_survives_line_movement(tmp_path: Path) -> None:
    config = configured_project(tmp_path)
    write(
        config,
        "architecture/a.md",
        "---\nid: DOC-001\nrevision: 1\n---\n\n# Doc A\n\n[setup](b.md#setup)\n",
    )
    write(
        config,
        "architecture/b.md",
        "---\nid: DOC-002\nrevision: 1\n---\n\n# Doc B\n\n"
        '<a id="setup"></a>\n## Setup\n\nBody.\n',
    )
    catalog = build_catalog(config)
    graph = build_reference_graph(catalog, config)
    assert not graph.boundaries

    # Insert leading content that shifts every line number; the explicit
    # anchor keeps the same canonical identity regardless of coordinates.
    write(
        config,
        "architecture/b.md",
        "---\nid: DOC-002\nrevision: 1\n---\n\n# Doc B\n\n"
        "Extra paragraph.\n\nAnother paragraph.\n\n<a id=\"setup\"></a>\n## Setup\n\nBody.\n",
    )
    catalog = build_catalog(config)
    graph = build_reference_graph(catalog, config)
    assert not graph.boundaries
    assert any(
        edge.relation == "references" and edge.target == Address("DOC-002", "setup")
        for edge in graph.edges
    )


# --- Authority distinctions ---------------------------------------------------


def test_authored_observed_and_generated_authorities_remain_distinct(tmp_path: Path) -> None:
    config = configured_project(tmp_path)
    write(
        config,
        "architecture/a.md",
        "---\nid: DOC-001\nrevision: 1\ndepends_on: [DOC-002]\n---\n\n"
        "# Doc A\n\n<a id=\"intro\"></a>\n## Introduction\n\n[setup](b.md#setup)\n",
    )
    write(
        config,
        "architecture/b.md",
        "---\nid: DOC-002\nrevision: 1\n---\n\n# Doc B\n\n"
        '<a id="setup"></a>\n## Setup\n\nBody.\n',
    )
    catalog = build_catalog(config)
    graph = build_reference_graph(catalog, config)

    authorities = {edge.relation: edge.authority for edge in graph.edges}
    assert authorities["depends_on"] == "authored"
    assert authorities["references"] == "observed"
    assert authorities["contains"] == "generated"


# --- Traversal ----------------------------------------------------------------


def test_direct_and_transitive_traversal_report_distance_and_proving_path(
    tmp_path: Path,
) -> None:
    config = configured_project(tmp_path)
    write(
        config,
        "architecture/a.md",
        "---\nid: DOC-001\nrevision: 1\ndepends_on: [DOC-002]\n---\n\n# Doc A\n\nBody.\n",
    )
    write(
        config,
        "architecture/b.md",
        "---\nid: DOC-002\nrevision: 1\ndepends_on: [DOC-003]\n---\n\n# Doc B\n\nBody.\n",
    )
    write(
        config,
        "architecture/c.md",
        "---\nid: DOC-003\nrevision: 1\n---\n\n# Doc C\n\nBody.\n",
    )
    catalog = build_catalog(config)
    graph = build_reference_graph(catalog, config)

    direct = graph.traverse(Address("DOC-001"), transitive=False)
    assert {result.address for result in direct} == {
        Address("DOC-001", "doc-a"),
        Address("DOC-002"),
    }
    assert all(result.direct and result.distance == 1 for result in direct)

    transitive = graph.traverse(Address("DOC-001"), transitive=True)
    doc_003 = next(result for result in transitive if result.address == Address("DOC-003"))
    assert doc_003.distance == 2
    assert not doc_003.direct
    assert [address.text for address in doc_003.path.addresses] == ["DOC-001", "DOC-002", "DOC-003"]


def test_traversal_is_deterministically_ordered(tmp_path: Path) -> None:
    config = configured_project(tmp_path)
    write(
        config,
        "architecture/a.md",
        "---\nid: DOC-001\nrevision: 1\ndepends_on: [DOC-002, DOC-003]\nrelated: [DOC-004]\n---\n\n"
        "# Doc A\n\nBody.\n",
    )
    for suffix, doc_id in (("b", "DOC-002"), ("c", "DOC-003"), ("d", "DOC-004")):
        write(
            config,
            f"architecture/{suffix}.md",
            f"---\nid: {doc_id}\nrevision: 1\n---\n\n# Doc\n\nBody.\n",
        )
    catalog = build_catalog(config)
    graph = build_reference_graph(catalog, config)

    first = graph.traverse(Address("DOC-001"))
    second = graph.traverse(Address("DOC-001"))
    assert first == second
    relations = [(result.relation, result.address.text) for result in first]
    assert relations == sorted(relations)


# --- Cycles --------------------------------------------------------------------


def test_related_cycles_are_allowed(tmp_path: Path) -> None:
    config = configured_project(tmp_path)
    write(
        config,
        "architecture/a.md",
        "---\nid: DOC-001\nrevision: 1\nrelated: [DOC-002]\n---\n\n# Doc A\n\nBody.\n",
    )
    write(
        config,
        "architecture/b.md",
        "---\nid: DOC-002\nrevision: 1\nrelated: [DOC-001]\n---\n\n# Doc B\n\nBody.\n",
    )
    catalog = build_catalog(config)
    graph = build_reference_graph(catalog, config)
    assert detect_cycles(graph.edges) == ()


def test_depends_on_cycle_blocks_dependency_ordering(tmp_path: Path) -> None:
    config = configured_project(tmp_path)
    write(
        config,
        "architecture/a.md",
        "---\nid: DOC-001\nrevision: 1\ndepends_on: [DOC-002]\n---\n\n# Doc A\n\nBody.\n",
    )
    write(
        config,
        "architecture/b.md",
        "---\nid: DOC-002\nrevision: 1\ndepends_on: [DOC-001]\n---\n\n# Doc B\n\nBody.\n",
    )
    catalog = build_catalog(config)
    graph = build_reference_graph(catalog, config)

    cycles = detect_cycles(graph.edges)
    assert len(cycles) == 1
    assert cycles[0].relation == "depends_on"
    assert cycles[0].severity == "error"
    assert set(cycles[0].members) == {"DOC-001", "DOC-002"}

    issues = graph_validation_issues(catalog, config)
    assert any("blocks dependency ordering" in issue.message for issue in issues)


def test_derived_from_cycle_is_an_error(tmp_path: Path) -> None:
    config = configured_project(tmp_path)
    write(
        config,
        "architecture/a.md",
        "---\nid: DOC-001\nrevision: 1\nderived_from: [DOC-002]\n---\n\n# Doc A\n\nBody.\n",
    )
    write(
        config,
        "architecture/b.md",
        "---\nid: DOC-002\nrevision: 1\nderived_from: [DOC-001]\n---\n\n# Doc B\n\nBody.\n",
    )
    catalog = build_catalog(config)
    graph = build_reference_graph(catalog, config)

    cycles = detect_cycles(graph.edges)
    assert len(cycles) == 1
    assert cycles[0].relation == "derived_from"
    issues = graph_validation_issues(catalog, config)
    assert any("is an error" in issue.message for issue in issues)


def test_observed_reference_cycles_are_navigation_evidence_only(tmp_path: Path) -> None:
    config = configured_project(tmp_path)
    write(
        config,
        "architecture/a.md",
        "---\nid: DOC-001\nrevision: 1\n---\n\n# Doc A\n\n[b](b.md)\n",
    )
    write(
        config,
        "architecture/b.md",
        "---\nid: DOC-002\nrevision: 1\n---\n\n# Doc B\n\n[a](a.md)\n",
    )
    catalog = build_catalog(config)
    graph = build_reference_graph(catalog, config)
    assert detect_cycles(graph.edges) == ()


# --- traverse_reasons (multi-reason BFS used by change-plan) -----------------


def _semantic_forward(graph, address: Address) -> tuple:
    return tuple(edge for edge in graph.forward(address) if edge.authority != GENERATED)


def _semantic_reverse(graph, address: Address) -> tuple:
    return tuple(edge for edge in graph.reverse_edges(address) if edge.authority != GENERATED)


def test_traverse_reasons_keeps_every_distinct_edge_at_minimal_distance(
    tmp_path: Path,
) -> None:
    """Unlike `traverse`, `traverse_reasons` must not discard an alternate
    authored/observed edge merely because BFS found one address first."""

    config = configured_project(tmp_path)
    write(
        config,
        "architecture/a.md",
        "---\nid: DOC-001\nrevision: 1\ndepends_on: [DOC-002]\n---\n\n"
        "See [Doc B](b.md) before any heading.\n\n# Doc A\n\nBody.\n",
    )
    write(
        config,
        "architecture/b.md",
        "---\nid: DOC-002\nrevision: 1\n---\n\n# Doc B\n\nBody.\n",
    )
    catalog = build_catalog(config)
    graph = build_reference_graph(catalog, config)

    results = traverse_reasons(
        Address("DOC-001"),
        forward=lambda address: _semantic_forward(graph, address),
        reverse_edges=lambda address: _semantic_reverse(graph, address),
        transitive=False,
    )
    doc_002_results = [result for result in results if result.address == Address("DOC-002")]
    assert {(result.relation, result.authority) for result in doc_002_results} == {
        ("depends_on", "authored"),
        ("references", "observed"),
    }
    assert all(result.distance == 1 and result.direct for result in doc_002_results)


def test_traverse_reasons_excludes_generated_containment_when_filtered(
    tmp_path: Path,
) -> None:
    config = configured_project(tmp_path)
    write(
        config,
        "architecture/a.md",
        "---\nid: DOC-001\nrevision: 1\n---\n\n# Doc A\n\n"
        "<a id=\"intro\"></a>\n## Introduction\n\nBody.\n",
    )
    catalog = build_catalog(config)
    graph = build_reference_graph(catalog, config)

    results = traverse_reasons(
        Address("DOC-001"),
        forward=lambda address: _semantic_forward(graph, address),
        reverse_edges=lambda address: _semantic_reverse(graph, address),
        transitive=False,
    )
    assert results == ()


def test_traverse_reasons_is_deterministic_and_bounded_on_transitive_expansion(
    tmp_path: Path,
) -> None:
    config = configured_project(tmp_path)
    write(
        config,
        "architecture/a.md",
        "---\nid: DOC-001\nrevision: 1\ndepends_on: [DOC-002]\n---\n\n# Doc A\n\nBody.\n",
    )
    write(
        config,
        "architecture/b.md",
        "---\nid: DOC-002\nrevision: 1\ndepends_on: [DOC-003]\n---\n\n# Doc B\n\nBody.\n",
    )
    write(
        config,
        "architecture/c.md",
        "---\nid: DOC-003\nrevision: 1\ndepends_on: [DOC-001]\n---\n\n# Doc C\n\nBody.\n",
    )
    catalog = build_catalog(config)
    graph = build_reference_graph(catalog, config)

    first = traverse_reasons(
        Address("DOC-001"),
        forward=lambda address: _semantic_forward(graph, address),
        reverse_edges=lambda address: _semantic_reverse(graph, address),
        transitive=True,
    )
    second = traverse_reasons(
        Address("DOC-001"),
        forward=lambda address: _semantic_forward(graph, address),
        reverse_edges=lambda address: _semantic_reverse(graph, address),
        transitive=True,
    )
    assert first == second
    doc_003 = next(result for result in first if result.address == Address("DOC-003"))
    assert doc_003.distance == 2
    assert not doc_003.direct
    assert [address.text for address in doc_003.path.addresses] == [
        "DOC-001",
        "DOC-002",
        "DOC-003",
    ]
    # DOC-001 is the query start and a cycle target; it must never reappear.
    assert Address("DOC-001") not in {result.address for result in first}


def test_traverse_reasons_preserves_distinct_minimal_proving_paths() -> None:
    start = Address("DOC-001")
    left = Address("DOC-002")
    right = Address("DOC-003")
    target = Address("DOC-004")
    edges = {
        start: (
            GraphEdge(start, left, "depends_on", AUTHORED, "metadata"),
            GraphEdge(start, right, "depends_on", AUTHORED, "metadata"),
        ),
        left: (GraphEdge(left, target, "depends_on", AUTHORED, "metadata"),),
        right: (GraphEdge(right, target, "depends_on", AUTHORED, "metadata"),),
    }

    results = traverse_reasons(
        start,
        forward=lambda address: edges.get(address, ()),
        reverse_edges=lambda _address: (),
        transitive=True,
    )

    target_results = [result for result in results if result.address == target]
    assert len(target_results) == 2
    assert {
        tuple(step.text for step in result.path.addresses) for result in target_results
    } == {
        ("DOC-001", "DOC-002", "DOC-004"),
        ("DOC-001", "DOC-003", "DOC-004"),
    }
