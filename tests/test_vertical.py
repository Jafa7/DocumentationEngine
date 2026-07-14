import hashlib
import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path, PurePosixPath

from docsystem.catalog import build_catalog, build_dependency_graph
from docsystem.cli import (
    build_parser,
    changes,
    context,
    context_gap_draft,
    doctor,
    finish,
    impact,
    index_projection,
    migration_report,
    read_document,
    report_draft,
    validate,
)
from docsystem.config import CONFIG_FILENAME, DEFAULT_CONFIG, load_config
from docsystem.projection import build_projection, config_fingerprint, write_projection


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


def test_context_packet_stats_report_counts_and_body_size(
    tmp_path: Path, capsys
) -> None:
    vertical_project(tmp_path)

    assert context(tmp_path, "DOC-002", depth=1, includes=["DOC-003#findings"]) == 0
    output = capsys.readouterr().out
    body, separator, stats = output.partition("\n## Packet stats\n")
    assert separator, "packet must end with a Packet stats section"
    assert body.endswith("- Expand with --depth, --include-related, or --include ID#anchor.\n")
    assert "- Included documents: 3" in stats
    assert "- Explicit sections: 1" in stats
    assert "- Omitted H2 sections: 1" in stats
    assert (
        f"- Body size: {body.count(chr(10))} lines, "
        f"{len(body.encode('utf-8'))} UTF-8 bytes" in stats
    )


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


def test_config_fingerprint_covers_projection_relevant_fields(
    tmp_path: Path,
) -> None:
    vertical_project(tmp_path)
    base = load_config(tmp_path)
    baseline = config_fingerprint(base)

    # Deterministic: the same configuration always fingerprints identically.
    assert config_fingerprint(load_config(tmp_path)) == baseline

    # Every field that affects catalog membership, metadata parsing, section or
    # navigation policy, dependency-graph semantics, or projection layout must
    # move the fingerprint.
    variants = {
        "documentation_root": replace(
            base, documentation_root=base.project_root / "other"
        ),
        "areas": replace(
            base, areas={**base.areas, "extra": PurePosixPath("extra")}
        ),
        "identifiers": replace(
            base, identifiers={**base.identifiers, "note": "NOTE"}
        ),
        "catalog_exclusions": replace(base, catalog_exclusions=("drafts/**.md",)),
        "navigation_extend_through": replace(
            base, navigation_extend_through=("summary",)
        ),
        "legacy_relation_mode": replace(base, legacy_relation_mode="strict"),
        "snapshot_document_types": replace(
            base, snapshot_document_types=("review",)
        ),
        "projection_format": replace(base, projection_format="other-json"),
    }
    for field, variant in variants.items():
        assert config_fingerprint(variant) != baseline, field

    # keep_generations is a retention knob only; it must not invalidate an
    # otherwise identical projection.
    assert (
        config_fingerprint(replace(base, keep_generations=base.keep_generations + 1))
        == baseline
    )


def test_config_semantics_invalidate_projection_for_legacy_paths(
    tmp_path: Path, capsys
) -> None:
    vertical_project(tmp_path)
    # Build a projection while legacy path relations resolve with a warning.
    assert index_projection(tmp_path, write=True) == 0
    capsys.readouterr()

    # Tighten relations.legacy_paths to strict without touching any Markdown.
    config_path = tmp_path / CONFIG_FILENAME
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace(
            'legacy_paths = "resolve-with-warning"', 'legacy_paths = "strict"'
        ),
        encoding="utf-8",
    )

    # The stale projection must not be served: the config fingerprint no longer
    # matches, so the read falls back to direct Markdown and fails closed on the
    # now-invalid legacy path relations instead of emitting resolved edges.
    assert context(tmp_path, "DOC-002", depth=1) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert (
        "projection stale: configuration changed; using direct Markdown"
        in captured.err
    )
    assert "must use a configured stable ID" in captured.err


def test_config_semantics_invalidate_projection_for_navigation(
    tmp_path: Path, capsys
) -> None:
    vertical_project(tmp_path)
    # Build a projection under the initial navigation policy.
    assert index_projection(tmp_path, write=True) == 0
    capsys.readouterr()

    # Extend navigation through an anchor that resolves to the document H1,
    # which the navigation policy rejects, without changing any Markdown.
    config_path = tmp_path / CONFIG_FILENAME
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace(
            'extend_through = ["summary", "contents"]',
            'extend_through = ["summary", "contents", "target"]',
        ),
        encoding="utf-8",
    )

    assert read_document(tmp_path, "DOC-002", navigation=True) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert (
        "projection stale: configuration changed; using direct Markdown"
        in captured.err
    )
    assert (
        "navigation.extend_through anchor 'target' resolves to H1" in captured.err
    )


def test_semantic_shard_tampering_falls_back_and_preserves_dependency(
    tmp_path: Path, capsys
) -> None:
    vertical_project(tmp_path)
    config = load_config(tmp_path)
    generation = write_projection(config, build_projection(build_catalog(config), config))
    generation_dir = (
        tmp_path / ".docsystem" / "cache" / "generations" / generation
    )

    # Drop DOC-002's dependencies from the shard while leaving the shard
    # identity and the Markdown source hashes untouched.
    shard_path = next(generation_dir.glob("documents/**/DOC-002.json"))
    shard = json.loads(shard_path.read_text(encoding="utf-8"))
    assert shard["dependencies"], "DOC-002 must have a dependency to drop"
    shard["dependencies"] = []
    shard_path.write_text(
        json.dumps(shard, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    # The reconstructed generation hash no longer matches, so the read falls
    # back to direct Markdown, warns, and still reports the dropped dependency.
    assert context(tmp_path, "DOC-002", depth=1) == 0
    captured = capsys.readouterr()
    assert "projection document shard invalid: DOC-002; using direct Markdown" in captured.err
    assert "## DOC-001 — README.md" in captured.out
    assert "DOC-002: depends_on README.md -> DOC-001" in captured.out


def test_manifest_source_hash_tampering_falls_back_to_markdown(
    tmp_path: Path, capsys
) -> None:
    root = vertical_project(tmp_path)
    config = load_config(tmp_path)
    generation = write_projection(config, build_projection(build_catalog(config), config))
    manifest_path = (
        tmp_path
        / ".docsystem"
        / "cache"
        / "generations"
        / generation
        / "manifest.json"
    )

    # Edit a source and rewrite only the manifest hash to match, leaving the
    # shard (and its generation-bound source hash) untouched. The manifest is
    # generated data, so its freshness fields must not be trusted on their own:
    # the loader binds them to the verified shard and falls back instead of
    # serving stale shard structure against edited Markdown.
    target = root / "target.md"
    edited = target.read_text(encoding="utf-8").replace(
        "Detailed target content.", "Tampered target content."
    )
    target.write_text(edited, encoding="utf-8")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["documents"]["DOC-002"]["source_sha256"] = hashlib.sha256(
        edited.encode()
    ).hexdigest()
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    assert context(tmp_path, "DOC-002", depth=1) == 0
    captured = capsys.readouterr()
    assert "projection corrupt; using direct Markdown" in captured.err
    assert "## DOC-001 — README.md" in captured.out


def test_changes_does_not_trust_a_manifest_outside_its_generation_root(
    tmp_path: Path, capsys
) -> None:
    vertical_project(tmp_path)
    config = load_config(tmp_path)
    generation = write_projection(config, build_projection(build_catalog(config), config))
    manifest_path = (
        tmp_path
        / ".docsystem"
        / "cache"
        / "generations"
        / generation
        / "manifest.json"
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["documents"]["DOC-002"]["source_sha256"] = "0" * 64
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    assert changes(tmp_path, json_output=True) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "unavailable"
    assert payload["changes"] == []


def test_context_json_is_deterministic_and_machine_readable(
    tmp_path: Path, capsys
) -> None:
    vertical_project(tmp_path)

    assert (
        context(
            tmp_path,
            "DOC-002",
            depth=1,
            includes=["DOC-003#findings"],
            json_output=True,
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema_version"] == 1
    assert payload["target"] == "DOC-002"
    assert payload["depth"] == 1
    assert payload["include_related"] is False
    assert [item["id"] for item in payload["documents"]] == [
        "DOC-002",
        "DOC-001",
        "DOC-003",
    ]
    target_document = payload["documents"][0]
    assert target_document["relations"] == ["target"]
    assert target_document["omitted_h2"] == ["details"]
    assert "# Target" in target_document["navigation"]
    # Additive typed fields leave the existing navigation value unchanged.
    assert target_document["revision"] == 2
    assert target_document["navigation"] == (
        "---\nid: DOC-002\nrevision: 2\ndepends_on: [README.md]\n"
        "related: [review.md]\nderived_from: "
        "[https://example.com/source, asset.png]\n"
        "validated_against: [DOC-001@1]\n---\n"
        "# Target\n## Summary\nShort target summary.\n"
        "## Contents\n- [Details](#details)"
    )
    assert target_document["sections"] == [
        {"anchor": "target", "title": "Target", "level": 1, "lines": 7, "bytes": 111},
        {"anchor": "summary", "title": "Summary", "level": 2, "lines": 2, "bytes": 32},
        {"anchor": "contents", "title": "Contents", "level": 2, "lines": 2, "bytes": 33},
        {"anchor": "details", "title": "Details", "level": 2, "lines": 2, "bytes": 35},
    ]
    assert payload["outline"] is False
    review = payload["documents"][2]
    assert review["explicit_sections"] == [
        {"anchor": "findings", "content": "## Findings\nFinding details."}
    ]
    assert payload["freshness"] == [
        {
            "source_id": "DOC-003",
            "target_id": "DOC-002",
            "pinned_revision": 1,
            "current_revision": 2,
            "classification": "historical snapshot",
        }
    ]
    assert {item["value"] for item in payload["migrations"]} == {
        "README.md",
        "review.md",
    }
    assert {item["value"] for item in payload["boundaries"]} == {
        "asset.png",
        "https://example.com/source",
    }
    assert payload["related_omitted"] == ["review.md"]
    assert payload["stats"] == {
        "included_documents": 3,
        "explicit_sections": 1,
        "omitted_h2_sections": 1,
    }

    assert index_projection(tmp_path, write=True) == 0
    capsys.readouterr()
    assert (
        context(
            tmp_path,
            "DOC-002",
            depth=1,
            includes=["DOC-003#findings"],
            json_output=True,
        )
        == 0
    )
    assert json.loads(capsys.readouterr().out) == payload

    args = build_parser().parse_args(["context", "DOC-002", str(tmp_path), "--json"])
    assert args.json_output is True


def test_projection_serves_reads_identically_without_rebuilding_catalog(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    vertical_project(tmp_path)

    def run_all() -> dict[str, str]:
        outputs: dict[str, str] = {}
        assert read_document(tmp_path, "DOC-002") == 0
        outputs["read"] = capsys.readouterr().out
        assert read_document(tmp_path, "DOC-002", list_sections=True) == 0
        outputs["list"] = capsys.readouterr().out
        assert read_document(tmp_path, "DOC-002", navigation=True) == 0
        outputs["navigation"] = capsys.readouterr().out
        assert read_document(tmp_path, "DOC-002", anchor="details") == 0
        outputs["anchor"] = capsys.readouterr().out
        assert impact(tmp_path, "DOC-002") == 0
        outputs["impact"] = capsys.readouterr().out
        assert (
            context(tmp_path, "DOC-002", depth=1, includes=["DOC-003#findings"]) == 0
        )
        outputs["context"] = capsys.readouterr().out
        assert (
            context(
                tmp_path,
                "DOC-002",
                depth=1,
                includes=["DOC-003#findings"],
                json_output=True,
            )
            == 0
        )
        outputs["context_json"] = capsys.readouterr().out
        assert context(tmp_path, "DOC-002", depth=1, outline=True) == 0
        outputs["context_outline"] = capsys.readouterr().out
        assert (
            context(tmp_path, "DOC-002", depth=1, outline=True, json_output=True) == 0
        )
        outputs["context_outline_json"] = capsys.readouterr().out
        assert (
            context(tmp_path, "DOC-002", depth=1, assume_known=["DOC-002@2"]) == 0
        )
        outputs["context_assume"] = capsys.readouterr().out
        assert (
            context(
                tmp_path,
                "DOC-002",
                depth=1,
                assume_known=["DOC-002@2"],
                json_output=True,
            )
            == 0
        )
        outputs["context_assume_json"] = capsys.readouterr().out
        return outputs

    direct = run_all()
    assert index_projection(tmp_path, write=True) == 0
    capsys.readouterr()

    def explode(config: object) -> None:
        raise AssertionError("projection-served reads must not rebuild the catalog")

    monkeypatch.setattr("docsystem.cli.build_catalog", explode)
    projected = run_all()
    assert projected == direct

    # The "sections" size map must not be sensitive to which path served it:
    # its byte counts come from the shared view shape, not from re-parsing.
    payload = json.loads(direct["context_json"])
    assert payload["documents"][0]["sections"], "sections must not be empty"
    assert payload["outline"] is False
    outline_payload = json.loads(direct["context_outline_json"])
    assert outline_payload["outline"] is True
    assert "navigation" not in outline_payload["documents"][0]

    # The declared-cache packet is identical from both paths and really omits
    # the current-revision target's content.
    assume_payload = json.loads(direct["context_assume_json"])
    assert assume_payload["documents"][0]["content_omitted"] == {
        "reason": "assumed-known",
        "declared_revision": 2,
    }
    assert "navigation" not in assume_payload["documents"][0]
    assert assume_payload["stats"]["assumed_known_omitted"] == 1


def test_since_delta_packet_serves_identically_from_both_paths(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    root = vertical_project(tmp_path)
    assert index_projection(tmp_path, write=True) == 0
    capsys.readouterr()
    pointer = tmp_path / ".docsystem" / "cache" / "current.json"
    generation = json.loads(pointer.read_text(encoding="utf-8"))["generation"]
    short = generation[:12]

    # Edit exactly one section of the target document.
    target = root / "target.md"
    target.write_text(
        target.read_text(encoding="utf-8").replace(
            "Detailed target content.", "Edited target content."
        ),
        encoding="utf-8",
    )

    # Direct path: the current generation is now stale, so the packet is served
    # from direct Markdown while --since compares against the retained manifest.
    assert context(tmp_path, "DOC-002", depth=1, since=generation) == 0
    direct_text = capsys.readouterr().out
    assert (
        context(tmp_path, "DOC-002", depth=1, since=generation, json_output=True) == 0
    )
    direct_json = capsys.readouterr().out

    # Rebuild so a fresh CURRENT generation serves the fast path while --since
    # still points at the OLDER retained generation.
    assert index_projection(tmp_path, write=True) == 0
    capsys.readouterr()

    def explode(config: object) -> None:
        raise AssertionError("projection-served reads must not rebuild the catalog")

    monkeypatch.setattr("docsystem.cli.build_catalog", explode)
    assert context(tmp_path, "DOC-002", depth=1, since=generation) == 0
    projected_text = capsys.readouterr().out
    assert (
        context(tmp_path, "DOC-002", depth=1, since=generation, json_output=True) == 0
    )
    projected_json = capsys.readouterr().out

    assert projected_text == direct_text
    assert projected_json == direct_json

    # The delta really contains only the changed section, and the untouched
    # dependency is omitted with the unchanged-since coverage marker.
    assert "### Changed section `details`" in projected_text
    assert "Edited target content." in projected_text
    assert (
        f"_Coverage: content omitted — unchanged since {short}." in projected_text
    )
    assert f"- Delta vs generation {short}: 1 changed, 1 unchanged omitted" in projected_text

    payload = json.loads(projected_json)
    changed = next(item for item in payload["documents"] if item["id"] == "DOC-002")
    # "target" (the H1) is the complete truth signal: its slice spans the
    # whole document, so the edit inside "details" changes its hash too, even
    # though only "details" is served as a content block.
    assert changed["changed_sections"] == ["target", "details"]
    assert {section["anchor"] for section in changed["explicit_sections"]} == {"details"}
    unchanged = next(item for item in payload["documents"] if item["id"] == "DOC-001")
    assert unchanged["content_omitted"] == {
        "reason": "unchanged-since",
        "generation": short,
    }


def test_since_delta_change_inside_extend_through_is_not_duplicated(
    tmp_path: Path, capsys
) -> None:
    root = vertical_project(tmp_path)
    assert index_projection(tmp_path, write=True) == 0
    capsys.readouterr()
    pointer = tmp_path / ".docsystem" / "cache" / "current.json"
    generation = json.loads(pointer.read_text(encoding="utf-8"))["generation"]

    # "summary" is inside navigation.extend_through, so it is always served
    # by the navigation prefix, not as its own content block.
    target = root / "target.md"
    target.write_text(
        target.read_text(encoding="utf-8").replace(
            "Short target summary.", "Updated target summary."
        ),
        encoding="utf-8",
    )
    assert index_projection(tmp_path, write=True) == 0
    capsys.readouterr()

    assert context(tmp_path, "DOC-002", depth=1, since=generation) == 0
    text = capsys.readouterr().out
    assert "Updated target summary." in text
    assert text.count("Updated target summary.") == 1
    assert "### Changed section `summary`" not in text
    assert "### Changed section `target`" not in text

    assert (
        context(tmp_path, "DOC-002", depth=1, since=generation, json_output=True)
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    changed = next(item for item in payload["documents"] if item["id"] == "DOC-002")
    # Both the H1 and the extend_through H2 signal via changed_sections, but
    # neither is duplicated into explicit_sections since navigation already
    # carries them.
    assert changed["changed_sections"] == ["target", "summary"]
    assert changed["explicit_sections"] == []


def test_changes_json_is_deterministic_and_machine_readable(
    tmp_path: Path, capsys
) -> None:
    root = vertical_project(tmp_path)

    assert changes(tmp_path, json_output=True) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == {"schema_version": 1, "status": "absent", "changes": []}

    assert index_projection(tmp_path, write=True) == 0
    capsys.readouterr()

    target = root / "target.md"
    target.write_text(
        target.read_text(encoding="utf-8").replace(
            "Detailed target content.", "Changed target content."
        ),
        encoding="utf-8",
    )
    assert changes(tmp_path, json_output=True) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "schema_version": 1,
        "status": "compared",
        "changes": [
            {
                "document_id": "DOC-002",
                "kind": "changed",
                "sections": ["details", "target"],
            }
        ],
    }

    args = build_parser().parse_args(["changes", str(tmp_path), "--json"])
    assert args.json_output is True


def test_report_draft_is_privacy_safe_and_read_only(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    vertical_project(project)
    monkeypatch.chdir(tmp_path)

    assert (
        report_draft(
            Path("project"),
            project_name="Example Project",
            report_type="adoption-finding",
            source="codex",
            component="projection",
        )
        == 0
    )
    output = capsys.readouterr().out
    assert "# Adoption Finding: Example Project" in output
    assert "`adoption-finding`, `triage`, `project:example-project`" in output
    assert "`source:codex`, `component:projection`" in output
    assert "ready=True" in output
    assert "resolvable_migrations=2" in output
    assert "boundaries=2" in output
    assert "freshness_classification=0 stale, 1 historical snapshot" in output
    assert "next_command=docsystem migrate project" in output
    assert tmp_path.resolve().as_posix() not in output
    assert "Private document bodies are omitted or sanitized" in output
    assert "Runtime or local-state changes made" in output
    assert "none; report draft is read-only" in output


def test_report_draft_writes_output_and_handles_config_errors(
    tmp_path: Path, capsys, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    output_path = Path("report.md")

    assert (
        report_draft(
            Path("missing-project"),
            project_name="Broken Project",
            report_type="runtime-report",
            source="claude",
            output=output_path,
        )
        == 0
    )
    captured = capsys.readouterr()
    assert "Report draft written" in captured.out
    report = output_path.read_text(encoding="utf-8")
    assert "# Runtime Report: Broken Project" in report
    assert (
        "configuration_error=configuration not found: "
        "missing-project/.docsystem.toml"
    ) in report
    assert tmp_path.resolve().as_posix() not in report
    assert "project:broken-project" in report


def test_context_gap_draft_records_only_body_free_material_evidence(
    tmp_path: Path, capsys
) -> None:
    vertical_project(tmp_path)
    config = load_config(tmp_path)
    generation = write_projection(config, build_projection(build_catalog(config), config))
    capsys.readouterr()

    assert (
        context_gap_draft(
            tmp_path,
            project_name="Example Project",
            report_type="adoption-finding",
            source="codex",
            reason="missing_dependency",
            initial=("DOC-002#summary",),
            expanded=("DOC-003#findings",),
            impacts=("result", "decision"),
        )
        == 0
    )
    output = capsys.readouterr().out
    assert "# Adoption Finding: Example Project" in output
    assert "`component:context`" in output
    assert "Classification: material unexpected context gap" in output
    assert "Reason code: `missing_dependency`" in output
    assert "Materially affected: `decision`, `result`" in output
    assert f"Projection generation: `{generation}`" in output
    assert "`DOC-002#summary (revision 2, lines 10-11)`" in output
    assert "`DOC-003#findings (revision 1, lines 9-10)`" in output
    assert "Document bodies included in this evidence: no" in output
    assert "Detailed target content." not in output
    assert "Finding details." not in output


def test_context_gap_draft_rejects_normal_expansion_and_ambiguous_addresses(
    tmp_path: Path, capsys
) -> None:
    vertical_project(tmp_path)

    assert (
        context_gap_draft(
            tmp_path,
            project_name="Example Project",
            report_type="adoption-finding",
            source="codex",
            reason="task_requires_full_review",
            initial=("DOC-002",),
            expanded=("DOC-003",),
            impacts=("verification",),
        )
        == 1
    )
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "normally local evidence" in captured.err

    assert (
        context_gap_draft(
            tmp_path,
            project_name="Example Project",
            report_type="core-bug",
            source="codex",
            reason="missing_section",
            initial=("DOC-002#summary",),
            expanded=("DOC-002#summary",),
            impacts=("result",),
        )
        == 1
    )
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "addresses cannot be both initial and expanded" in captured.err

    assert (
        context_gap_draft(
            tmp_path,
            project_name="Example Project",
            report_type="core-bug",
            source="codex",
            reason="missing_section",
            initial=("DOC-002#unknown",),
            expanded=("DOC-003",),
            impacts=("result",),
        )
        == 1
    )
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "section anchor not found: DOC-002#unknown" in captured.err

    assert (
        context_gap_draft(
            tmp_path,
            project_name="Example Project",
            report_type="core-bug",
            source="codex",
            reason="missing_dependency",
            initial=("DOC-002", "DOC-002"),
            expanded=("DOC-003",),
            impacts=("result",),
        )
        == 1
    )
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "duplicate initial address: DOC-002" in captured.err


def test_context_gap_parser_has_controlled_repeatable_evidence() -> None:
    args = build_parser().parse_args(
        [
            "report",
            "context-gap",
            ".",
            "--project-name",
            "Example",
            "--type",
            "core-bug",
            "--source",
            "claude",
            "--reason",
            "navigation_insufficient",
            "--initial",
            "DOC-001",
            "--expanded",
            "DOC-002#details",
            "--impact",
            "plan",
            "--impact",
            "verification",
        ]
    )
    assert args.report_command == "context-gap"
    assert args.initial == ["DOC-001"]
    assert args.expanded == ["DOC-002#details"]
    assert args.impact == ["plan", "verification"]


def test_finish_summarizes_return_context_and_snapshot_classification(
    tmp_path: Path, capsys
) -> None:
    vertical_project(tmp_path)

    assert finish(tmp_path, "DOC-002", depth=1) == 0
    output = capsys.readouterr().out
    assert "# Finish handoff: DOC-002" in output
    assert "`DOC-001` — `README.md`" in output
    assert "`DOC-003` — `review.md`" not in output
    assert "Related traversal: omitted" in output
    assert "No stale or historical snapshot pins" in output
    assert "DOC-002` depends_on `README.md` -> `DOC-001`" in output
    assert "unresolved/resource derived_from `asset.png`" in output

    assert finish(tmp_path, "DOC-002", depth=1, include_related=True) == 0
    related_output = capsys.readouterr().out
    assert "`DOC-003` — `review.md`" in related_output
    assert (
        "`DOC-003` pins `DOC-002@1`; current revision is 2 — historical snapshot"
        in related_output
    )


def test_finish_json_is_deterministic_and_parser_exposes_new_commands(
    tmp_path: Path, capsys
) -> None:
    vertical_project(tmp_path)

    assert finish(tmp_path, "DOC-002", depth=1, include_related=True, json_output=True) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema_version"] == 1
    assert payload["target"] == "DOC-002"
    assert [item["id"] for item in payload["included_documents"]] == [
        "DOC-002",
        "DOC-001",
        "DOC-003",
    ]
    assert payload["freshness"] == [
        {
            "source_id": "DOC-003",
            "target_id": "DOC-002",
            "pinned_revision": 1,
            "current_revision": 2,
            "classification": "historical snapshot",
        }
    ]

    finish_args = build_parser().parse_args(["finish", "DOC-002", str(tmp_path), "--json"])
    assert finish_args.command == "finish"
    assert finish_args.json_output is True

    report_args = build_parser().parse_args(
        [
            "report",
            "draft",
            str(tmp_path),
            "--project-name",
            "Example",
            "--type",
            "core-bug",
            "--source",
            "vscode",
            "--component",
            "cli",
        ]
    )
    assert report_args.command == "report"
    assert report_args.report_command == "draft"
    assert report_args.report_type == "core-bug"


def test_finish_preserves_context_expansion_and_report_state(
    tmp_path: Path, capsys
) -> None:
    vertical_project(tmp_path)

    assert (
        finish(
            tmp_path,
            "DOC-002",
            context_expansion="material-gap",
            context_gap_report="drafted",
        )
        == 0
    )
    output = capsys.readouterr().out
    assert "## Context expansion" in output
    assert "- Classification: material-gap" in output
    assert "- Report state: drafted" in output

    assert (
        finish(
            tmp_path,
            "DOC-002",
            json_output=True,
            context_expansion="normal",
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["context_expansion"] == {
        "classification": "normal",
        "report_state": "not-needed",
    }

    assert (
        finish(
            tmp_path,
            "DOC-002",
            context_expansion="material-gap",
            context_gap_report="not-needed",
        )
        == 1
    )
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "requires a drafted or filed report" in captured.err

    args = build_parser().parse_args(
        [
            "finish",
            "DOC-002",
            str(tmp_path),
            "--context-expansion",
            "material-gap",
            "--context-gap-report",
            "filed",
        ]
    )
    assert args.context_expansion == "material-gap"
    assert args.context_gap_report == "filed"


def test_projection_generation_is_immutable_and_corruption_falls_back(
    tmp_path: Path, capsys
) -> None:
    vertical_project(tmp_path)
    config = load_config(tmp_path)
    projection = build_projection(build_catalog(config), config)
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


def test_cli_context_command_matches_library_output_from_unrelated_cwd(
    tmp_path: Path, capsys
) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    vertical_project(project_root)

    assert context(project_root, "DOC-002", depth=1) == 0
    expected_stdout = capsys.readouterr().out

    unrelated_cwd = tmp_path / "unrelated-cwd"
    unrelated_cwd.mkdir()
    repo_src = Path(__file__).resolve().parents[1] / "src"
    env = dict(os.environ, PYTHONPATH=str(repo_src))

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "docsystem",
            "context",
            "DOC-002",
            str(project_root),
            "--depth",
            "1",
        ],
        cwd=unrelated_cwd,
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == expected_stdout
    assert list(unrelated_cwd.iterdir()) == []


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
        projection = build_projection(build_catalog(config), config)
        generations.append(write_projection(config, projection))

    generation_root = tmp_path / ".docsystem" / "cache" / "generations"
    retained = {path.name for path in generation_root.iterdir()}
    assert len(retained) == 2
    assert generations[-1] in retained


def test_projection_retention_ignores_leftover_staging_directories(
    tmp_path: Path,
) -> None:
    root = vertical_project(tmp_path)
    config = load_config(tmp_path)
    target = root / "target.md"

    generations: list[str] = []
    for revision in range(2):
        target.write_text(
            target.read_text(encoding="utf-8")
            + f"\nGeneration marker {revision}.\n",
            encoding="utf-8",
        )
        projection = build_projection(build_catalog(config), config)
        generations.append(write_projection(config, projection))

    generation_root = tmp_path / ".docsystem" / "cache" / "generations"
    staging = generation_root / ".staging-leftover"
    staging.mkdir()

    target.write_text(
        target.read_text(encoding="utf-8") + "\nGeneration marker 2.\n",
        encoding="utf-8",
    )
    projection = build_projection(build_catalog(config), config)
    generations.append(write_projection(config, projection))

    retained_real = {
        path.name
        for path in generation_root.iterdir()
        if path.is_dir() and not path.name.startswith(".staging-")
    }
    assert retained_real == {generations[-2], generations[-1]}
    assert staging.is_dir()
