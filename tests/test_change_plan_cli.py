import json
from pathlib import Path

from docsystem.catalog import build_catalog
from docsystem.cli import change_plan, index_projection, initialize
from docsystem.config import load_config
from docsystem.projection import build_projection, cache_root, write_projection


def _write(project_root: Path, relative: str, text: str) -> None:
    path = project_root / "plan" / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def bootstrap_project(tmp_path: Path) -> Path:
    """An initialized project exercising authored/observed/generated edges.

    `DOC-001` depends on `DOC-002` (authored, read) and marks `DOC-004`
    `related` (authored, but never promoted to read). Its `intro` section
    observes a link into `DOC-002#setup` and one broken anchor. `DOC-101`
    observes `DOC-002` from its index, giving `DOC-002` a reverse impact item.
    """

    assert initialize(tmp_path) == 0
    _write(
        tmp_path,
        "architecture/README.md",
        "---\nid: DOC-100\nrevision: 1\n---\n\n# Architecture\n\n[Doc A](a.md)\n",
    )
    _write(
        tmp_path,
        "architecture/a.md",
        "---\nid: DOC-001\nrevision: 1\ndepends_on: [DOC-002]\nrelated: [DOC-004]\n---\n\n"
        "# Doc A\n\n<a id=\"intro\"></a>\n## Introduction\n\n"
        "See [setup](../roadmap/b.md#setup) and [broken](../roadmap/b.md#missing).\n",
    )
    _write(
        tmp_path,
        "roadmap/README.md",
        "---\nid: DOC-101\nrevision: 1\n---\n\n# Roadmap\n\n[Doc B](b.md)\n[Doc D](d.md)\n",
    )
    _write(
        tmp_path,
        "roadmap/b.md",
        "---\nid: DOC-002\nrevision: 1\n---\n\n# Doc B\n\n"
        "<a id=\"setup\"></a>\n## Setup\n\nBody.\n",
    )
    _write(
        tmp_path,
        "roadmap/d.md",
        "---\nid: DOC-004\nrevision: 1\n---\n\n# Doc D\n\nBody.\n",
    )
    return tmp_path


def test_document_and_section_target_are_read_at_distance_zero(
    tmp_path: Path, capsys
) -> None:
    bootstrap_project(tmp_path)
    capsys.readouterr()

    assert change_plan(tmp_path, "DOC-001") == 0
    payload_rows = capsys.readouterr().out.splitlines()
    target_rows = [row.split("\t") for row in payload_rows if row.startswith("item\tDOC-001\t")]
    assert len(target_rows) == 1
    _kind, _address, disposition, scope, *_rest, distance, cls, _path, _reason = target_rows[0]
    assert disposition == "read"
    assert scope == "target"
    assert distance == "0"
    assert cls == "target"

    assert change_plan(tmp_path, "DOC-001#intro") == 0
    out = capsys.readouterr().out
    assert out.splitlines()[0].split("\t")[:3] == ["item", "DOC-001#intro", "read"]


def test_authored_semantic_dependency_is_read(tmp_path: Path, capsys) -> None:
    bootstrap_project(tmp_path)
    capsys.readouterr()

    assert change_plan(tmp_path, "DOC-001", json_output=True) == 0
    payload = json.loads(capsys.readouterr().out)
    doc_002 = next(item for item in payload["items"] if item["address"] == "DOC-002")
    assert doc_002["disposition"] == "read"
    assert doc_002["reasons"][0]["relation"] == "depends_on"
    assert doc_002["reasons"][0]["authority"] == "authored"
    assert doc_002["reasons"][0]["scope"] == "forward"


def test_related_is_never_promoted_to_mandatory_read(tmp_path: Path, capsys) -> None:
    bootstrap_project(tmp_path)
    capsys.readouterr()

    assert change_plan(tmp_path, "DOC-001", json_output=True) == 0
    payload = json.loads(capsys.readouterr().out)
    doc_004 = next(item for item in payload["items"] if item["address"] == "DOC-004")
    assert doc_004["disposition"] == "review"
    assert doc_004["reasons"][0]["relation"] == "related"


def test_observed_forward_reference_is_review(tmp_path: Path, capsys) -> None:
    bootstrap_project(tmp_path)
    capsys.readouterr()

    assert change_plan(tmp_path, "DOC-001#intro", json_output=True) == 0
    payload = json.loads(capsys.readouterr().out)
    setup = next(item for item in payload["items"] if item["address"] == "DOC-002#setup")
    assert setup["disposition"] == "review"
    assert setup["reasons"][0]["authority"] == "observed"


def test_reverse_adds_incoming_impact_as_review_not_read(tmp_path: Path, capsys) -> None:
    bootstrap_project(tmp_path)
    capsys.readouterr()

    assert change_plan(tmp_path, "DOC-002", reverse=True, json_output=True) == 0
    payload = json.loads(capsys.readouterr().out)
    doc_001 = next(item for item in payload["items"] if item["address"] == "DOC-001")
    assert doc_001["disposition"] == "review"
    assert doc_001["reasons"][0]["scope"] == "reverse"
    assert doc_001["reasons"][0]["relation"] == "depends_on"
    # Reverse observed impact (DOC-101's index links to Doc B) is included too.
    assert {item["address"] for item in payload["items"]} >= {
        "DOC-002",
        "DOC-001",
        "DOC-101#roadmap",
    }


def test_generated_containment_does_not_enumerate_every_section(
    tmp_path: Path, capsys
) -> None:
    bootstrap_project(tmp_path)
    capsys.readouterr()

    assert change_plan(tmp_path, "DOC-001", json_output=True) == 0
    payload = json.loads(capsys.readouterr().out)
    addresses = {item["address"] for item in payload["items"]}
    # DOC-001#intro is a real section reachable only through generated
    # containment; it must not appear as a plan item for a bare-document query.
    assert "DOC-001#intro" not in addresses


def test_transitive_document_plan_uses_containment_to_find_section_references(
    tmp_path: Path, capsys
) -> None:
    bootstrap_project(tmp_path)
    capsys.readouterr()

    assert change_plan(tmp_path, "DOC-001", transitive=True, json_output=True) == 0
    payload = json.loads(capsys.readouterr().out)
    addresses = {item["address"] for item in payload["items"]}
    assert "DOC-001#intro" not in addresses
    setup = next(item for item in payload["items"] if item["address"] == "DOC-002#setup")
    reason = setup["reasons"][0]
    assert setup["disposition"] == "review"
    assert reason["path"] == ["DOC-001", "DOC-001#intro", "DOC-002#setup"]


def test_unrelated_duplicate_id_blocks_authored_completeness(
    tmp_path: Path, capsys
) -> None:
    bootstrap_project(tmp_path)
    _write(
        tmp_path,
        "architecture/duplicate.md",
        "---\nid: DOC-004\nrevision: 1\n---\n\n# Duplicate\n",
    )
    capsys.readouterr()

    assert change_plan(tmp_path, "DOC-001") == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "duplicate document ID DOC-004" in captured.err


def test_multiple_inclusion_reasons_aggregate_without_loss(tmp_path: Path, capsys) -> None:
    bootstrap_project(tmp_path)
    path = tmp_path / "plan" / "architecture" / "a.md"
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            "# Doc A", "See [Doc B](../roadmap/b.md) before any heading.\n\n# Doc A"
        ),
        encoding="utf-8",
    )
    capsys.readouterr()

    assert change_plan(tmp_path, "DOC-001", json_output=True) == 0
    payload = json.loads(capsys.readouterr().out)
    doc_002 = next(item for item in payload["items"] if item["address"] == "DOC-002")
    signatures = {(reason["relation"], reason["authority"]) for reason in doc_002["reasons"]}
    assert signatures == {("depends_on", "authored"), ("references", "observed")}
    assert doc_002["disposition"] == "read"


def test_direct_and_transitive_proving_paths(tmp_path: Path, capsys) -> None:
    bootstrap_project(tmp_path)
    # DOC-005 is reachable only transitively, through DOC-002 -- unlike
    # DOC-004, which the fixture already reaches directly via `related`.
    _write(
        tmp_path,
        "roadmap/b.md",
        "---\nid: DOC-002\nrevision: 1\ndepends_on: [DOC-005]\n---\n\n"
        "# Doc B\n\n<a id=\"setup\"></a>\n## Setup\n\nBody.\n",
    )
    _write(
        tmp_path,
        "roadmap/e.md",
        "---\nid: DOC-005\nrevision: 1\n---\n\n# Doc E\n\nBody.\n",
    )
    path = tmp_path / "plan" / "roadmap" / "README.md"
    path.write_text(path.read_text(encoding="utf-8") + "[Doc E](e.md)\n", encoding="utf-8")
    capsys.readouterr()

    assert change_plan(tmp_path, "DOC-001", transitive=True, json_output=True) == 0
    payload = json.loads(capsys.readouterr().out)
    doc_002 = next(item for item in payload["items"] if item["address"] == "DOC-002")
    doc_005 = next(item for item in payload["items"] if item["address"] == "DOC-005")
    assert doc_002["reasons"][0]["direct"] is True
    assert doc_002["reasons"][0]["distance"] == 1
    reason = doc_005["reasons"][0]
    assert reason["direct"] is False
    assert reason["distance"] == 2
    assert reason["path"] == ["DOC-001", "DOC-002", "DOC-005"]


def test_completeness_layers_are_independent_and_boundaries_visible(
    tmp_path: Path, capsys
) -> None:
    bootstrap_project(tmp_path)
    capsys.readouterr()

    assert change_plan(tmp_path, "DOC-001#intro", json_output=True) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["completeness"] == {
        "authored": "complete",
        "observed": "bounded",
        "generated": "not-enumerated",
    }
    assert payload["boundaries"][0]["category"] == "missing-anchor"
    assert payload["boundaries"][0]["source"] == "DOC-001#intro"

    assert change_plan(tmp_path, "DOC-002", reverse=True, json_output=True) == 0
    reverse_payload = json.loads(capsys.readouterr().out)
    assert reverse_payload["completeness"]["observed"] == "unknown"


def test_unknown_address_fails_closed_with_no_stdout(tmp_path: Path, capsys) -> None:
    bootstrap_project(tmp_path)
    capsys.readouterr()

    assert change_plan(tmp_path, "DOC-999") == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "ERROR" in captured.err

    assert change_plan(tmp_path, "DOC-001#no-such-anchor") == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "ERROR" in captured.err


def test_graph_blocking_error_fails_closed_with_no_stdout(tmp_path: Path, capsys) -> None:
    bootstrap_project(tmp_path)
    path = tmp_path / "plan" / "architecture" / "a.md"
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            "depends_on: [DOC-002]", "depends_on: [DOC-999]"
        ),
        encoding="utf-8",
    )
    capsys.readouterr()

    assert change_plan(tmp_path, "DOC-001") == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "metadata.depends_on references unknown ID DOC-999" in captured.err


def test_text_columns_are_fixed_and_deterministic(tmp_path: Path, capsys) -> None:
    bootstrap_project(tmp_path)
    capsys.readouterr()

    assert change_plan(tmp_path, "DOC-001") == 0
    first = capsys.readouterr().out
    assert change_plan(tmp_path, "DOC-001") == 0
    second = capsys.readouterr().out
    assert first == second
    rows = [line.split("\t") for line in first.splitlines()]
    assert all(len(row) == 11 for row in rows)
    assert {row[0] for row in rows} <= {"item", "boundary", "completeness"}
    completeness_layers = {row[1] for row in rows if row[0] == "completeness"}
    assert completeness_layers == {"authored", "observed", "generated"}


def test_json_schema(tmp_path: Path, capsys) -> None:
    bootstrap_project(tmp_path)
    capsys.readouterr()

    assert change_plan(tmp_path, "DOC-001", json_output=True) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema_version"] == 1
    assert payload["address"] == "DOC-001"
    assert payload["reverse"] is False
    assert payload["transitive"] is False
    assert isinstance(payload["items"], list)
    assert isinstance(payload["boundaries"], list)
    assert set(payload["completeness"]) == {"authored", "observed", "generated"}


def test_direct_and_projected_text_are_byte_identical(tmp_path: Path, capsys) -> None:
    bootstrap_project(tmp_path)
    capsys.readouterr()

    assert change_plan(tmp_path, "DOC-001#intro") == 0
    direct_out = capsys.readouterr().out

    assert index_projection(tmp_path, write=True) == 0
    capsys.readouterr()

    assert change_plan(tmp_path, "DOC-001#intro") == 0
    projected_captured = capsys.readouterr()
    assert projected_captured.out == direct_out
    assert projected_captured.err == ""


def test_direct_and_projected_json_are_byte_identical(tmp_path: Path, capsys) -> None:
    bootstrap_project(tmp_path)
    capsys.readouterr()

    assert change_plan(tmp_path, "DOC-002", reverse=True, json_output=True) == 0
    direct_out = capsys.readouterr().out

    assert index_projection(tmp_path, write=True) == 0
    capsys.readouterr()

    assert change_plan(tmp_path, "DOC-002", reverse=True, json_output=True) == 0
    projected = capsys.readouterr()
    assert projected.out == direct_out
    assert projected.err == ""


def test_transitive_document_plan_is_projection_equivalent(tmp_path: Path, capsys) -> None:
    bootstrap_project(tmp_path)
    capsys.readouterr()

    assert change_plan(tmp_path, "DOC-001", transitive=True, json_output=True) == 0
    direct_out = capsys.readouterr().out

    assert index_projection(tmp_path, write=True) == 0
    capsys.readouterr()

    assert change_plan(tmp_path, "DOC-001", transitive=True, json_output=True) == 0
    projected = capsys.readouterr()
    assert projected.out == direct_out
    assert projected.err == ""


def test_targeted_query_does_not_affect_unrelated_query_when_unrelated_shard_tampered(
    tmp_path: Path, capsys
) -> None:
    bootstrap_project(tmp_path)
    assert index_projection(tmp_path, write=True) == 0
    capsys.readouterr()

    config = load_config(tmp_path)
    pointer = json.loads((cache_root(config) / "current.json").read_text(encoding="utf-8"))
    generation_dir = cache_root(config) / "generations" / str(pointer["generation"])
    # DOC-004's documents shard is unrelated to a non-transitive query on DOC-001.
    shard_path = generation_dir / "documents" / "DOC" / "000000" / "DOC-004.json"
    shard = json.loads(shard_path.read_text(encoding="utf-8"))
    shard["sections"] = {"tampered": {}}
    shard_path.write_text(json.dumps(shard), encoding="utf-8")

    assert change_plan(tmp_path, "DOC-001") == 0
    captured = capsys.readouterr()
    assert captured.err == ""  # projected path used, no fallback triggered


def test_selected_shard_corruption_falls_back_visibly_and_atomically(
    tmp_path: Path, capsys
) -> None:
    bootstrap_project(tmp_path)
    assert index_projection(tmp_path, write=True) == 0
    capsys.readouterr()

    config = load_config(tmp_path)
    pointer = json.loads((cache_root(config) / "current.json").read_text(encoding="utf-8"))
    generation_dir = cache_root(config) / "generations" / str(pointer["generation"])
    shard_path = generation_dir / "references" / "DOC" / "000000" / "DOC-001.json"
    shard = json.loads(shard_path.read_text(encoding="utf-8"))
    shard["forward"] = []
    shard_path.write_text(json.dumps(shard), encoding="utf-8")

    assert change_plan(tmp_path, "DOC-001#intro") == 0
    captured = capsys.readouterr()
    assert "using direct Markdown" in captured.err
    assert "DOC-002#setup" in captured.out  # correct result recovered from direct Markdown


def test_projection_absent_falls_back_with_one_note(tmp_path: Path, capsys) -> None:
    bootstrap_project(tmp_path)
    capsys.readouterr()

    assert change_plan(tmp_path, "DOC-001") == 0
    captured = capsys.readouterr()
    assert captured.err.count("NOTE") == 1
    assert "projection absent" in captured.err


def test_index_projection_still_builds_reference_shards_used_by_change_plan(
    tmp_path: Path,
) -> None:
    bootstrap_project(tmp_path)
    config = load_config(tmp_path)
    projection = build_projection(build_catalog(config), config)
    write_projection(config, projection)
    assert (cache_root(config) / "current.json").is_file()
