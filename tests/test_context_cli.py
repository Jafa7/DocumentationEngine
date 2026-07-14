import json
from pathlib import Path

from docsystem.cli import (
    build_parser,
    context,
    dependencies,
    doctor,
    impact,
    index_projection,
    read_document,
    validate,
)
from docsystem.config import CONFIG_FILENAME, DEFAULT_CONFIG


def _current_generation(tmp_path: Path) -> str:
    pointer = tmp_path / ".docsystem" / "cache" / "current.json"
    return json.loads(pointer.read_text(encoding="utf-8"))["generation"]


def configured_documents(tmp_path: Path) -> None:
    (tmp_path / CONFIG_FILENAME).write_text(DEFAULT_CONFIG, encoding="utf-8")
    area = tmp_path / "plan" / "architecture"
    area.mkdir(parents=True)
    (area / "README.md").write_text(
        """\
---
id: DOC-001
revision: 3
---

# Architecture

Introduction.

## Index details

Details.

[Context](context.md)
""",
        encoding="utf-8",
    )
    (area / "context.md").write_text(
        """\
---
id: DOC-002
revision: 1
depends_on: [DOC-001]
validated_against: [DOC-001@3]
---

# Context

Summary.

## Selected section

Selected text.

### Nested

Nested text.

## Other

Other text.
""",
        encoding="utf-8",
    )


def configured_context_views(tmp_path: Path) -> None:
    configured_documents(tmp_path)
    area = tmp_path / "plan" / "architecture"
    readme = area / "README.md"
    readme.write_text(
        readme.read_text(encoding="utf-8") + "\n[Downstream](downstream.md)\n",
        encoding="utf-8",
    )
    (area / "downstream.md").write_text(
        """\
---
id: DOC-003
revision: 1
depends_on: [DOC-002]
---
# Downstream

Downstream summary.
""",
        encoding="utf-8",
    )
    config_path = tmp_path / CONFIG_FILENAME
    config_path.write_text(
        config_path.read_text(encoding="utf-8")
        + """
[context.views.map]
tier = 1
delivery = "outline"
direction = "both"
depth = 0
relations = []
layers = ["authored"]

[context.views.task]
tier = 2
delivery = "navigation"
direction = "forward"
depth = 1
relations = ["depends_on"]
layers = ["authored"]

[context.views.downstream]
tier = 3
delivery = "navigation"
direction = "reverse"
depth = 1
relations = ["depends_on"]
layers = ["authored"]

[context.views.boundary]
tier = 4
delivery = "navigation"
direction = "forward"
depth = 0
relations = ["depends_on"]
layers = ["authored"]

[context.views.both]
tier = 5
delivery = "navigation"
direction = "both"
depth = 1
relations = ["depends_on"]
layers = ["authored"]
""",
        encoding="utf-8",
    )


def test_purpose_context_views_expose_policy_and_omissions(
    tmp_path: Path, capsys
) -> None:
    configured_context_views(tmp_path)

    assert context(tmp_path, "DOC-002", view_name="map", json_output=True) == 0
    capsys.readouterr()  # Direct fallback warning plus payload are tested below.
    assert context(tmp_path, "DOC-002", view_name="map", json_output=True) == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["outline"] is True
    assert payload["purpose_view"] == {
        "name": "map",
        "tier": 1,
        "delivery": "outline",
        "direction": "both",
        "depth": 0,
        "relations": [],
        "layers": ["authored"],
    }
    assert [item["id"] for item in payload["documents"]] == ["DOC-002"]
    assert payload["view_omissions"] == [
        {
            "source_id": "DOC-002",
            "direction": "forward",
            "relation": "depends_on",
            "peer_id": "DOC-001",
            "reason": "relation-filter",
        },
        {
            "source_id": "DOC-002",
            "direction": "forward",
            "relation": "validated_against",
            "peer_id": "DOC-001",
            "reason": "relation-filter",
        },
        {
            "source_id": "DOC-002",
            "direction": "reverse",
            "relation": "depends_on",
            "peer_id": "DOC-003",
            "reason": "relation-filter",
        },
    ]
    assert "Selected text." not in captured.out

    assert context(tmp_path, "DOC-002", view_name="task", json_output=True) == 0
    payload = json.loads(capsys.readouterr().out)
    assert [item["id"] for item in payload["documents"]] == ["DOC-002", "DOC-001"]
    assert payload["view_omissions"] == [
        {
            "source_id": "DOC-002",
            "direction": "forward",
            "relation": "validated_against",
            "peer_id": "DOC-001",
            "reason": "relation-filter",
        }
    ]

    assert context(
        tmp_path,
        "DOC-002",
        view_name="task",
        includes=["DOC-002#other"],
        json_output=True,
    ) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["documents"][0]["explicit_sections"] == [
        {"anchor": "other", "content": "## Other\n\nOther text."}
    ]

    assert context(tmp_path, "DOC-001", view_name="downstream", json_output=True) == 0
    payload = json.loads(capsys.readouterr().out)
    assert [item["id"] for item in payload["documents"]] == ["DOC-001", "DOC-002"]
    assert payload["documents"][1]["relations"] == ["reverse:depends_on"]

    assert context(tmp_path, "DOC-002", view_name="both", json_output=True) == 0
    payload = json.loads(capsys.readouterr().out)
    assert [item["id"] for item in payload["documents"]] == [
        "DOC-002",
        "DOC-001",
        "DOC-003",
    ]
    assert payload["documents"][1]["relations"] == ["depends_on"]
    assert payload["documents"][2]["relations"] == ["reverse:depends_on"]

    assert context(tmp_path, "DOC-002", view_name="boundary", json_output=True) == 0
    payload = json.loads(capsys.readouterr().out)
    assert [item["id"] for item in payload["documents"]] == ["DOC-002"]
    assert payload["view_omissions"] == [
        {
            "source_id": "DOC-002",
            "direction": "forward",
            "relation": "depends_on",
            "peer_id": "DOC-001",
            "reason": "depth-limit",
        },
        {
            "source_id": "DOC-002",
            "direction": "forward",
            "relation": "validated_against",
            "peer_id": "DOC-001",
            "reason": "relation-filter",
        },
    ]


def test_purpose_view_direct_and_projection_output_are_identical(
    tmp_path: Path, capsys
) -> None:
    configured_context_views(tmp_path)

    assert context(tmp_path, "DOC-002", view_name="task") == 0
    direct_text = capsys.readouterr().out
    assert "Purpose view: task (tier 2, forward, authored)" in direct_text
    assert (
        "View omitted: DOC-002 forward validated_against DOC-001 "
        "(relation-filter)"
    ) in direct_text
    assert context(tmp_path, "DOC-002", view_name="task", json_output=True) == 0
    direct_json = capsys.readouterr().out
    assert index_projection(tmp_path, write=True) == 0
    capsys.readouterr()
    assert context(tmp_path, "DOC-002", view_name="task") == 0
    projected_text = capsys.readouterr()
    assert projected_text.err == ""
    assert projected_text.out == direct_text
    assert context(tmp_path, "DOC-002", view_name="task", json_output=True) == 0
    projected_json = capsys.readouterr()
    assert projected_json.err == ""
    assert projected_json.out == direct_json


def test_context_view_policy_change_invalidates_projection(
    tmp_path: Path, capsys
) -> None:
    configured_context_views(tmp_path)
    assert index_projection(tmp_path, write=True) == 0
    capsys.readouterr()

    config_path = tmp_path / CONFIG_FILENAME
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace(
            '[context.views.task]\ntier = 2\ndelivery = "navigation"\n'
            'direction = "forward"',
            '[context.views.task]\ntier = 2\ndelivery = "navigation"\n'
            'direction = "reverse"',
        ),
        encoding="utf-8",
    )

    assert context(tmp_path, "DOC-002", view_name="task", json_output=True) == 0
    captured = capsys.readouterr()
    assert "projection stale: configuration changed" in captured.err
    payload = json.loads(captured.out)
    assert payload["purpose_view"]["direction"] == "reverse"
    assert [item["id"] for item in payload["documents"]] == ["DOC-002", "DOC-003"]


def test_purpose_views_fail_closed_for_unknown_or_conflicting_controls(
    tmp_path: Path, capsys
) -> None:
    configured_context_views(tmp_path)

    assert context(tmp_path, "DOC-002", view_name="missing") == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "context view not found: missing" in captured.err

    assert context(tmp_path, "DOC-002", view_name="task", depth=1) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "cannot combine --view with --depth" in captured.err

    assert context(
        tmp_path,
        "DOC-002",
        view_name="map",
        includes=["DOC-001#index-details"],
    ) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "cannot combine outline delivery with --anchor or --include" in captured.err

    args = build_parser().parse_args(
        ["context", "DOC-002", str(tmp_path), "--view", "task", "--json"]
    )
    assert args.view_name == "task"
    assert args.depth is None
    assert args.include_related is None
    assert args.outline is None


def test_reverse_purpose_view_requires_a_complete_catalog_graph(
    tmp_path: Path, capsys
) -> None:
    configured_context_views(tmp_path)
    area = tmp_path / "plan" / "architecture"
    (area / "broken.md").write_text(
        """\
---
id: DOC-004
revision: 1
depends_on: [DOC-999]
---
# Broken
""",
        encoding="utf-8",
    )
    readme = area / "README.md"
    readme.write_text(
        readme.read_text(encoding="utf-8") + "\n[Broken](broken.md)\n",
        encoding="utf-8",
    )

    assert context(tmp_path, "DOC-001", view_name="downstream") == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "metadata.depends_on references unknown ID DOC-999" in captured.err

    # A forward-only answer remains scoped to its selected source graph.
    assert context(tmp_path, "DOC-002", view_name="task") == 0
    assert "Context packet: DOC-002" in capsys.readouterr().out


def test_purpose_view_text_recommends_compatible_expansion(tmp_path: Path, capsys) -> None:
    configured_context_views(tmp_path)

    assert context(tmp_path, "DOC-002", view_name="map") == 0
    outline = capsys.readouterr().out
    assert "Expand with another configured view" in outline
    assert "drop --outline" not in outline

    assert context(tmp_path, "DOC-002", view_name="task") == 0
    navigation = capsys.readouterr().out
    assert "Expand with --include ID#anchor, another configured view" in navigation
    assert "Expand with --depth" not in navigation


def test_read_document_supports_full_navigation_and_section(
    tmp_path: Path, capsys
) -> None:
    configured_documents(tmp_path)

    assert read_document(tmp_path, "DOC-002", navigation=True) == 0
    assert capsys.readouterr().out.endswith("# Context\n\nSummary.\n")

    assert read_document(tmp_path, "DOC-002", anchor="selected-section") == 0
    assert capsys.readouterr().out == (
        "## Selected section\n\nSelected text.\n\n### Nested\n\nNested text.\n"
    )

    assert read_document(tmp_path, "DOC-002") == 0
    assert "## Other\n\nOther text." in capsys.readouterr().out


def test_read_document_lists_sections_in_machine_stable_order(
    tmp_path: Path, capsys
) -> None:
    configured_documents(tmp_path)

    assert read_document(tmp_path, "DOC-002", list_sections=True) == 0
    assert capsys.readouterr().out == (
        "context\tH1\t8:22\tContext\n"
        "selected-section\tH2\t12:19\tSelected section\n"
        "nested\tH3\t16:19\tNested\n"
        "other\tH2\t20:22\tOther\n"
    )
    args = build_parser().parse_args(["read", "DOC-002", "--list"])
    assert args.list_sections is True


def test_read_document_reports_unknown_id_and_anchor(tmp_path: Path, capsys) -> None:
    configured_documents(tmp_path)

    assert read_document(tmp_path, "DOC-999") == 1
    assert "document ID not found: DOC-999" in capsys.readouterr().err
    assert read_document(tmp_path, "DOC-002", anchor="missing") == 1
    assert "anchor not found in DOC-002: missing" in capsys.readouterr().err


def test_read_fails_without_stdout_on_section_diagnostics(
    tmp_path: Path, capsys
) -> None:
    configured_documents(tmp_path)
    source = tmp_path / "plan" / "architecture" / "context.md"
    source.write_text(
        source.read_text(encoding="utf-8").replace(
            "## Selected section",
            '<a id="duplicate"></a>\n## Selected section\n'
            '<a id="duplicate"></a>\n## Duplicate',
        ),
        encoding="utf-8",
    )

    assert read_document(tmp_path, "DOC-002", list_sections=True) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "duplicate explicit anchor 'duplicate'" in captured.err
    assert validate(tmp_path) == 1
    assert "duplicate explicit anchor 'duplicate'" in capsys.readouterr().err
    assert doctor(tmp_path) == 1
    assert "duplicate explicit anchor 'duplicate'" in capsys.readouterr().err


def test_dependencies_support_forward_and_reverse_queries(
    tmp_path: Path, capsys
) -> None:
    configured_documents(tmp_path)

    assert dependencies(tmp_path, "DOC-002") == 0
    assert capsys.readouterr().out == (
        "depends_on\tDOC-001\t-\nvalidated_against\tDOC-001\t3\n"
    )

    assert dependencies(tmp_path, "DOC-001", reverse=True) == 0
    assert capsys.readouterr().out == (
        "depends_on\tDOC-002\t-\nvalidated_against\tDOC-002\t3\n"
    )


def test_stale_pin_warns_without_failing_validation(tmp_path: Path, capsys) -> None:
    configured_documents(tmp_path)
    readme = tmp_path / "plan" / "architecture" / "README.md"
    readme.write_text(readme.read_text(encoding="utf-8").replace("revision: 3", "revision: 4"))

    assert validate(tmp_path) == 0
    captured = capsys.readouterr()
    assert "WARNING: architecture/context.md:" in captured.err
    assert "DOC-001@3 is stale" in captured.err
    assert captured.out == "Markdown navigation is valid.\n"


def test_completed_roadmap_snapshot_rule_preserves_pin_without_warning(
    tmp_path: Path, capsys
) -> None:
    configured_documents(tmp_path)
    config_path = tmp_path / CONFIG_FILENAME
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace(
            "snapshot_rules = []",
            'snapshot_rules = [{ source_type = "roadmap", '
            'source_status = "completed" }]',
        ),
        encoding="utf-8",
    )
    source = tmp_path / "plan" / "architecture" / "context.md"
    source.write_text(
        source.read_text(encoding="utf-8")
        .replace("revision: 1", "revision: 1\ntype: roadmap\nstatus: completed")
        .replace("DOC-001@3", "DOC-001@2"),
        encoding="utf-8",
    )

    assert validate(tmp_path) == 0
    captured = capsys.readouterr()
    assert captured.out == "Markdown navigation is valid.\n"
    assert captured.err == ""

    assert context(tmp_path, "DOC-002", depth=1, json_output=True) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["freshness"] == [
        {
            "source_id": "DOC-002",
            "target_id": "DOC-001",
            "pinned_revision": 2,
            "current_revision": 3,
            "classification": "historical snapshot",
        }
    ]

    assert impact(tmp_path, "DOC-001") == 0
    report = capsys.readouterr().out
    assert "| `DOC-002` | validated_against | 2 | historical snapshot |" in report

    source.write_text(
        source.read_text(encoding="utf-8").replace(
            "status: completed", "status: active"
        ),
        encoding="utf-8",
    )
    assert validate(tmp_path) == 0
    assert "DOC-001@2 is stale" in capsys.readouterr().err


def test_forward_dependencies_fail_closed_for_source_graph_errors(
    tmp_path: Path, capsys
) -> None:
    configured_documents(tmp_path)
    source = tmp_path / "plan" / "architecture" / "context.md"
    source.write_text(
        source.read_text(encoding="utf-8").replace(
            "depends_on: [DOC-001]",
            "depends_on: [DOC-001, ../legacy.md, DOC-999]",
        ),
        encoding="utf-8",
    )

    assert dependencies(tmp_path, "DOC-002") == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    # `../legacy.md` does not resolve to a cataloged document, so it is a
    # permanent boundary rather than a document relation: it never blocks,
    # even in the default strict mode.
    assert "legacy.md" not in captured.err
    assert "references unknown ID DOC-999" in captured.err

    assert dependencies(tmp_path, "DOC-001") == 0
    assert capsys.readouterr().out == ""


def test_reverse_dependencies_fail_closed_for_any_graph_error(
    tmp_path: Path, capsys
) -> None:
    configured_documents(tmp_path)
    source = tmp_path / "plan" / "architecture" / "context.md"
    source.write_text(
        source.read_text(encoding="utf-8").replace(
            "depends_on: [DOC-001]", "depends_on: [DOC-001, DOC-999]"
        ),
        encoding="utf-8",
    )

    assert dependencies(tmp_path, "DOC-001", reverse=True) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "references unknown ID DOC-999" in captured.err


def test_stale_pin_warns_but_does_not_block_dependencies(
    tmp_path: Path, capsys
) -> None:
    configured_documents(tmp_path)
    readme = tmp_path / "plan" / "architecture" / "README.md"
    readme.write_text(readme.read_text(encoding="utf-8").replace("revision: 3", "revision: 4"))

    assert dependencies(tmp_path, "DOC-002") == 0
    captured = capsys.readouterr()
    assert captured.out == (
        "depends_on\tDOC-001\t-\nvalidated_against\tDOC-001\t3\n"
    )
    assert "WARNING: architecture/context.md:" in captured.err
    assert "DOC-001@3 is stale" in captured.err


def test_non_graph_metadata_error_does_not_block_dependencies(
    tmp_path: Path, capsys
) -> None:
    configured_documents(tmp_path)
    source = tmp_path / "plan" / "architecture" / "context.md"
    source.write_text(
        source.read_text(encoding="utf-8").replace(
            "revision: 1", "revision: 1\ntype: []"
        ),
        encoding="utf-8",
    )

    assert dependencies(tmp_path, "DOC-002") == 0
    captured = capsys.readouterr()
    assert captured.out == (
        "depends_on\tDOC-001\t-\nvalidated_against\tDOC-001\t3\n"
    )
    assert captured.err == ""


def test_navigation_extension_is_contiguous_and_falls_back_when_absent(
    tmp_path: Path, capsys
) -> None:
    configured_documents(tmp_path)
    config_path = tmp_path / CONFIG_FILENAME
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace(
            "extend_through = []",
            'extend_through = ["selected-section", "other"]',
        ),
        encoding="utf-8",
    )

    assert read_document(tmp_path, "DOC-002", navigation=True) == 0
    output = capsys.readouterr().out
    assert "## Selected section" in output
    assert "### Nested" in output
    assert output.endswith("## Other\n\nOther text.\n")

    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace(
            'extend_through = ["selected-section", "other"]',
            'extend_through = ["not-present"]',
        ),
        encoding="utf-8",
    )
    assert read_document(tmp_path, "DOC-002", navigation=True) == 0
    assert capsys.readouterr().out.endswith("# Context\n\nSummary.\n")


def test_navigation_extension_requires_matching_h2(
    tmp_path: Path, capsys
) -> None:
    configured_documents(tmp_path)
    config_path = tmp_path / CONFIG_FILENAME
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace(
            "extend_through = []", 'extend_through = ["nested"]'
        ),
        encoding="utf-8",
    )

    assert read_document(tmp_path, "DOC-002", navigation=True) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "anchor 'nested' resolves to H3, expected H2" in captured.err
    assert validate(tmp_path) == 1
    assert "anchor 'nested' resolves to H3, expected H2" in capsys.readouterr().err


def test_reverse_dependencies_only_report_warnings_related_to_target(
    tmp_path: Path, capsys
) -> None:
    configured_documents(tmp_path)
    area = tmp_path / "plan" / "architecture"
    (area / "independent.md").write_text(
        """\
---
id: DOC-003
revision: 1
validated_against: [DOC-002@9]
---

# Independent
""",
        encoding="utf-8",
    )
    readme = area / "README.md"
    readme.write_text(
        readme.read_text(encoding="utf-8") + "\n[Independent](independent.md)\n",
        encoding="utf-8",
    )

    assert dependencies(tmp_path, "DOC-001", reverse=True) == 0
    captured = capsys.readouterr()
    assert captured.out == (
        "depends_on\tDOC-002\t-\nvalidated_against\tDOC-002\t3\n"
    )
    assert captured.err == ""

    assert dependencies(tmp_path, "DOC-002", reverse=True) == 0
    captured = capsys.readouterr()
    assert captured.out == "validated_against\tDOC-003\t9\n"
    assert "WARNING: architecture/independent.md:" in captured.err
    assert "DOC-002@9 is stale" in captured.err


def test_reverse_dependencies_fail_closed_for_unmapped_markdown(
    tmp_path: Path, capsys
) -> None:
    configured_documents(tmp_path)
    (tmp_path / "plan" / "orphan.md").write_text("# Unmapped\n", encoding="utf-8")

    assert dependencies(tmp_path, "DOC-001", reverse=True) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "ERROR: orphan.md: Markdown is not mapped" in captured.err


def test_context_outline_json_reports_concrete_section_size_maps(
    tmp_path: Path, capsys
) -> None:
    configured_documents(tmp_path)

    assert context(tmp_path, "DOC-002", depth=1, outline=True, json_output=True) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema_version"] == 1
    assert payload["outline"] is True
    assert payload["target"] == "DOC-002"
    assert [item["id"] for item in payload["documents"]] == ["DOC-002", "DOC-001"]

    target_document = payload["documents"][0]
    assert target_document["path"] == "architecture/context.md"
    assert target_document["revision"] == 1
    assert target_document["relations"] == ["target"]
    assert "navigation" not in target_document
    assert "explicit_sections" not in target_document
    assert "omitted_h2" not in target_document
    assert target_document["sections"] == [
        {"anchor": "context", "title": "Context", "level": 1, "lines": 15, "bytes": 105},
        {
            "anchor": "selected-section",
            "title": "Selected section",
            "level": 2,
            "lines": 8,
            "bytes": 62,
        },
        {"anchor": "nested", "title": "Nested", "level": 3, "lines": 4, "bytes": 25},
        {"anchor": "other", "title": "Other", "level": 2, "lines": 3, "bytes": 21},
    ]

    dependency_document = payload["documents"][1]
    assert dependency_document["revision"] == 3
    assert dependency_document["sections"] == [
        {
            "anchor": "architecture",
            "title": "Architecture",
            "level": 1,
            "lines": 9,
            "bytes": 80,
        },
        {
            "anchor": "index-details",
            "title": "Index details",
            "level": 2,
            "lines": 5,
            "bytes": 49,
        },
    ]
    assert payload["stats"] == {
        "included_documents": 2,
        "listed_sections": 6,
        "total_section_bytes": 342,
    }


def test_context_outline_text_matches_deterministic_shape(
    tmp_path: Path, capsys
) -> None:
    configured_documents(tmp_path)

    assert context(tmp_path, "DOC-002", depth=1, outline=True) == 0
    output = capsys.readouterr().out
    assert output == (
        "# Context outline: DOC-002\n"
        "\n"
        "- Dependency depth: 1\n"
        "- Related traversal: omitted\n"
        "\n"
        "## DOC-002 — architecture/context.md\n"
        "\n"
        "Relations: target.\n"
        "\n"
        "| Anchor | Level | Lines | Bytes | Title |\n"
        "|---|---|---|---|---|\n"
        "| `context` | H1 | 15 | 105 | Context |\n"
        "| `selected-section` | H2 | 8 | 62 | Selected section |\n"
        "| `nested` | H3 | 4 | 25 | Nested |\n"
        "| `other` | H2 | 3 | 21 | Other |\n"
        "\n"
        "## DOC-001 — architecture/README.md\n"
        "\n"
        "Relations: depends_on, validated_against.\n"
        "\n"
        "| Anchor | Level | Lines | Bytes | Title |\n"
        "|---|---|---|---|---|\n"
        "| `architecture` | H1 | 9 | 80 | Architecture |\n"
        "| `index-details` | H2 | 5 | 49 | Index details |\n"
        "\n"
        "## Diagnostics and boundaries\n"
        "\n"
        "- No stale revision pins among included documents.\n"
        "- No unresolved/resource boundaries among included documents.\n"
        "- Fetch content with --include ID#anchor, or drop --outline for "
        "full navigation.\n"
        "\n"
        "## Packet stats\n"
        "\n"
        "- Included documents: 2\n"
        "- Listed sections: 6\n"
        "- Total section bytes: 342\n"
    )

    args = build_parser().parse_args(["context", "DOC-002", "--outline"])
    assert args.outline is True


def test_context_outline_rejects_anchor_and_include_with_no_partial_stdout(
    tmp_path: Path, capsys
) -> None:
    configured_documents(tmp_path)

    assert context(tmp_path, "DOC-002", outline=True, anchor="other") == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert (
        captured.err
        == "ERROR: cannot combine --outline with --anchor or --include\n"
    )
    assert (
        context(tmp_path, "DOC-002", outline=True, includes=["DOC-001#index-details"])
        == 1
    )
    captured = capsys.readouterr()
    assert captured.out == ""
    assert (
        captured.err
        == "ERROR: cannot combine --outline with --anchor or --include\n"
    )


def test_compact_context_deduplicates_parent_child_and_aggregates_reasons(
    tmp_path: Path, capsys
) -> None:
    configured_documents(tmp_path)

    assert context(
        tmp_path,
        "DOC-002",
        depth=1,
        anchor="selected-section",
        includes=["DOC-002#nested", "DOC-002#nested"],
        compact=True,
        json_output=True,
    ) == 0
    direct = capsys.readouterr()
    payload = json.loads(direct.out)
    assert payload["compact"] is True
    target = payload["documents"][0]
    assert "navigation" not in target
    assert "explicit_sections" not in target
    assert "sections" not in target
    assert len(target["content_fragments"]) == 1
    assert target["content_fragments"][0]["content"].count("Nested text.") == 1
    manifest = {row["address"]: row for row in target["content_manifest"]}
    assert set(manifest) == {
        "DOC-002",
        "DOC-002#nested",
        "DOC-002#selected-section",
    }
    assert manifest["DOC-002#nested"]["delivery"] == "covered-by-fragment"
    assert manifest["DOC-002#nested"]["reasons"] == ["explicit include"]
    dependency = payload["documents"][1]
    assert dependency["inclusion_reasons"] == [
        {"via_id": "DOC-002", "direction": "forward", "relation": "depends_on"},
        {
            "via_id": "DOC-002",
            "direction": "forward",
            "relation": "validated_against",
        },
    ]

    assert index_projection(tmp_path, write=True) == 0
    capsys.readouterr()
    assert context(
        tmp_path,
        "DOC-002",
        depth=1,
        anchor="selected-section",
        includes=["DOC-002#nested", "DOC-002#nested"],
        compact=True,
        json_output=True,
    ) == 0
    projected = capsys.readouterr()
    assert projected.out == direct.out
    assert projected.err == ""


def test_compact_context_text_aggregates_view_diagnostics_with_json_drilldown(
    tmp_path: Path, capsys
) -> None:
    configured_context_views(tmp_path)

    assert context(tmp_path, "DOC-002", view_name="boundary", compact=True) == 0
    text = capsys.readouterr().out
    assert "# Compact context packet: DOC-002" in text
    assert "View omissions: 1 forward depends_on (depth-limit)" in text
    assert "View omissions: 1 forward validated_against (relation-filter)" in text
    assert "full rows: rerun with --json" in text
    assert "DOC-001 (depth-limit)" not in text

    assert context(
        tmp_path,
        "DOC-002",
        view_name="boundary",
        compact=True,
        json_output=True,
    ) == 0
    payload = json.loads(capsys.readouterr().out)
    assert len(payload["view_omissions"]) == 2
    assert payload["view_omissions"][0]["peer_id"] == "DOC-001"


def test_compact_context_rejects_outline_delivery_without_partial_stdout(
    tmp_path: Path, capsys
) -> None:
    configured_documents(tmp_path)

    assert context(tmp_path, "DOC-002", outline=True, compact=True) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.endswith(
        "ERROR: cannot combine outline delivery with --compact\n"
    )

    args = build_parser().parse_args(["context", "DOC-002", "--compact"])
    assert args.compact is True


def test_compact_context_preserves_same_relation_paths_from_multiple_documents(
    tmp_path: Path, capsys
) -> None:
    configured_documents(tmp_path)
    area = tmp_path / "plan" / "architecture"
    readme = area / "README.md"
    readme.write_text(
        readme.read_text(encoding="utf-8").replace(
            "revision: 3", "revision: 3\ndepends_on: [DOC-004]"
        )
        + "\n[Third](third.md)\n[Fourth](fourth.md)\n",
        encoding="utf-8",
    )
    context_path = area / "context.md"
    context_path.write_text(
        context_path.read_text(encoding="utf-8").replace(
            "depends_on: [DOC-001]", "depends_on: [DOC-001, DOC-003]"
        ),
        encoding="utf-8",
    )
    (area / "third.md").write_text(
        "---\nid: DOC-003\nrevision: 1\ndepends_on: [DOC-004]\n---\n# Third\n",
        encoding="utf-8",
    )
    (area / "fourth.md").write_text(
        "---\nid: DOC-004\nrevision: 1\n---\n# Fourth\n",
        encoding="utf-8",
    )

    assert context(
        tmp_path, "DOC-002", depth=2, compact=True, json_output=True
    ) == 0
    payload = json.loads(capsys.readouterr().out)
    fourth = next(item for item in payload["documents"] if item["id"] == "DOC-004")
    assert fourth["inclusion_reasons"] == [
        {"via_id": "DOC-001", "direction": "forward", "relation": "depends_on"},
        {"via_id": "DOC-003", "direction": "forward", "relation": "depends_on"},
    ]


def test_compact_context_keeps_attention_rows_and_summarizes_adoption_noise(
    tmp_path: Path, capsys
) -> None:
    configured_documents(tmp_path)
    config_path = tmp_path / CONFIG_FILENAME
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace(
            'legacy_paths = "strict"', 'legacy_paths = "resolve-with-warning"'
        ),
        encoding="utf-8",
    )
    context_path = tmp_path / "plan" / "architecture" / "context.md"
    context_path.write_text(
        context_path.read_text(encoding="utf-8")
        .replace("depends_on: [DOC-001]", "depends_on: [README.md]")
        .replace(
            "validated_against: [DOC-001@3]",
            "validated_against: [DOC-001@2]\n"
            "derived_from: [https://example.com/source]",
        ),
        encoding="utf-8",
    )

    assert context(tmp_path, "DOC-002", compact=True) == 0
    text = capsys.readouterr().out
    assert "DOC-002: DOC-001@2, current 3 — STALE" in text
    assert (
        "DOC-002: unresolved/resource derived_from "
        "https://example.com/source (external URL)" in text
    )
    assert "Adoption mappings: 1 depends_on; full rows: rerun with --json." in text
    assert "depends_on README.md -> DOC-001" not in text

    assert context(tmp_path, "DOC-002", compact=True, json_output=True) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["migrations"] == [
        {
            "source_id": "DOC-002",
            "relation": "depends_on",
            "value": "README.md",
            "target_id": "DOC-001",
        }
    ]
    assert payload["boundaries"][0]["value"] == "https://example.com/source"

def test_context_json_preserves_navigation_and_adds_typed_revision(
    tmp_path: Path, capsys
) -> None:
    configured_documents(tmp_path)

    assert context(tmp_path, "DOC-002", depth=1, json_output=True) == 0
    payload = json.loads(capsys.readouterr().out)
    target_document = payload["documents"][0]
    assert target_document["navigation"] == (
        "---\n"
        "id: DOC-002\n"
        "revision: 1\n"
        "depends_on: [DOC-001]\n"
        "validated_against: [DOC-001@3]\n"
        "---\n\n"
        "# Context\n\n"
        "Summary."
    )
    assert target_document["revision"] == 1
    # Every "sections" entry is additive alongside the unchanged existing keys.
    assert target_document["sections"][0] == {
        "anchor": "context",
        "title": "Context",
        "level": 1,
        "lines": 15,
        "bytes": 105,
    }

    # The JSON field keeps its v1 navigation meaning byte-for-byte aligned
    # with the text packet rather than changing an existing schema field.
    assert context(tmp_path, "DOC-002", depth=1) == 0
    text_output = capsys.readouterr().out
    assert "id: DOC-002" in text_output

def test_assume_known_current_omits_content_with_declared_marker(
    tmp_path: Path, capsys
) -> None:
    configured_documents(tmp_path)

    assert context(tmp_path, "DOC-002", depth=1, assume_known=["DOC-002@1"]) == 0
    output = capsys.readouterr().out
    target, _, rest = output.partition("## DOC-001")
    # The declared, current-revision target keeps its heading, Relations line
    # and diagnostics but omits navigation for the declared-cache coverage line.
    assert "## DOC-002 — architecture/context.md" in target
    assert "Relations: target." in target
    assert "\n# Context\n" not in target
    assert "Summary." not in target
    assert (
        "_Coverage: content omitted — declared known at revision 1 (current). "
        "Omitted H2: selected-section, other._" in target
    )
    # The undeclared dependency still serves full navigation.
    assert "# Architecture" in rest
    assert "- Content omitted (assumed known): 1" in output


def test_assume_known_stale_declaration_includes_content_and_notes_mismatch(
    tmp_path: Path, capsys
) -> None:
    configured_documents(tmp_path)

    assert context(tmp_path, "DOC-002", depth=1, assume_known=["DOC-002@9"]) == 0
    output = capsys.readouterr().out
    # A stale declaration must never omit content: navigation returns and a
    # revision-checked diagnostics note records the mismatch.
    assert "# Context" in output
    assert (
        "- DOC-002: assumed known at revision 9, current 1 — content included"
        in output
    )
    assert "- Content omitted (assumed known): 0" in output


def test_assume_known_json_marks_omission_and_mismatches(
    tmp_path: Path, capsys
) -> None:
    configured_documents(tmp_path)

    assert (
        context(tmp_path, "DOC-002", depth=1, assume_known=["DOC-002@1"], json_output=True)
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    target_document = payload["documents"][0]
    assert "navigation" not in target_document
    assert target_document["content_omitted"] == {
        "reason": "assumed-known",
        "declared_revision": 1,
    }
    assert target_document["omitted_h2"] == ["selected-section", "other"]
    assert "sections" in target_document
    assert payload["assume_known_mismatches"] == []
    assert payload["stats"]["assumed_known_omitted"] == 1

    assert (
        context(tmp_path, "DOC-002", depth=1, assume_known=["DOC-002@9"], json_output=True)
        == 0
    )
    stale = json.loads(capsys.readouterr().out)
    assert "navigation" in stale["documents"][0]
    assert "content_omitted" not in stale["documents"][0]
    assert stale["assume_known_mismatches"] == [
        {"id": "DOC-002", "declared_revision": 9, "current_revision": 1}
    ]
    assert stale["stats"]["assumed_known_omitted"] == 0


def test_assume_known_explicit_include_beats_declared_cache(
    tmp_path: Path, capsys
) -> None:
    configured_documents(tmp_path)

    assert (
        context(
            tmp_path,
            "DOC-002",
            depth=1,
            assume_known=["DOC-002@1"],
            includes=["DOC-002#other"],
        )
        == 0
    )
    output = capsys.readouterr().out
    # Navigation stays omitted, but the explicitly requested section prints.
    assert "### Explicit section `other`" in output
    assert "Other text." in output
    assert "\n# Context\n" not in output
    assert (
        "_Coverage: content omitted — declared known at revision 1 (current). "
        "Omitted H2: selected-section._" in output
    )


def test_assume_known_rejects_malformed_value_without_stdout(
    tmp_path: Path, capsys
) -> None:
    configured_documents(tmp_path)

    assert context(tmp_path, "DOC-002", assume_known=["DOC-002@x"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "ERROR: invalid --assume-known value: 'DOC-002@x'\n"

    assert context(tmp_path, "DOC-002", assume_known=["DOC-002@0"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "ERROR: invalid --assume-known value: 'DOC-002@0'\n"

    assert context(tmp_path, "DOC-002", assume_known=["DOC-002"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "ERROR: invalid --assume-known value: 'DOC-002'\n"


def test_assume_known_rejects_conflicting_declarations_but_allows_duplicates(
    tmp_path: Path, capsys
) -> None:
    configured_documents(tmp_path)

    assert (
        context(
            tmp_path,
            "DOC-002",
            assume_known=["DOC-002@3", "DOC-002@7"],
        )
        == 1
    )
    captured = capsys.readouterr()
    assert captured.out == ""
    assert (
        captured.err
        == "ERROR: conflicting --assume-known declarations for DOC-002\n"
    )

    # An exact duplicate declaration is not a conflict; it behaves like a
    # single declaration.
    assert (
        context(
            tmp_path,
            "DOC-002",
            depth=1,
            assume_known=["DOC-002@1", "DOC-002@1"],
        )
        == 0
    )
    output = capsys.readouterr().out
    assert (
        "_Coverage: content omitted — declared known at revision 1 (current). "
        "Omitted H2: selected-section, other._" in output
    )


def test_assume_known_unknown_id_uses_standard_error(
    tmp_path: Path, capsys
) -> None:
    configured_documents(tmp_path)

    assert context(tmp_path, "DOC-002", assume_known=["DOC-404@1"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.endswith("ERROR: document ID not found: DOC-404\n")


def test_since_delta_packet_includes_only_changed_sections(
    tmp_path: Path, capsys
) -> None:
    configured_documents(tmp_path)
    assert index_projection(tmp_path, write=True) == 0
    capsys.readouterr()
    generation = _current_generation(tmp_path)

    context_md = tmp_path / "plan" / "architecture" / "context.md"
    context_md.write_text(
        context_md.read_text(encoding="utf-8").replace(
            "Other text.", "Changed other text."
        ),
        encoding="utf-8",
    )
    # Rebuild so the current generation serves while --since points at the older
    # retained generation.
    assert index_projection(tmp_path, write=True) == 0
    capsys.readouterr()

    assert context(tmp_path, "DOC-002", depth=1, since=generation) == 0
    output = capsys.readouterr().out
    short = generation[:12]
    # Only the edited section's content returns.
    assert "### Changed section `other`" in output
    assert "Changed other text." in output
    assert "### Changed section `selected-section`" not in output
    assert "Selected text." not in output
    # The untouched sibling document is omitted with the delta coverage marker.
    assert (
        f"_Coverage: content omitted — unchanged since {short}. Omitted H2: "
        "index-details._" in output
    )
    assert (
        f"- Delta vs generation {short}: 1 changed, 1 unchanged omitted" in output
    )


def test_since_delta_packet_json_shape(tmp_path: Path, capsys) -> None:
    configured_documents(tmp_path)
    assert index_projection(tmp_path, write=True) == 0
    capsys.readouterr()
    generation = _current_generation(tmp_path)

    context_md = tmp_path / "plan" / "architecture" / "context.md"
    context_md.write_text(
        context_md.read_text(encoding="utf-8").replace(
            "Other text.", "Changed other text."
        ),
        encoding="utf-8",
    )
    assert index_projection(tmp_path, write=True) == 0
    capsys.readouterr()

    assert context(tmp_path, "DOC-002", depth=1, since=generation, json_output=True) == 0
    payload = json.loads(capsys.readouterr().out)
    short = generation[:12]
    changed = payload["documents"][0]
    assert changed["id"] == "DOC-002"
    # "context" (the H1) is the complete truth signal: its slice spans the
    # whole document, so the edit inside "other" changes its hash too, even
    # though only "other" is served as a content block.
    assert changed["changed_sections"] == ["context", "other"]
    assert changed["removed_sections"] == []
    assert changed["metadata_changes"] == []
    assert changed["source_changed_outside_sections"] is False
    assert {section["anchor"] for section in changed["explicit_sections"]} == {"other"}
    assert "navigation" in changed
    unchanged = payload["documents"][1]
    assert unchanged["id"] == "DOC-001"
    assert unchanged["content_omitted"] == {
        "reason": "unchanged-since",
        "generation": short,
    }
    assert "navigation" not in unchanged
    # --since keeps assume-known-only keys absent, so JSON stays byte-stable.
    assert "assume_known_mismatches" not in payload
    assert "assumed_known_omitted" not in payload["stats"]


def test_since_delta_packet_serves_document_absent_from_generation_in_full(
    tmp_path: Path, capsys
) -> None:
    configured_documents(tmp_path)
    area = tmp_path / "plan" / "architecture"
    readme = area / "README.md"
    context_document = area / "context.md"
    readme_content = readme.read_text(encoding="utf-8")
    context_content = context_document.read_text(encoding="utf-8")
    readme.write_text(
        readme_content.replace("\n[Context](context.md)\n", "\n"),
        encoding="utf-8",
    )
    context_document.unlink()
    assert index_projection(tmp_path, write=True) == 0
    capsys.readouterr()
    generation = _current_generation(tmp_path)

    # Restore DOC-002 only after capturing a legitimate older generation in
    # which it did not exist. The retained generation remains hash-verifiable.
    readme.write_text(readme_content, encoding="utf-8")
    context_document.write_text(context_content, encoding="utf-8")

    assert context(tmp_path, "DOC-002", depth=1, since=generation) == 0
    output = capsys.readouterr().out
    short = generation[:12]
    assert f"- DOC-002: new since {short}" in output
    # A brand-new document is served whole: navigation lead-in plus every
    # non-extend_through H2 as a changed block, nothing omitted.
    assert "Summary." in output
    assert "### Changed section `selected-section`" in output
    assert "Selected text." in output
    assert "### Changed section `nested`" not in output
    assert "### Changed section `other`" in output
    assert "Other text." in output
    assert "Omitted H2: none._" in output

    assert (
        context(tmp_path, "DOC-002", depth=1, since=generation, json_output=True)
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    changed = next(item for item in payload["documents"] if item["id"] == "DOC-002")
    assert changed["changed_sections"] == [
        "context",
        "selected-section",
        "nested",
        "other",
    ]
    assert {section["anchor"] for section in changed["explicit_sections"]} == {
        "selected-section",
        "other",
    }
    assert "navigation" in changed


def test_since_rejects_semantically_tampered_retained_manifest(
    tmp_path: Path, capsys
) -> None:
    configured_documents(tmp_path)
    assert index_projection(tmp_path, write=True) == 0
    capsys.readouterr()
    generation = _current_generation(tmp_path)
    manifest_path = (
        tmp_path
        / ".docsystem"
        / "cache"
        / "generations"
        / generation
        / "manifest.json"
    )
    original_manifest = manifest_path.read_text(encoding="utf-8")
    manifest = json.loads(original_manifest)
    manifest["documents"]["DOC-002"]["source_sha256"] = "0" * 64
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    assert context(tmp_path, "DOC-002", since=generation) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == f"ERROR: unknown projection generation: {generation}\n"

    manifest_path.write_text(original_manifest, encoding="utf-8")
    document_shard = (
        tmp_path
        / ".docsystem"
        / "cache"
        / "generations"
        / generation
        / "documents"
        / "DOC"
        / "000000"
        / "DOC-002.json"
    )
    shard = json.loads(document_shard.read_text(encoding="utf-8"))
    shard["revision"] = 99
    document_shard.write_text(json.dumps(shard), encoding="utf-8")

    assert context(tmp_path, "DOC-002", since=generation) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == f"ERROR: unknown projection generation: {generation}\n"


def test_since_reports_removed_sections_and_metadata_only_changes(
    tmp_path: Path, capsys
) -> None:
    configured_documents(tmp_path)
    assert index_projection(tmp_path, write=True) == 0
    capsys.readouterr()
    generation = _current_generation(tmp_path)

    context_md = tmp_path / "plan" / "architecture" / "context.md"
    content = context_md.read_text(encoding="utf-8")
    content = content.replace("revision: 1", "revision: 2", 1)
    content = content.split("\n## Other\n", 1)[0] + "\n"
    context_md.write_text(content, encoding="utf-8")

    assert context(
        tmp_path, "DOC-002", depth=1, since=generation, json_output=True
    ) == 0
    payload = json.loads(capsys.readouterr().out)
    changed = payload["documents"][0]
    assert changed["revision"] == 2
    assert changed["removed_sections"] == ["other"]
    assert changed["metadata_changes"] == [
        {"field": "revision", "before": 1, "after": 2}
    ]
    assert changed["source_changed_outside_sections"] is False
    assert "revision: 2" in changed["navigation"]


def test_since_metadata_only_change_is_explicit_in_json(
    tmp_path: Path, capsys
) -> None:
    configured_documents(tmp_path)
    assert index_projection(tmp_path, write=True) == 0
    capsys.readouterr()
    generation = _current_generation(tmp_path)

    context_md = tmp_path / "plan" / "architecture" / "context.md"
    context_md.write_text(
        context_md.read_text(encoding="utf-8").replace(
            "revision: 1", "revision: 2", 1
        ),
        encoding="utf-8",
    )

    assert context(
        tmp_path, "DOC-002", depth=1, since=generation, json_output=True
    ) == 0
    payload = json.loads(capsys.readouterr().out)
    changed = payload["documents"][0]
    assert changed["changed_sections"] == []
    assert changed["removed_sections"] == []
    assert changed["metadata_changes"] == [
        {"field": "revision", "before": 1, "after": 2}
    ]
    assert changed["source_changed_outside_sections"] is True
    assert "revision: 2" in changed["navigation"]


def test_since_explicit_include_beats_unchanged_omission(
    tmp_path: Path, capsys
) -> None:
    configured_documents(tmp_path)
    assert index_projection(tmp_path, write=True) == 0
    capsys.readouterr()
    generation = _current_generation(tmp_path)

    assert context(
        tmp_path,
        "DOC-002",
        depth=1,
        since=generation,
        includes=["DOC-002#other"],
        json_output=True,
    ) == 0
    payload = json.loads(capsys.readouterr().out)
    target = payload["documents"][0]
    assert target["content_omitted"] == {
        "reason": "unchanged-since",
        "generation": generation[:12],
    }
    assert target["explicit_sections"] == [
        {"anchor": "other", "content": "## Other\n\nOther text."}
    ]


def test_since_delta_h1_lead_in_change_serves_via_navigation_only(
    tmp_path: Path, capsys
) -> None:
    configured_documents(tmp_path)
    assert index_projection(tmp_path, write=True) == 0
    capsys.readouterr()
    generation = _current_generation(tmp_path)

    context_md = tmp_path / "plan" / "architecture" / "context.md"
    context_md.write_text(
        context_md.read_text(encoding="utf-8").replace(
            "Summary.", "Updated summary."
        ),
        encoding="utf-8",
    )
    assert index_projection(tmp_path, write=True) == 0
    capsys.readouterr()

    assert context(tmp_path, "DOC-002", depth=1, since=generation) == 0
    output = capsys.readouterr().out
    # Navigation already carries the H1 lead-in change; no block, no
    # duplication.
    assert "Updated summary." in output
    assert output.count("Updated summary.") == 1
    assert "### Changed section" not in output
    assert (
        "_Coverage: navigation. Omitted H2: selected-section, other._" in output
    )

    assert (
        context(tmp_path, "DOC-002", depth=1, since=generation, json_output=True)
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    changed = next(item for item in payload["documents"] if item["id"] == "DOC-002")
    # The H1 still signals via changed_sections even though it renders no block.
    assert changed["changed_sections"] == ["context"]
    assert changed["explicit_sections"] == []


def test_since_delta_nested_change_emits_one_block_with_no_hole(
    tmp_path: Path, capsys
) -> None:
    configured_documents(tmp_path)
    assert index_projection(tmp_path, write=True) == 0
    capsys.readouterr()
    generation = _current_generation(tmp_path)

    context_md = tmp_path / "plan" / "architecture" / "context.md"
    context_md.write_text(
        context_md.read_text(encoding="utf-8").replace(
            "Nested text.", "Updated nested text."
        ),
        encoding="utf-8",
    )
    assert index_projection(tmp_path, write=True) == 0
    capsys.readouterr()

    assert context(tmp_path, "DOC-002", depth=1, since=generation) == 0
    output = capsys.readouterr().out
    # Exactly one block for the enclosing H2; its own lead-in text is present
    # alongside the nested change, so there is no hole.
    assert output.count("### Changed section") == 1
    assert "### Changed section `selected-section`" in output
    assert "### Changed section `nested`" not in output
    assert "Selected text." in output
    assert "Updated nested text." in output
    assert (
        "_Coverage: navigation + explicit sections. Omitted H2: other._" in output
    )

    assert (
        context(tmp_path, "DOC-002", depth=1, since=generation, json_output=True)
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    changed = next(item for item in payload["documents"] if item["id"] == "DOC-002")
    assert changed["changed_sections"] == ["context", "selected-section", "nested"]
    assert {section["anchor"] for section in changed["explicit_sections"]} == {
        "selected-section"
    }


def test_since_rejects_unknown_short_and_ambiguous_generations(
    tmp_path: Path, capsys
) -> None:
    configured_documents(tmp_path)
    assert index_projection(tmp_path, write=True) == 0
    capsys.readouterr()
    generation = _current_generation(tmp_path)

    assert context(tmp_path, "DOC-002", since="0" * 64) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == f"ERROR: unknown projection generation: {'0' * 64}\n"

    # A prefix shorter than 12 characters is refused even if it is unique.
    short = generation[:8]
    assert context(tmp_path, "DOC-002", since=short) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == f"ERROR: unknown projection generation: {short}\n"

    # Two generations sharing a 12-char prefix make that prefix ambiguous.
    generations_dir = tmp_path / ".docsystem" / "cache" / "generations"
    prefix = "a" * 12
    for suffix in ("1" * 52, "2" * 52):
        fake = generations_dir / (prefix + suffix)
        fake.mkdir(parents=True)
        (fake / "manifest.json").write_text(
            json.dumps({"generation": prefix + suffix, "documents": {}}),
            encoding="utf-8",
        )
    assert context(tmp_path, "DOC-002", since=prefix) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == f"ERROR: unknown projection generation: {prefix}\n"


def test_context_flag_combinations_fail_closed(tmp_path: Path, capsys) -> None:
    configured_documents(tmp_path)

    assert context(tmp_path, "DOC-002", since="a" * 12, assume_known=["DOC-002@1"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "ERROR: cannot combine --since with --assume-known\n"

    assert context(tmp_path, "DOC-002", outline=True, assume_known=["DOC-002@1"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert (
        captured.err
        == "ERROR: cannot combine --outline with --assume-known or --since\n"
    )

    assert context(tmp_path, "DOC-002", outline=True, since="a" * 12) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert (
        captured.err
        == "ERROR: cannot combine --outline with --assume-known or --since\n"
    )

    parsed = build_parser().parse_args(
        [
            "context",
            "DOC-002",
            "--assume-known",
            "DOC-001@2",
            "--assume-known",
            "DOC-002@1",
            "--since",
            "deadbeefcafe",
        ]
    )
    assert parsed.assume_known == ["DOC-001@2", "DOC-002@1"]
    assert parsed.since == "deadbeefcafe"
