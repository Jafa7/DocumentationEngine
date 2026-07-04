from pathlib import Path

from docsystem.catalog import build_catalog, build_dependency_graph
from docsystem.cli import (
    build_parser,
    changes,
    context,
    doctor,
    impact,
    index_projection,
    migration_report,
    validate,
)
from docsystem.config import CONFIG_FILENAME, DEFAULT_CONFIG, load_config
from docsystem.projection import build_projection, write_projection


def vertical_project(tmp_path: Path) -> Path:
    config = (
        DEFAULT_CONFIG.replace("[areas]\n", '[areas]\nworkspace = "."\n')
        .replace('legacy_paths = "strict"', 'legacy_paths = "resolve-with-warning"')
        .replace("snapshot_types = []", 'snapshot_types = ["review", "experiment"]')
        .replace("extend_through = []", 'extend_through = ["summary", "contents"]')
    )
    (tmp_path / CONFIG_FILENAME).write_text(config, encoding="utf-8")
    root = tmp_path / "plan"
    root.mkdir()
    (root / "README.md").write_text(
        """\
---
id: DOC-001
revision: 1
---
# Index
[Target](target.md)
[Review](review.md)
""",
        encoding="utf-8",
    )
    (root / "target.md").write_text(
        """\
---
id: DOC-002
revision: 2
depends_on: [README.md]
related: [review.md]
derived_from: [https://example.com/source, asset.png]
validated_against: [DOC-001@1]
---
# Target
## Summary
Short target summary.
## Contents
- [Details](#details)
## Details
Detailed target content.
""",
        encoding="utf-8",
    )
    (root / "review.md").write_text(
        """\
---
id: DOC-003
revision: 1
type: review
validated_against: [DOC-002@1]
---
# Review
Review navigation.
## Findings
Finding details.
""",
        encoding="utf-8",
    )
    return root


def test_legacy_relations_resolve_with_visible_boundaries(
    tmp_path: Path, capsys
) -> None:
    vertical_project(tmp_path)
    catalog = build_catalog(load_config(tmp_path))
    graph = build_dependency_graph(catalog)

    assert [
        (edge.relation, edge.source_id, edge.target_id)
        for edge in graph.outgoing("DOC-002")
    ] == [
        ("depends_on", "DOC-002", "DOC-001"),
        ("related", "DOC-002", "DOC-003"),
        ("validated_against", "DOC-002", "DOC-001"),
    ]
    assert len(catalog.relation_boundaries) == 2

    assert migration_report(tmp_path) == 0
    report = capsys.readouterr().out
    assert "resolved\tDOC-002\tdepends_on\tREADME.md\tDOC-001" in report
    assert "boundary\tDOC-002\tderived_from\thttps://example.com/source" in report


def test_legacy_mode_does_not_reclassify_wrong_prefix_ids_as_paths(
    tmp_path: Path,
) -> None:
    root = vertical_project(tmp_path)
    target = root / "target.md"
    target.write_text(
        target.read_text(encoding="utf-8").replace(
            "depends_on: [README.md]",
            "depends_on: [README.md, OTHER-001]",
        ),
        encoding="utf-8",
    )

    catalog = build_catalog(load_config(tmp_path))
    document = next(
        item
        for item in catalog.documents
        if item.metadata and item.metadata.document_id == "DOC-002"
    )
    assert (
        "metadata.depends_on entry 'OTHER-001' must use a configured stable ID"
        in document.metadata_issues
    )
    assert not any(
        item.value == "OTHER-001" for item in catalog.relation_boundaries
    )


def test_duplicate_legacy_path_is_a_graph_error(tmp_path: Path) -> None:
    root = vertical_project(tmp_path)
    target = root / "target.md"
    target.write_text(
        target.read_text(encoding="utf-8").replace(
            "depends_on: [README.md]",
            "depends_on: [README.md, README.md]",
        ),
        encoding="utf-8",
    )

    catalog = build_catalog(load_config(tmp_path))
    document = next(
        item
        for item in catalog.documents
        if item.metadata and item.metadata.document_id == "DOC-002"
    )
    assert (
        "metadata.depends_on contains duplicate reference README.md"
        in document.graph_issues
    )


def test_legacy_adoption_diagnostics_are_concise_or_verbose(
    tmp_path: Path, capsys
) -> None:
    vertical_project(tmp_path)

    assert validate(tmp_path) == 0
    concise = capsys.readouterr().err
    assert "2 legacy relation values resolve to stable IDs" in concise
    assert "2 legacy relation values remain resource/outside boundaries" in concise
    assert "DOC-002@1 is stale" in concise
    assert "legacy metadata.depends_on value 'README.md'" not in concise

    assert doctor(tmp_path) == 0
    doctor_concise = capsys.readouterr().err
    assert "2 legacy relation values resolve to stable IDs" in doctor_concise
    assert "DOC-002@1 is stale" in doctor_concise

    assert validate(tmp_path, verbose_adoption=True) == 0
    verbose = capsys.readouterr().err
    assert (
        "legacy metadata.depends_on value 'README.md' resolves to DOC-001"
        in verbose
    )
    assert (
        "legacy metadata.derived_from value 'asset.png': resource/outside catalog"
        in verbose
    )
    assert "legacy relation values resolve to stable IDs;" not in verbose

    assert doctor(tmp_path, verbose_adoption=True) == 0
    doctor_verbose = capsys.readouterr().err
    assert (
        "legacy metadata.related value 'review.md' resolves to DOC-003"
        in doctor_verbose
    )

    validate_args = build_parser().parse_args(
        ["validate", str(tmp_path), "--verbose-adoption"]
    )
    doctor_args = build_parser().parse_args(
        ["doctor", str(tmp_path), "--verbose-adoption"]
    )
    assert validate_args.verbose_adoption is True
    assert doctor_args.verbose_adoption is True


def test_concise_adoption_output_preserves_errors(
    tmp_path: Path, capsys
) -> None:
    root = vertical_project(tmp_path)
    target = root / "target.md"
    target.write_text(
        target.read_text(encoding="utf-8").replace(
            "depends_on: [README.md]",
            "depends_on: [README.md, DOC-999]",
        ),
        encoding="utf-8",
    )

    assert validate(tmp_path) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "references unknown ID DOC-999" in captured.err
    assert "2 legacy relation values resolve to stable IDs" in captured.err


def test_context_and_impact_expose_coverage_boundaries_and_snapshots(
    tmp_path: Path, capsys
) -> None:
    vertical_project(tmp_path)

    assert context(tmp_path, "DOC-002", depth=1) == 0
    packet = capsys.readouterr()
    assert "# Context packet: DOC-002" in packet.out
    assert "DOC-001" in packet.out
    assert "Omitted H2: details" in packet.out
    assert "unresolved/resource derived_from asset.png" in packet.out
    assert "Related omitted: review.md" in packet.out

    assert impact(tmp_path, "DOC-002") == 0
    report = capsys.readouterr().out
    assert "| `DOC-003` | validated_against | 1 | historical snapshot |" in report
    args = build_parser().parse_args(
        [
            "context",
            "DOC-002",
            str(tmp_path),
            "--depth",
            "2",
            "--include-related",
            "--include",
            "DOC-003#findings",
        ]
    )
    assert args.document_id == "DOC-002"
    assert args.depth == 2
    assert args.include_related is True
    assert args.include == ["DOC-003#findings"]


def test_projection_preserves_output_and_detects_stale_changes(
    tmp_path: Path, capsys
) -> None:
    root = vertical_project(tmp_path)

    assert context(tmp_path, "DOC-002", depth=1) == 0
    direct = capsys.readouterr().out
    assert index_projection(tmp_path, write=True) == 0
    capsys.readouterr()
    assert index_projection(tmp_path) == 0
    capsys.readouterr()
    assert context(tmp_path, "DOC-002", depth=1) == 0
    projected = capsys.readouterr().out
    assert projected == direct

    target = root / "target.md"
    target.write_text(
        target.read_text(encoding="utf-8").replace(
            "Detailed target content.", "Changed target content."
        ),
        encoding="utf-8",
    )
    assert changes(tmp_path) == 0
    output = capsys.readouterr().out
    assert "changed\tDOC-002" in output
    assert "section\tDOC-002#details" in output
    assert context(tmp_path, "DOC-002", depth=1) == 0
    captured = capsys.readouterr()
    assert "projection stale; using direct Markdown" in captured.err


def test_projection_generation_is_immutable_and_corruption_falls_back(
    tmp_path: Path, capsys
) -> None:
    vertical_project(tmp_path)
    config = load_config(tmp_path)
    projection = build_projection(build_catalog(config))
    generation = write_projection(config, projection)
    manifest = (
        tmp_path
        / ".docsystem"
        / "cache"
        / "generations"
        / generation
        / "manifest.json"
    )
    original = manifest.read_bytes()

    assert write_projection(config, projection) == generation
    assert manifest.read_bytes() == original

    document_shard = next(manifest.parent.glob("documents/**/*.json"))
    document_shard.write_text("{}", encoding="utf-8")
    assert context(tmp_path, "DOC-002", depth=1) == 0
    captured = capsys.readouterr()
    assert "projection document shard invalid" in captured.err


def test_context_anchor_error_has_no_partial_stdout(
    tmp_path: Path, capsys
) -> None:
    vertical_project(tmp_path)

    assert context(tmp_path, "DOC-002", anchor="missing") == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "anchor not found in DOC-002: missing" in captured.err


def test_projection_retains_current_generation_with_configured_limit(
    tmp_path: Path,
) -> None:
    root = vertical_project(tmp_path)
    config = load_config(tmp_path)
    target = root / "target.md"

    generations: list[str] = []
    for revision in range(3):
        target.write_text(
            target.read_text(encoding="utf-8")
            + f"\nGeneration marker {revision}.\n",
            encoding="utf-8",
        )
        projection = build_projection(build_catalog(config))
        generations.append(write_projection(config, projection))

    generation_root = tmp_path / ".docsystem" / "cache" / "generations"
    retained = {path.name for path in generation_root.iterdir()}
    assert len(retained) == 2
    assert generations[-1] in retained
