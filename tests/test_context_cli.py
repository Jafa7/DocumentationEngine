from pathlib import Path

from docsystem.cli import build_parser, dependencies, doctor, read_document, validate
from docsystem.config import CONFIG_FILENAME, DEFAULT_CONFIG


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
