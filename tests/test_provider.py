import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from docsystem.catalog import build_catalog, validate_catalog
from docsystem.cli import (
    build_parser,
    provider_compare,
    provider_snapshot,
    show_config,
)
from docsystem.config import CONFIG_FILENAME, DEFAULT_CONFIG, load_config
from docsystem.projection import (
    PinnedProjection,
    build_projection,
    cache_root,
    config_fingerprint,
    load_pinned_projection,
    load_verified_projection,
    write_projection,
)
from docsystem.provider import (
    MAX_RESPONSE_BYTES,
    ProviderContractError,
    compare_response,
    encode_response,
    snapshot_response,
)


def _config(*, provider: bool = True) -> str:
    value = DEFAULT_CONFIG.replace(
        "[areas]\n", '[areas]\nworkspace = "."\n'
    ).replace("keep_generations = 2", "keep_generations = 20")
    if provider:
        value = value.replace(
            "[provider]\n",
            '[provider]\nid = "synthetic-docs"\nvisibility = "private"\n',
        )
    return value


def _target(*, preface: str = "", changed: str = "Beta", removed: bool = False) -> str:
    removed_section = "" if removed else '<a id="removed"></a>\n## Removed\nGamma\n'
    return f"""\
---
id: DOC-002
revision: 1
---
# Target
{preface}<a id="stable"></a>
## Stable
Alpha
<a id="changed"></a>
## Changed
{changed}
{removed_section}"""


def _write_project(root: Path, *, provider: bool = True) -> Path:
    (root / CONFIG_FILENAME).write_text(_config(provider=provider), encoding="utf-8")
    docs = root / "plan"
    docs.mkdir()
    (docs / "README.md").write_text(
        """\
---
id: DOC-001
revision: 1
---
# Index
[Target](target.md)
""",
        encoding="utf-8",
    )
    (docs / "target.md").write_text(_target(), encoding="utf-8")
    return docs


def _generation(project: Path) -> str:
    config = load_config(project)
    catalog = build_catalog(config)
    errors = [
        issue
        for issue in validate_catalog(catalog, config)
        if issue.severity != "warning"
    ]
    assert errors == []
    return write_projection(config, build_projection(catalog, config))


def _all_pages(function, *args, page_size: int = 2):
    items = []
    cursor = None
    payloads = []
    while True:
        payload = function(*args, cursor=cursor, page_size=page_size)
        payloads.append(payload)
        key = "observations" if "observations" in payload else "changes"
        items.extend(payload[key])
        cursor = payload["page"]["next_cursor"]
        if cursor is None:
            return items, payloads


def test_provider_config_is_optional_and_validated(tmp_path: Path) -> None:
    _write_project(tmp_path, provider=False)
    config = load_config(tmp_path)
    assert config.provider_id is None
    assert config.provider_visibility == "private"

    cases = (
        ('[provider]\nid = ""\n', "provider.id"),
        ('[provider]\nid = "Upper"\n', "provider.id"),
        ('[provider]\nid = "ok"\nvisibility = "secret"\n', "provider.visibility"),
        ('[provider]\nid = "ok"\nextra = true\n', "unknown key"),
    )
    for index, (provider, expected) in enumerate(cases):
        project = tmp_path / f"invalid-{index}"
        project.mkdir()
        (project / CONFIG_FILENAME).write_text(
            DEFAULT_CONFIG.replace("[provider]\n", provider), encoding="utf-8"
        )
        with pytest.raises(ValueError, match=expected):
            load_config(project)


def test_provider_identity_and_visibility_shape_projection_generation(
    tmp_path: Path, capsys
) -> None:
    _write_project(tmp_path)
    baseline_config = load_config(tmp_path)
    baseline = config_fingerprint(baseline_config)
    assert show_config(tmp_path) == 0
    shown = capsys.readouterr().out
    assert "provider.id=synthetic-docs" in shown
    assert "provider.visibility=private" in shown
    config_path = tmp_path / CONFIG_FILENAME
    config_path.write_text(
        _config().replace('visibility = "private"', 'visibility = "public"'),
        encoding="utf-8",
    )
    assert config_fingerprint(load_config(tmp_path)) != baseline
    config_path.write_text(
        _config().replace('id = "synthetic-docs"', 'id = "other-docs"'),
        encoding="utf-8",
    )
    assert config_fingerprint(load_config(tmp_path)) != baseline


def test_snapshot_is_body_free_paged_and_stable_after_live_drift(
    tmp_path: Path,
) -> None:
    docs = _write_project(tmp_path)
    generation = _generation(tmp_path)
    config = load_config(tmp_path)
    current, reason = load_verified_projection(config)
    assert current is not None
    assert reason == "projection current"
    snapshot = load_pinned_projection(config, generation[:12])

    observations, pages = _all_pages(snapshot_response, snapshot, page_size=2)
    assert len(observations) == 7
    assert pages[0]["snapshot"]["provider_id"] == "synthetic-docs"
    assert pages[0]["snapshot"]["coverage"] == {
        "documents": 2,
        "sections": 5,
        "excluded_markdown": 0,
        "unmapped_markdown": 0,
    }
    assert all(
        len(encode_response(page).encode("utf-8")) <= MAX_RESPONSE_BYTES
        for page in pages
    )
    by_address = {
        (item["document_id"], item["anchor"]): item for item in observations
    }
    assert by_address[("DOC-002", "stable")]["anchor_kind"] == "explicit"
    assert by_address[("DOC-001", "index")]["anchor_kind"] == "generated"
    rendered = "".join(encode_response(page) for page in pages)
    assert "Alpha" not in rendered
    assert str(tmp_path) not in rendered
    assert "target.md" in rendered
    assert encode_response(snapshot_response(snapshot, page_size=2)) == encode_response(
        snapshot_response(snapshot, page_size=2)
    )

    # Explicit pinned export is historical evidence, not current-source reads.
    (docs / "target.md").write_text(_target(changed="live drift"), encoding="utf-8")
    assert snapshot_response(
        load_pinned_projection(config, generation), page_size=2
    ) == pages[0]


def test_compare_classifies_relocation_change_missing_and_added(
    tmp_path: Path,
) -> None:
    docs = _write_project(tmp_path)
    before_generation = _generation(tmp_path)

    moved = docs / "nested" / "renamed.md"
    moved.parent.mkdir()
    moved.write_text(
        _target(preface="A new introductory paragraph.\n", changed="Delta", removed=True)
        + '<a id="added"></a>\n## Added\nNew\n',
        encoding="utf-8",
    )
    (docs / "target.md").unlink()
    (docs / "README.md").write_text(
        """\
---
id: DOC-001
revision: 1
---
# Index
[Target](nested/renamed.md)
""",
        encoding="utf-8",
    )
    after_generation = _generation(tmp_path)
    config = load_config(tmp_path)
    before = load_pinned_projection(config, before_generation)
    after = load_pinned_projection(config, after_generation)

    payload = compare_response(before, after, page_size=100)
    changes = {
        (item["entity"]["document_id"], item["entity"]["anchor"]): item
        for item in payload["changes"]
    }
    assert changes[("DOC-002", "stable")]["classification"] == "relocated"
    assert changes[("DOC-002", "stable")]["before"]["content_hash"] == changes[
        ("DOC-002", "stable")
    ]["after"]["content_hash"]
    assert changes[("DOC-002", "stable")]["before"]["path"] == "target.md"
    assert changes[("DOC-002", "stable")]["after"]["path"] == "nested/renamed.md"
    assert changes[("DOC-002", "changed")]["classification"] == "changed"
    assert changes[("DOC-002", "removed")]["classification"] == "missing"
    assert changes[("DOC-002", "added")]["classification"] == "added"
    assert changes[("DOC-002", None)]["classification"] == "changed"
    assert payload["summary"] == {
        "unchanged": 0,
        "relocated": 1,
        "changed": 5,
        "missing": 1,
        "added": 1,
        "ambiguous": 0,
    }

    reconstructed, pages = _all_pages(
        compare_response, before, after, page_size=2
    )
    assert reconstructed == payload["changes"]
    assert len({item["page"]["cursor"] for item in pages}) == len(pages)
    assert encode_response(compare_response(before, after, page_size=2)) == encode_response(
        compare_response(before, after, page_size=2)
    )

    wrong_cursor = pages[0]["page"]["next_cursor"]
    assert wrong_cursor is not None
    with pytest.raises(ProviderContractError, match="does not belong"):
        snapshot_response(after, cursor=wrong_cursor, page_size=2)
    with pytest.raises(ProviderContractError, match="cursor is invalid"):
        compare_response(
            before,
            after,
            cursor=("A" if wrong_cursor[0] != "A" else "B") + wrong_cursor[1:],
            page_size=2,
        )


def test_compare_distinguishes_line_and_path_relocation(tmp_path: Path) -> None:
    docs = _write_project(tmp_path)
    original_generation = _generation(tmp_path)

    target = docs / "target.md"
    target.write_text(
        _target(preface="Introductory line.\n"), encoding="utf-8"
    )
    shifted_generation = _generation(tmp_path)
    config = load_config(tmp_path)
    original = load_pinned_projection(config, original_generation)
    shifted = load_pinned_projection(config, shifted_generation)
    shifted_changes = {
        (item["entity"]["document_id"], item["entity"]["anchor"]): item
        for item in compare_response(original, shifted)["changes"]
    }
    stable = shifted_changes[("DOC-002", "stable")]
    assert stable["classification"] == "relocated"
    assert stable["before"]["path"] == stable["after"]["path"]
    assert stable["before"]["lines"] != stable["after"]["lines"]
    assert stable["before"]["content_hash"] == stable["after"]["content_hash"]

    moved = docs / "nested" / "target.md"
    moved.parent.mkdir()
    target.replace(moved)
    (docs / "README.md").write_text(
        """\
---
id: DOC-001
revision: 1
---
# Index
[Target](nested/target.md)
""",
        encoding="utf-8",
    )
    moved_generation = _generation(tmp_path)
    moved_snapshot = load_pinned_projection(config, moved_generation)
    moved_changes = {
        (item["entity"]["document_id"], item["entity"]["anchor"]): item
        for item in compare_response(shifted, moved_snapshot)["changes"]
    }
    moved_stable = moved_changes[("DOC-002", "stable")]
    assert moved_stable["classification"] == "relocated"
    assert moved_stable["before"]["path"] == "target.md"
    assert moved_stable["after"]["path"] == "nested/target.md"
    assert moved_stable["before"]["lines"] == moved_stable["after"]["lines"]
    assert (
        moved_stable["before"]["content_hash"]
        == moved_stable["after"]["content_hash"]
    )


def test_compare_treats_cross_document_section_move_as_missing_and_added(
    tmp_path: Path,
) -> None:
    docs = _write_project(tmp_path)
    before_generation = _generation(tmp_path)

    (docs / "target.md").write_text(
        _target(removed=True),
        encoding="utf-8",
    )
    (docs / "destination.md").write_text(
        """\
---
id: DOC-003
revision: 1
---
# Destination
<a id="removed"></a>
## Removed
Gamma
""",
        encoding="utf-8",
    )
    (docs / "README.md").write_text(
        """\
---
id: DOC-001
revision: 2
---
# Index
[Target](target.md)
[Destination](destination.md)
""",
        encoding="utf-8",
    )
    after_generation = _generation(tmp_path)
    config = load_config(tmp_path)
    before = load_pinned_projection(config, before_generation)
    after = load_pinned_projection(config, after_generation)

    changes = compare_response(before, after)["changes"]
    moved_changes = [
        item for item in changes if item["entity"]["anchor"] == "removed"
    ]
    assert [item["classification"] for item in moved_changes] == [
        "missing",
        "added",
    ]
    assert [item["entity"]["document_id"] for item in moved_changes] == [
        "DOC-002",
        "DOC-003",
    ]
    assert (
        moved_changes[0]["before"]["content_hash"]
        == moved_changes[1]["after"]["content_hash"]
    )


def test_provider_cli_fails_closed_for_unavailable_unknown_and_corrupt_generation(
    tmp_path: Path, capsys
) -> None:
    _write_project(tmp_path)
    generation = _generation(tmp_path)

    assert provider_snapshot(tmp_path, "f" * 64) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "[generation-unknown]" in captured.err

    assert provider_snapshot(tmp_path, generation, page_size=0) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "[page-invalid]" in captured.err

    config = load_config(tmp_path)
    generation_dir = cache_root(config) / "generations" / generation
    document_shard = next((generation_dir / "documents").rglob("DOC-002.json"))
    document_shard.write_text("{}\n", encoding="utf-8")
    assert provider_snapshot(tmp_path, generation) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "[generation-corrupt]" in captured.err

    absent = tmp_path / "absent"
    absent.mkdir()
    _write_project(absent)
    assert provider_snapshot(absent, generation) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "[provider-unavailable]" in captured.err


def test_pinned_generation_rejects_ambiguous_unsupported_incomplete_and_mismatch(
    tmp_path: Path, capsys
) -> None:
    docs = _write_project(tmp_path)
    generation = _generation(tmp_path)
    config = load_config(tmp_path)
    generation_root = cache_root(config) / "generations"

    ambiguous = generation_root / (generation[:12] + ("f" * 52))
    ambiguous.mkdir()
    assert provider_snapshot(tmp_path, generation[:12]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "[generation-selector-ambiguous]" in captured.err
    ambiguous.rmdir()

    manifest = generation_root / generation / "manifest.json"
    original_manifest = manifest.read_text(encoding="utf-8")
    value = json.loads(original_manifest)
    value["schema_version"] = 4
    manifest.write_text(json.dumps(value), encoding="utf-8")
    assert provider_snapshot(tmp_path, generation) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "[generation-unsupported]" in captured.err
    manifest.write_text(original_manifest, encoding="utf-8")

    (docs / "README.md").write_text(
        """\
---
id: DOC-001
revision: 1
---
# Index
""",
        encoding="utf-8",
    )
    incomplete = write_projection(
        config, build_projection(build_catalog(config), config)
    )
    assert provider_snapshot(tmp_path, incomplete) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "[generation-incomplete]" in captured.err

    (tmp_path / CONFIG_FILENAME).write_text(
        _config().replace('id = "synthetic-docs"', 'id = "other-docs"'),
        encoding="utf-8",
    )
    assert provider_snapshot(tmp_path, generation) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "[provider-mismatch]" in captured.err


def test_provider_cli_and_parser_are_explicit_and_current_pointer_independent(
    tmp_path: Path, capsys
) -> None:
    docs = _write_project(tmp_path)
    before = _generation(tmp_path)
    (docs / "target.md").write_text(_target(changed="Delta"), encoding="utf-8")
    after = _generation(tmp_path)

    pointer = cache_root(load_config(tmp_path)) / "current.json"
    pointer.write_text("not JSON\n", encoding="utf-8")
    assert provider_compare(tmp_path, before, after, page_size=2) == 0
    first = capsys.readouterr()
    assert first.err == ""
    payload = json.loads(first.out)
    assert payload["before"]["generation"] == before
    assert payload["after"]["generation"] == after

    args = build_parser().parse_args(
        [
            "provider",
            "compare",
            before,
            after,
            str(tmp_path),
            "--json",
            "--page-size",
            "2",
        ]
    )
    assert args.provider_command == "compare"
    assert args.before_generation == before
    assert args.after_generation == after


def test_provider_snapshot_requires_identity_and_enforces_byte_bound(
    tmp_path: Path, capsys
) -> None:
    _write_project(tmp_path, provider=False)
    assert provider_snapshot(tmp_path, "f" * 64) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "[provider-not-configured]" in captured.err

    huge = "a" * MAX_RESPONSE_BYTES
    snapshot = PinnedProjection(
        generation="f" * 64,
        provider={
            "schema_version": 1,
            "id": "synthetic-docs",
            "visibility": "private",
            "capabilities": [
                "bounded-entity-observations-v1",
                "pinned-generation-compare-v1",
            ],
            "catalog_complete": True,
            "coverage": {
                "documents": 1,
                "sections": 1,
                "excluded_markdown": 0,
                "unmapped_markdown": 0,
            },
            "scope": {
                "catalog": "included-markdown",
                "path_base": "documentation-root",
                "sections": "all-addressable",
            },
            "boundaries": [
                "excluded-and-unmapped-paths-omitted",
                "generated-anchor-stability-depends-on-heading",
                "markdown-bodies-omitted",
                "metadata-and-relations-omitted",
            ],
        },
        documents={
            "DOC-001": {
                "path": "doc.md",
                "line_count": 2,
                "source_sha256": "0" * 64,
                "sections": {
                    huge: {
                        "anchor_kind": "explicit",
                        "start_line": 2,
                        "end_line": 2,
                        "sha256": "1" * 64,
                    }
                },
            }
        },
    )
    first = snapshot_response(snapshot, page_size=1)
    with pytest.raises(ProviderContractError, match="cannot fit"):
        snapshot_response(snapshot, cursor=first["page"]["next_cursor"], page_size=1)


def test_provider_installed_style_subprocess_uses_only_public_cli(
    tmp_path: Path,
) -> None:
    docs = _write_project(tmp_path)
    before = _generation(tmp_path)
    (docs / "target.md").write_text(_target(changed="Изменено"), encoding="utf-8")
    after = _generation(tmp_path)
    unrelated = tmp_path / "unrelated"
    unrelated.mkdir()
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1] / "src"))
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "docsystem",
            "provider",
            "compare",
            before,
            after,
            str(tmp_path),
            "--json",
        ],
        cwd=unrelated,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    payload = json.loads(result.stdout)
    assert payload["kind"] == "provider-compare"
    assert payload["summary"]["changed"] >= 1
    assert "Изменено" not in result.stdout


def test_public_provider_snapshot_example_is_valid() -> None:
    project = Path(__file__).resolve().parents[1] / "examples" / "provider-snapshots"
    config = load_config(project)
    catalog = build_catalog(config)
    assert [
        issue
        for issue in validate_catalog(catalog, config)
        if issue.severity != "warning"
    ] == []
    projection = build_projection(catalog, config)
    assert projection["provider"]["id"] == "example-docs"
    assert projection["provider"]["catalog_complete"] is True
