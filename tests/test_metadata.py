from pathlib import Path, PurePosixPath

from docsystem.catalog import (
    build_catalog,
    build_dependency_graph,
    validate_metadata,
)
from docsystem.config import CONFIG_FILENAME, DEFAULT_CONFIG, load_config


def configured_project(tmp_path: Path) -> Path:
    (tmp_path / CONFIG_FILENAME).write_text(DEFAULT_CONFIG, encoding="utf-8")
    area = tmp_path / "plan" / "architecture"
    area.mkdir(parents=True)
    return area


def write_document(path: Path, metadata: str, body: str = "# Document\n") -> None:
    path.write_text(f"---\n{metadata}---\n\n{body}", encoding="utf-8")


def issue_messages(tmp_path: Path) -> list[str]:
    config = load_config(tmp_path)
    return [issue.message for issue in validate_metadata(build_catalog(config))]


def test_minimal_and_extended_metadata_build_a_dependency_graph(
    tmp_path: Path,
) -> None:
    area = configured_project(tmp_path)
    write_document(area / "README.md", "id: DOC-001\nrevision: 2\n")
    write_document(
        area / "design.md",
        """\
id: DOC-002
revision: 1
type: canonical
status: active
owner: documentation-team
derived_from: [DOC-001]
depends_on: [DOC-001]
related: [DOC-001]
supersedes: [DOC-001]
validated_against: [DOC-001@2]
""",
    )

    catalog = build_catalog(load_config(tmp_path))
    design = catalog.documents[1]
    assert design.metadata is not None
    assert design.metadata.document_type == "canonical"
    assert design.metadata.additional_fields == (("owner", "documentation-team"),)
    assert validate_metadata(catalog) == ()
    assert [
        (edge.relation, edge.source_id, edge.target_id, edge.expected_revision)
        for edge in build_dependency_graph(catalog).edges
    ] == [
        ("depends_on", "DOC-002", "DOC-001", None),
        ("derived_from", "DOC-002", "DOC-001", None),
        ("related", "DOC-002", "DOC-001", None),
        ("supersedes", "DOC-002", "DOC-001", None),
        ("validated_against", "DOC-002", "DOC-001", 2),
    ]


def test_missing_and_malformed_front_matter_remain_in_catalog(tmp_path: Path) -> None:
    area = configured_project(tmp_path)
    (area / "README.md").write_text("# Missing\n", encoding="utf-8")
    (area / "broken.md").write_text("---\nid: [\n---\n# Broken\n", encoding="utf-8")

    catalog = build_catalog(load_config(tmp_path))

    assert [document.path for document in catalog.documents] == [
        PurePosixPath("architecture/README.md"),
        PurePosixPath("architecture/broken.md"),
    ]
    messages = [issue.message for issue in validate_metadata(catalog)]
    assert "YAML front matter is required" in messages
    assert any(message.startswith("invalid YAML front matter") for message in messages)


def test_invalid_id_revision_and_duplicate_id_are_reported(tmp_path: Path) -> None:
    area = configured_project(tmp_path)
    write_document(area / "README.md", "id: BAD-001\nrevision: 0\n")
    write_document(area / "one.md", "id: DOC-001\nrevision: 1\n")
    write_document(area / "two.md", "id: DOC-001\nrevision: 2\n")

    messages = issue_messages(tmp_path)

    assert "metadata.id must use a configured stable ID prefix" in messages
    assert "metadata.revision must be a positive integer" in messages
    assert sum(message.startswith("duplicate document ID DOC-001") for message in messages) == 2


def test_invalid_unknown_self_duplicate_and_stale_references_are_reported(
    tmp_path: Path,
) -> None:
    area = configured_project(tmp_path)
    write_document(area / "README.md", "id: DOC-001\nrevision: 2\n")
    write_document(
        area / "design.md",
        """\
id: DOC-002
revision: 1
derived_from: [../legacy/source.md]
depends_on: [DOC-002, DOC-999, DOC-001, DOC-001]
related: DOC-001
validated_against: [DOC-001@1, DOC-001@1, DOC-001@0, invalid]
""",
    )

    messages = issue_messages(tmp_path)

    assert (
        "metadata.derived_from entry '../legacy/source.md' must use a configured "
        "stable ID"
    ) in messages
    assert "metadata.depends_on cannot reference its own ID" in messages
    assert "metadata.depends_on references unknown ID DOC-999" in messages
    assert "metadata.depends_on contains duplicate reference DOC-001" in messages
    assert "metadata.related must be a list" in messages
    assert any("DOC-001@1 is stale" in message for message in messages)
    assert "metadata.validated_against contains duplicate reference DOC-001@1" in messages
    assert "metadata.validated_against revisions must be positive" in messages
    assert "metadata.validated_against entries must use ID@revision" in messages


def test_stale_pin_is_a_non_blocking_diagnostic(tmp_path: Path) -> None:
    area = configured_project(tmp_path)
    write_document(area / "README.md", "id: DOC-001\nrevision: 2\n")
    write_document(
        area / "review.md",
        "id: DOC-002\nrevision: 1\ntype: review\nvalidated_against: [DOC-001@1]\n",
    )

    issues = validate_metadata(build_catalog(load_config(tmp_path)))

    assert len(issues) == 1
    assert issues[0].severity == "warning"
    assert "pin DOC-001@1 is stale" in issues[0].message
