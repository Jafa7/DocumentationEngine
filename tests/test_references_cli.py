import hashlib
import json
from pathlib import Path

from docsystem.catalog import build_catalog
from docsystem.cli import index_projection, initialize, references
from docsystem.config import load_config
from docsystem.graph import Address
from docsystem.projection import (
    build_projection,
    cache_root,
    open_targeted_projection,
    targeted_forward_edges,
    write_projection,
)


def _write(project_root: Path, relative: str, text: str) -> None:
    path = project_root / "plan" / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def bootstrap_project(tmp_path: Path) -> Path:
    """An initialized project with two cross-referencing, reachable documents."""

    assert initialize(tmp_path) == 0
    _write(
        tmp_path,
        "architecture/README.md",
        "---\nid: DOC-100\nrevision: 1\n---\n\n# Architecture\n\n[Doc A](a.md)\n",
    )
    _write(
        tmp_path,
        "architecture/a.md",
        "---\nid: DOC-001\nrevision: 1\ndepends_on: [DOC-002]\n---\n\n"
        "# Doc A\n\n<a id=\"intro\"></a>\n## Introduction\n\n"
        "See [setup](../roadmap/b.md#setup) and [broken](../roadmap/b.md#missing).\n",
    )
    _write(
        tmp_path,
        "roadmap/README.md",
        "---\nid: DOC-101\nrevision: 1\n---\n\n# Roadmap\n\n[Doc B](b.md)\n",
    )
    _write(
        tmp_path,
        "roadmap/b.md",
        "---\nid: DOC-002\nrevision: 1\n---\n\n# Doc B\n\n"
        "<a id=\"setup\"></a>\n## Setup\n\nBody.\n",
    )
    return tmp_path


def test_references_text_columns_and_ordering(tmp_path: Path, capsys) -> None:
    bootstrap_project(tmp_path)
    capsys.readouterr()

    assert references(tmp_path, "DOC-001") == 0
    out = capsys.readouterr().out.splitlines()
    rows = [line.split("\t") for line in out]
    assert all(len(row) == 9 for row in rows)
    assert {row[6] for row in rows} == {
        "DOC-001#doc-a",
        "DOC-001#intro",
        "DOC-002",
    }
    assert all(row[0] == "edge" for row in rows)


def test_references_json_schema(tmp_path: Path, capsys) -> None:
    bootstrap_project(tmp_path)
    capsys.readouterr()

    assert references(tmp_path, "DOC-001#intro", json_output=True) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["address"] == "DOC-001#intro"
    assert payload["schema_version"] == 1
    assert payload["reverse"] is False
    assert payload["transitive"] is False
    assert payload["completeness"] == {"authored": "complete", "observed": "bounded"}
    assert payload["results"][0]["address"] == "DOC-002#setup"
    assert payload["boundaries"][0]["category"] == "missing-anchor"
    assert payload["boundaries"][0]["source"] == "DOC-001#intro"


def test_references_reverse_and_transitive(tmp_path: Path, capsys) -> None:
    bootstrap_project(tmp_path)
    capsys.readouterr()

    assert references(tmp_path, "DOC-002#setup", reverse=True) == 0
    out = capsys.readouterr().out
    assert "DOC-001#intro" in out

    assert references(tmp_path, "DOC-001", transitive=True) == 0
    out = capsys.readouterr().out
    assert any("transitive" in line for line in out.splitlines())


def test_reverse_json_does_not_overclaim_observed_completeness(
    tmp_path: Path, capsys
) -> None:
    bootstrap_project(tmp_path)
    capsys.readouterr()

    assert references(tmp_path, "DOC-002#setup", reverse=True, json_output=True) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["completeness"] == {"authored": "complete", "observed": "unknown"}


def test_transitive_query_reports_boundaries_from_reached_sections(
    tmp_path: Path, capsys
) -> None:
    bootstrap_project(tmp_path)
    path = tmp_path / "plan" / "roadmap" / "b.md"
    path.write_text(path.read_text(encoding="utf-8") + "\n[external](https://example.com)\n")
    capsys.readouterr()

    assert references(tmp_path, "DOC-001", transitive=True, json_output=True) == 0
    payload = json.loads(capsys.readouterr().out)
    assert any(
        item["source"] == "DOC-002#setup" and item["category"] == "external"
        for item in payload["boundaries"]
    )


def test_unknown_address_fails_closed_with_no_stdout(tmp_path: Path, capsys) -> None:
    bootstrap_project(tmp_path)
    capsys.readouterr()

    assert references(tmp_path, "DOC-999") == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "ERROR" in captured.err


def test_graph_errors_are_reported_precisely_instead_of_as_unknown_address(
    tmp_path: Path, capsys
) -> None:
    bootstrap_project(tmp_path)
    path = tmp_path / "plan" / "architecture" / "a.md"
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            "depends_on: [DOC-002]", "depends_on: [DOC-999]"
        ),
        encoding="utf-8",
    )
    capsys.readouterr()

    assert references(tmp_path, "DOC-001") == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "metadata.depends_on references unknown ID DOC-999" in captured.err
    assert "unknown graph address" not in captured.err

    assert references(tmp_path, "DOC-001#no-such-anchor") == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "ERROR" in captured.err


def test_direct_and_projected_output_are_byte_identical(tmp_path: Path, capsys) -> None:
    bootstrap_project(tmp_path)
    capsys.readouterr()

    assert references(tmp_path, "DOC-001#intro", reverse=False) == 0
    direct_out = capsys.readouterr().out

    assert index_projection(tmp_path, write=True) == 0
    capsys.readouterr()

    assert references(tmp_path, "DOC-001#intro", reverse=False) == 0
    projected_captured = capsys.readouterr()
    assert projected_captured.out == direct_out
    assert projected_captured.err == ""  # no fallback diagnostic once the projection exists


def test_direct_and_projected_json_are_byte_identical(tmp_path: Path, capsys) -> None:
    bootstrap_project(tmp_path)
    capsys.readouterr()

    assert references(tmp_path, "DOC-001#intro", json_output=True) == 0
    direct_out = capsys.readouterr().out

    assert index_projection(tmp_path, write=True) == 0
    capsys.readouterr()

    assert references(tmp_path, "DOC-001#intro", json_output=True) == 0
    projected = capsys.readouterr()
    assert projected.out == direct_out
    assert projected.err == ""


def test_targeted_loader_does_not_read_unrelated_shards(tmp_path: Path) -> None:
    bootstrap_project(tmp_path)
    config = load_config(tmp_path)
    write_projection(config, build_projection(build_catalog(config), config))

    accessor, reason = open_targeted_projection(config)
    assert accessor is not None, reason
    result = targeted_forward_edges(accessor, Address("DOC-001"))
    assert result is not None
    assert accessor.read_shards == {("documents", "DOC-001"), ("references", "DOC-001")}
    assert ("documents", "DOC-002") not in accessor.read_shards
    assert ("references", "DOC-002") not in accessor.read_shards


def test_tampered_selected_shard_falls_back_visibly(tmp_path: Path, capsys) -> None:
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

    assert references(tmp_path, "DOC-001#intro") == 0
    captured = capsys.readouterr()
    assert "using direct Markdown" in captured.err
    assert "DOC-002#setup" in captured.out  # correct result recovered from direct Markdown


def test_tampered_shard_and_matching_manifest_hash_still_invalidate_generation(
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
    body = {key: value for key, value in shard.items() if key not in {"schema_version", "id"}}
    shard_hash = hashlib.sha256(
        json.dumps(
            body, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()
    manifest_path = generation_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["references"]["DOC-001"]["shard_sha256"] = shard_hash
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    assert references(tmp_path, "DOC-001#intro") == 0
    captured = capsys.readouterr()
    assert "projection corrupt; using direct Markdown" in captured.err
    assert "DOC-002#setup" in captured.out


def test_tampered_unrelated_shard_does_not_affect_unrelated_query(tmp_path: Path, capsys) -> None:
    bootstrap_project(tmp_path)
    assert index_projection(tmp_path, write=True) == 0
    capsys.readouterr()

    config = load_config(tmp_path)
    pointer = json.loads((cache_root(config) / "current.json").read_text(encoding="utf-8"))
    generation_dir = cache_root(config) / "generations" / str(pointer["generation"])
    # DOC-002's documents shard is unrelated to a plain forward query on DOC-001.
    shard_path = generation_dir / "documents" / "DOC" / "000000" / "DOC-002.json"
    shard = json.loads(shard_path.read_text(encoding="utf-8"))
    shard["dependencies"] = [
        {"relation": "depends_on", "target": "DOC-999", "expected_revision": None}
    ]
    shard_path.write_text(json.dumps(shard), encoding="utf-8")

    assert references(tmp_path, "DOC-001") == 0
    captured = capsys.readouterr()
    assert captured.err == ""  # projected path used, no fallback triggered
