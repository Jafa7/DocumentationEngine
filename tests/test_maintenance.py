import json
from pathlib import Path

from docsystem.catalog import build_catalog
from docsystem.cli import initialize, maintenance
from docsystem.config import CONFIG_FILENAME, load_config
from docsystem.projection import build_projection, cache_root, write_projection

MAINTENANCE_TOML = """
[[maintenance]]
name = "install-version"
source_document = "DOC-001"
source_anchor = "install-block"

[[maintenance.occurrences]]
document = "DOC-002"
anchor = "quickstart"
role = "current"

[[maintenance.occurrences]]
document = "DOC-003"
anchor = "changelog"
role = "historical"

[[maintenance.occurrences]]
document = "DOC-004"
anchor = "example-usage"
role = "example"

[[maintenance.occurrences]]
document = "DOC-005"
anchor = "release-snapshot"
role = "snapshot"

[[maintenance.occurrences]]
document = "DOC-006"
anchor = "unmanaged-note"
role = "unmanaged"
"""


def _write(project_root: Path, relative: str, text: str) -> None:
    path = project_root / "plan" / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _append_maintenance_config(project_root: Path, extra_toml: str) -> None:
    config_path = project_root / CONFIG_FILENAME
    config_path.write_text(
        config_path.read_text(encoding="utf-8") + extra_toml, encoding="utf-8"
    )


def bootstrap_project(
    tmp_path: Path,
    *,
    occurrence_line: str = "pip install docsystem==1.2.2\n",
    source_line: str = "pip install docsystem==1.2.2\n",
) -> Path:
    """A project with one `current` occurrence and one of every other role."""

    assert initialize(tmp_path) == 0
    _append_maintenance_config(tmp_path, MAINTENANCE_TOML)
    _write(
        tmp_path,
        "architecture/a.md",
        "---\nid: DOC-001\nrevision: 1\n---\n\n# Doc A\n\n"
        '<a id="install-block"></a>\n## Install block\n\n'
        "<!-- docsystem:source target=install-version -->\n"
        f"{source_line}"
        "<!-- /docsystem:source target=install-version -->\n\n"
        '<a id="docs"></a>\n## Docs\n\n'
        "Example syntax shown for reference only:\n\n"
        "```text\n"
        "<!-- docsystem:source target=install-version -->\n"
        "<!-- /docsystem:source target=install-version -->\n"
        "```\n",
    )
    _write(
        tmp_path,
        "architecture/b.md",
        "---\nid: DOC-002\nrevision: 1\n---\n\n# Doc B\n\n"
        '<a id="quickstart"></a>\n## Quickstart\n\n'
        "<!-- docsystem:managed target=install-version -->\n"
        f"{occurrence_line}"
        "<!-- /docsystem:managed target=install-version -->\n",
    )
    _write(
        tmp_path,
        "architecture/c.md",
        "---\nid: DOC-003\nrevision: 1\n---\n\n# Doc C\n\n"
        '<a id="changelog"></a>\n## Changelog\n\n'
        "pip install docsystem==1.0.0\n",
    )
    _write(
        tmp_path,
        "architecture/d.md",
        "---\nid: DOC-004\nrevision: 1\n---\n\n# Doc D\n\n"
        '<a id="example-usage"></a>\n## Example usage\n\n'
        "pip install docsystem==1.2.2  # illustrative only\n",
    )
    _write(
        tmp_path,
        "architecture/e.md",
        "---\nid: DOC-005\nrevision: 1\n---\n\n# Doc E\n\n"
        '<a id="release-snapshot"></a>\n## Release snapshot\n\n'
        "pip install docsystem==0.9.0\n",
    )
    _write(
        tmp_path,
        "architecture/f.md",
        "---\nid: DOC-006\nrevision: 1\n---\n\n# Doc F\n\n"
        '<a id="unmanaged-note"></a>\n## Unmanaged note\n\n'
        "pip install docsystem\n",
    )
    return tmp_path


def _snapshot(project_root: Path) -> dict[str, str]:
    return {
        path.as_posix(): path.read_text(encoding="utf-8")
        for path in sorted((project_root / "plan").rglob("*.md"))
    }


# --- clean / drift / roles --------------------------------------------------


def test_clean_target_check_and_preview_both_exit_zero(tmp_path: Path, capsys) -> None:
    bootstrap_project(tmp_path)
    capsys.readouterr()
    before = _snapshot(tmp_path)

    assert maintenance(tmp_path, "install-version", check=True, preview=False) == 0
    out = capsys.readouterr().out
    assert "status\tclean" in out.splitlines()[0]

    assert maintenance(tmp_path, "install-version", check=False, preview=True) == 0
    out = capsys.readouterr().out
    assert "status\tclean" in out.splitlines()[0]
    assert "## diff" not in out
    assert _snapshot(tmp_path) == before


def test_drifted_current_occurrence_check_exits_two_with_deterministic_diff(
    tmp_path: Path, capsys
) -> None:
    bootstrap_project(tmp_path, occurrence_line="pip install docsystem==1.0.0\n")
    capsys.readouterr()
    before = _snapshot(tmp_path)

    assert maintenance(tmp_path, "install-version", check=True, preview=False) == 2
    out = capsys.readouterr().out
    assert out.splitlines()[0].split("\t")[1:] == ["install-version", "status", "drifted"]
    assert "occurrence\tDOC-002#quickstart\tcurrent\tdrifted" in out
    assert "-pip install docsystem==1.0.0" in out
    assert "+pip install docsystem==1.2.2" in out
    assert _snapshot(tmp_path) == before


def test_preview_of_drifted_target_still_exits_zero(tmp_path: Path, capsys) -> None:
    bootstrap_project(tmp_path, occurrence_line="pip install docsystem==1.0.0\n")
    capsys.readouterr()
    assert maintenance(tmp_path, "install-version", check=False, preview=True) == 0
    out = capsys.readouterr().out
    assert "status\tdrifted" in out.splitlines()[0]


def test_non_current_roles_are_excluded_evidence_never_diffed(
    tmp_path: Path, capsys
) -> None:
    bootstrap_project(tmp_path)
    capsys.readouterr()
    assert (
        maintenance(tmp_path, "install-version", check=False, preview=True, json_output=True)
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    by_address = {item["address"]: item for item in payload["occurrences"]}
    for address, role in (
        ("DOC-003#changelog", "historical"),
        ("DOC-004#example-usage", "example"),
        ("DOC-005#release-snapshot", "snapshot"),
        ("DOC-006#unmanaged-note", "unmanaged"),
    ):
        item = by_address[address]
        assert item["role"] == role
        assert item["eligible"] is False
        assert item["disposition"] == "excluded"
        assert item["diff"] is None
        assert item["block_hash"] is None
        assert role in item["reason"]
    current = by_address["DOC-002#quickstart"]
    assert current["eligible"] is True
    assert current["disposition"] == "clean"


def test_json_output_is_deterministic_across_runs(tmp_path: Path, capsys) -> None:
    bootstrap_project(tmp_path, occurrence_line="pip install docsystem==1.0.0\n")
    capsys.readouterr()
    maintenance(tmp_path, "install-version", check=False, preview=True, json_output=True)
    first = capsys.readouterr().out
    maintenance(tmp_path, "install-version", check=False, preview=True, json_output=True)
    second = capsys.readouterr().out
    assert first == second
    payload = json.loads(first)
    assert payload["schema_version"] == 1
    assert payload["target"] == "install-version"
    assert payload["source"]["section_range"]["start_line"] > 0
    assert payload["source"]["marker_range"]["start_line"] > 0
    assert payload["source"]["content_range"]["start_line"] > 0
    current = payload["occurrences"][0]
    assert current["document_hash"]
    assert current["section_hash"]
    assert current["marker_range"]["start_line"] > 0
    assert current["content_range"]["start_line"] > 0


# --- source change / stale evidence -----------------------------------------


def test_source_content_change_flips_previously_clean_occurrence_to_drifted(
    tmp_path: Path, capsys
) -> None:
    bootstrap_project(tmp_path)
    assert maintenance(tmp_path, "install-version", check=True, preview=False) == 0
    capsys.readouterr()

    a_path = tmp_path / "plan" / "architecture" / "a.md"
    a_path.write_text(
        a_path.read_text(encoding="utf-8").replace("1.2.2", "1.3.0"), encoding="utf-8"
    )

    assert maintenance(tmp_path, "install-version", check=True, preview=False) == 2
    out = capsys.readouterr().out
    assert "block_hash=" in out
    assert "+pip install docsystem==1.3.0" in out


def test_stale_expected_source_hash_fails_closed(tmp_path: Path, capsys) -> None:
    bootstrap_project(tmp_path)
    capsys.readouterr()

    assert (
        maintenance(
            tmp_path,
            "install-version",
            check=False,
            preview=True,
            expected_source_hash="0" * 64,
        )
        == 1
    )
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "source block hash changed" in captured.err


def test_invalid_expected_source_hash_fails_closed(tmp_path: Path, capsys) -> None:
    bootstrap_project(tmp_path)
    capsys.readouterr()

    assert (
        maintenance(
            tmp_path,
            "install-version",
            check=False,
            preview=True,
            expected_source_hash="not-a-sha256",
        )
        == 1
    )
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "must be a lowercase SHA-256 value" in captured.err


# --- errors: unknown target/address, empty stdout ---------------------------


def test_unknown_target_fails_closed_with_empty_stdout(tmp_path: Path, capsys) -> None:
    bootstrap_project(tmp_path)
    capsys.readouterr()
    assert maintenance(tmp_path, "does-not-exist", check=True, preview=False) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "unknown maintenance target" in captured.err


def test_unknown_source_document_fails_closed(tmp_path: Path, capsys) -> None:
    bootstrap_project(tmp_path)
    capsys.readouterr()
    (tmp_path / "plan" / "architecture" / "a.md").unlink()
    assert maintenance(tmp_path, "install-version", check=True, preview=False) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "document ID not found: DOC-001" in captured.err


def test_graph_blocking_error_fails_closed_with_empty_stdout(tmp_path: Path, capsys) -> None:
    bootstrap_project(tmp_path)
    capsys.readouterr()
    _write(
        tmp_path,
        "architecture/dup.md",
        "---\nid: DOC-002\nrevision: 1\n---\n\n# Duplicate Doc B\n\nBody.\n",
    )
    assert maintenance(tmp_path, "install-version", check=True, preview=False) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "duplicate document ID DOC-002" in captured.err


# --- marker validation: missing/duplicate/nested/crossed/fenced ------------


def test_missing_managed_marker_fails_closed(tmp_path: Path, capsys) -> None:
    bootstrap_project(tmp_path)
    capsys.readouterr()
    _write(
        tmp_path,
        "architecture/b.md",
        "---\nid: DOC-002\nrevision: 1\n---\n\n# Doc B\n\n"
        '<a id="quickstart"></a>\n## Quickstart\n\n'
        "pip install docsystem==1.2.2\n",
    )
    assert maintenance(tmp_path, "install-version", check=True, preview=False) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "no managed marker pair found" in captured.err


def test_duplicate_source_marker_fails_closed(tmp_path: Path, capsys) -> None:
    bootstrap_project(tmp_path)
    capsys.readouterr()
    _write(
        tmp_path,
        "architecture/a.md",
        "---\nid: DOC-001\nrevision: 1\n---\n\n# Doc A\n\n"
        '<a id="install-block"></a>\n## Install block\n\n'
        "<!-- docsystem:source target=install-version -->\n"
        "pip install docsystem==1.2.2\n"
        "<!-- /docsystem:source target=install-version -->\n\n"
        "<!-- docsystem:source target=install-version -->\n"
        "pip install docsystem==1.2.2\n"
        "<!-- /docsystem:source target=install-version -->\n",
    )
    assert maintenance(tmp_path, "install-version", check=True, preview=False) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "duplicate source marker pair" in captured.err


def test_nested_source_marker_is_reported_as_duplicate(tmp_path: Path, capsys) -> None:
    bootstrap_project(tmp_path)
    capsys.readouterr()
    _write(
        tmp_path,
        "architecture/a.md",
        "---\nid: DOC-001\nrevision: 1\n---\n\n# Doc A\n\n"
        '<a id="install-block"></a>\n## Install block\n\n'
        "<!-- docsystem:source target=install-version -->\n"
        "<!-- docsystem:source target=install-version -->\n"
        "pip install docsystem==1.2.2\n"
        "<!-- /docsystem:source target=install-version -->\n"
        "<!-- /docsystem:source target=install-version -->\n",
    )
    assert maintenance(tmp_path, "install-version", check=True, preview=False) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "duplicate source marker pair" in captured.err


def test_crossed_markers_fail_closed(tmp_path: Path, capsys) -> None:
    bootstrap_project(tmp_path)
    capsys.readouterr()
    _write(
        tmp_path,
        "architecture/a.md",
        "---\nid: DOC-001\nrevision: 1\n---\n\n# Doc A\n\n"
        '<a id="install-block"></a>\n## Install block\n\n'
        "<!-- docsystem:source target=install-version -->\n"
        "<!-- docsystem:managed target=install-version -->\n"
        "pip install docsystem==1.2.2\n"
        "<!-- /docsystem:source target=install-version -->\n"
        "<!-- /docsystem:managed target=install-version -->\n",
    )
    assert maintenance(tmp_path, "install-version", check=True, preview=False) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "crossed markers" in captured.err


def test_fenced_code_markers_are_inert(tmp_path: Path, capsys) -> None:
    # bootstrap_project already embeds an example marker pair inside a fenced
    # code block in DOC-001's "Docs" section; a clean run proves it was never
    # counted as a real (duplicate) source marker.
    bootstrap_project(tmp_path)
    capsys.readouterr()
    assert maintenance(tmp_path, "install-version", check=True, preview=False) == 0
    assert capsys.readouterr().out.splitlines()[0].endswith("clean")


def test_malformed_marker_for_target_fails_closed(tmp_path: Path, capsys) -> None:
    bootstrap_project(tmp_path)
    capsys.readouterr()
    path = tmp_path / "plan" / "architecture" / "b.md"
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            "<!-- docsystem:managed target=install-version -->",
            '<!-- docsystem:managed target="install-version" -->',
        ),
        encoding="utf-8",
    )

    assert maintenance(tmp_path, "install-version", check=True, preview=False) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "malformed marker for target 'install-version'" in captured.err


def test_marker_outside_declared_section_fails_closed(tmp_path: Path, capsys) -> None:
    bootstrap_project(tmp_path)
    capsys.readouterr()
    _write(
        tmp_path,
        "architecture/b.md",
        "---\nid: DOC-002\nrevision: 1\n---\n\n# Doc B\n\n"
        "<!-- docsystem:managed target=install-version -->\n"
        "pip install docsystem==1.2.2\n"
        "<!-- /docsystem:managed target=install-version -->\n\n"
        '<a id="quickstart"></a>\n## Quickstart\n\nBody.\n',
    )
    assert maintenance(tmp_path, "install-version", check=True, preview=False) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "outside declared section" in captured.err


# --- CRLF / EOF preservation -------------------------------------------------


def test_crlf_source_document_is_normalized_like_the_rest_of_the_engine(
    tmp_path: Path, capsys
) -> None:
    """Every read path in the engine (`catalog.build_catalog`, `sections`)
    reads Markdown through Python's universal-newline text mode, which
    already translates CRLF/CR to LF before any content reaches this module.
    A CRLF-written source document therefore compares equal to its LF
    counterpart: this is the explicit, deterministic, engine-wide contract
    rather than a maintenance-specific normalization.
    """

    bootstrap_project(tmp_path)
    capsys.readouterr()
    a_path = tmp_path / "plan" / "architecture" / "a.md"
    a_path.write_bytes(a_path.read_text(encoding="utf-8").replace("\n", "\r\n").encode())

    assert maintenance(tmp_path, "install-version", check=True, preview=False) == 0
    out = capsys.readouterr().out
    assert out.splitlines()[0].endswith("clean")
    assert "occurrence\tDOC-002#quickstart\tcurrent\tclean" in out


def test_no_trailing_newline_at_eof_does_not_crash(tmp_path: Path, capsys) -> None:
    bootstrap_project(tmp_path)
    capsys.readouterr()
    b_path = tmp_path / "plan" / "architecture" / "b.md"
    text = b_path.read_text(encoding="utf-8")
    assert text.endswith("\n")
    b_path.write_text(text[:-1], encoding="utf-8")

    assert maintenance(tmp_path, "install-version", check=True, preview=False) == 0
    assert capsys.readouterr().out.splitlines()[0].endswith("clean")


# --- direct/projected equivalence and fallback ------------------------------


def test_direct_and_projected_output_are_byte_identical(tmp_path: Path, capsys) -> None:
    bootstrap_project(tmp_path, occurrence_line="pip install docsystem==1.0.0\n")
    capsys.readouterr()

    assert (
        maintenance(tmp_path, "install-version", check=False, preview=True, json_output=True)
        == 0
    )
    direct_stdout = capsys.readouterr().out

    config = load_config(tmp_path)
    write_projection(config, build_projection(build_catalog(config), config))

    assert (
        maintenance(tmp_path, "install-version", check=False, preview=True, json_output=True)
        == 0
    )
    captured = capsys.readouterr()
    assert captured.out == direct_stdout
    assert captured.err == ""


def test_corrupt_projection_falls_back_to_direct_with_one_diagnostic(
    tmp_path: Path, capsys
) -> None:
    bootstrap_project(tmp_path)
    config = load_config(tmp_path)
    write_projection(config, build_projection(build_catalog(config), config))
    capsys.readouterr()

    pointer = cache_root(config) / "current.json"
    pointer.write_text(
        '{"schema_version": 3, "generation": "not-a-real-generation"}', encoding="utf-8"
    )

    assert maintenance(tmp_path, "install-version", check=True, preview=False) == 0
    captured = capsys.readouterr()
    assert captured.out.splitlines()[0].endswith("clean")
    err_lines = captured.err.splitlines()
    assert len(err_lines) == 1
    assert any(line.startswith("WARNING:") for line in err_lines)
