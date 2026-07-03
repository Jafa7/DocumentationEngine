from pathlib import Path

from docsystem.cli import dependencies, read_document, validate
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


def test_read_document_reports_unknown_id_and_anchor(tmp_path: Path, capsys) -> None:
    configured_documents(tmp_path)

    assert read_document(tmp_path, "DOC-999") == 1
    assert "document ID not found: DOC-999" in capsys.readouterr().err
    assert read_document(tmp_path, "DOC-002", anchor="missing") == 1
    assert "anchor not found in DOC-002: missing" in capsys.readouterr().err


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
