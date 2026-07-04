from pathlib import Path, PurePosixPath

from docsystem.catalog import (
    build_catalog,
    validate_catalog,
    validate_membership,
    validate_reachability,
)
from docsystem.config import CONFIG_FILENAME, DEFAULT_CONFIG, ProjectConfig, load_config


def configured_project(tmp_path: Path) -> tuple[Path, ProjectConfig]:
    (tmp_path / CONFIG_FILENAME).write_text(DEFAULT_CONFIG, encoding="utf-8")
    documentation_root = tmp_path / "plan"
    documentation_root.mkdir()
    return documentation_root, load_config(tmp_path)


def test_catalog_assigns_documents_to_configured_roles(tmp_path: Path) -> None:
    root, config = configured_project(tmp_path)
    roadmap = root / "roadmap"
    roadmap.mkdir()
    (roadmap / "README.md").write_text("[Release](release.md)\n", encoding="utf-8")
    (roadmap / "release.md").write_text("# Release\n", encoding="utf-8")
    (root / "unconfigured.md").write_text("# Outside configured areas\n", encoding="utf-8")

    catalog = build_catalog(config)

    assert [(document.role, document.path) for document in catalog.documents] == [
        ("roadmap", PurePosixPath("roadmap/README.md")),
        ("roadmap", PurePosixPath("roadmap/release.md")),
    ]
    assert validate_reachability(catalog, config) == ()


def test_catalog_uses_configured_area_path_instead_of_role_name(tmp_path: Path) -> None:
    custom_config = DEFAULT_CONFIG.replace('roadmap = "roadmap"', 'roadmap = "delivery/plans"')
    (tmp_path / CONFIG_FILENAME).write_text(custom_config, encoding="utf-8")
    area = tmp_path / "plan" / "delivery" / "plans"
    area.mkdir(parents=True)
    (area / "README.md").write_text(
        "[Release][current]\n\n[current]: release.md\n", encoding="utf-8"
    )
    (area / "release.md").write_text("# Release\n", encoding="utf-8")
    config = load_config(tmp_path)

    catalog = build_catalog(config)

    assert {document.role for document in catalog.documents} == {"roadmap"}
    assert validate_reachability(catalog, config) == ()


def test_more_specific_area_wins_when_configured_paths_overlap(tmp_path: Path) -> None:
    custom_config = DEFAULT_CONFIG.replace(
        'architecture = "architecture"', 'architecture = "modules/shared"'
    )
    (tmp_path / CONFIG_FILENAME).write_text(custom_config, encoding="utf-8")
    nested = tmp_path / "plan" / "modules" / "shared"
    nested.mkdir(parents=True)
    (nested / "README.md").write_text("# Shared architecture\n", encoding="utf-8")

    catalog = build_catalog(load_config(tmp_path))

    assert [(document.role, document.path.as_posix()) for document in catalog.documents] == [
        ("architecture", "modules/shared/README.md")
    ]


def test_nested_index_must_be_linked_from_parent_index(tmp_path: Path) -> None:
    root, config = configured_project(tmp_path)
    roadmap = root / "roadmap"
    nested = roadmap / "releases"
    nested.mkdir(parents=True)
    (roadmap / "README.md").write_text("# Roadmap\n", encoding="utf-8")
    (nested / "README.md").write_text("[Release](v1.md)\n", encoding="utf-8")
    (nested / "v1.md").write_text("# Version 1\n", encoding="utf-8")

    issues = validate_reachability(build_catalog(config), config)

    assert [(issue.path.as_posix(), issue.message) for issue in issues] == [
        (
            "roadmap/releases/README.md",
            "not linked from nearest index roadmap/README.md",
        )
    ]


def test_document_without_index_is_reported(tmp_path: Path) -> None:
    root, config = configured_project(tmp_path)
    decisions = root / "decisions"
    decisions.mkdir()
    (decisions / "DEC-001.md").write_text("# Decision\n", encoding="utf-8")

    issues = validate_reachability(build_catalog(config), config)

    assert len(issues) == 1
    assert issues[0].path == PurePosixPath("decisions/DEC-001.md")
    assert "no README.md or index.md" in issues[0].message


def test_external_and_image_links_do_not_create_navigation_edges(tmp_path: Path) -> None:
    root, config = configured_project(tmp_path)
    area = root / "architecture"
    area.mkdir()
    (area / "README.md").write_text(
        "![Diagram](design.md)\n[Website](https://example.com/design.md)\n",
        encoding="utf-8",
    )
    (area / "design.md").write_text("# Design\n", encoding="utf-8")

    issues = validate_reachability(build_catalog(config), config)

    assert len(issues) == 1
    assert issues[0].path == PurePosixPath("architecture/design.md")


def test_catalog_classifies_included_excluded_and_unmapped_markdown(
    tmp_path: Path,
) -> None:
    config_text = DEFAULT_CONFIG.replace(
        "exclude = []",
        'exclude = ["templates/*.md", "templates/*-template.md"]',
    )
    (tmp_path / CONFIG_FILENAME).write_text(config_text, encoding="utf-8")
    root = tmp_path / "plan"
    architecture = root / "architecture"
    templates = root / "templates"
    architecture.mkdir(parents=True)
    templates.mkdir()
    (architecture / "README.md").write_text(
        "---\nid: DOC-001\nrevision: 1\n---\n# Architecture\n",
        encoding="utf-8",
    )
    (templates / "broken-template.md").write_bytes(b"\xffnot UTF-8")
    (root / "unmapped.md").write_text("# Unmapped\n", encoding="utf-8")

    catalog = build_catalog(load_config(tmp_path))

    assert [
        (
            membership.state,
            membership.role,
            membership.reason,
            membership.path.as_posix(),
        )
        for membership in catalog.memberships
    ] == [
        ("included", "architecture", None, "architecture/README.md"),
        (
            "excluded",
            None,
            "templates/*.md",
            "templates/broken-template.md",
        ),
        ("unmapped", None, "no configured area", "unmapped.md"),
    ]
    assert [document.path.as_posix() for document in catalog.documents] == [
        "architecture/README.md"
    ]
    assert [issue.path.as_posix() for issue in validate_membership(catalog)] == [
        "unmapped.md"
    ]
    assert all(
        issue.path != PurePosixPath("templates/broken-template.md")
        for issue in validate_catalog(catalog, load_config(tmp_path))
    )


def test_root_area_is_fallback_and_root_index_participates_in_reachability(
    tmp_path: Path,
) -> None:
    config_text = DEFAULT_CONFIG.replace(
        "[areas]\n", '[areas]\nworkspace = "."\n'
    ).replace("exclude = []", 'exclude = ["draft.md"]')
    (tmp_path / CONFIG_FILENAME).write_text(config_text, encoding="utf-8")
    root = tmp_path / "plan"
    modules = root / "modules"
    notes = root / "notes"
    modules.mkdir(parents=True)
    notes.mkdir()
    (root / "README.md").write_text(
        """\
---
id: DOC-001
revision: 1
---
# Workspace
[Guide](guide.md)
[Topic](notes/topic.md)
""",
        encoding="utf-8",
    )
    (root / "guide.md").write_text(
        "---\nid: DOC-002\nrevision: 1\n---\n# Guide\n", encoding="utf-8"
    )
    (notes / "topic.md").write_text(
        "---\nid: DOC-003\nrevision: 1\n---\n# Topic\n", encoding="utf-8"
    )
    (modules / "README.md").write_text(
        "---\nid: DOC-010\nrevision: 1\n---\n# Modules\n", encoding="utf-8"
    )
    (root / "draft.md").write_text("---\nid: [\n---\n", encoding="utf-8")
    config = load_config(tmp_path)

    catalog = build_catalog(config)

    assert {
        membership.path.as_posix(): (membership.state, membership.role)
        for membership in catalog.memberships
    } == {
        "README.md": ("included", "workspace"),
        "draft.md": ("excluded", None),
        "guide.md": ("included", "workspace"),
        "modules/README.md": ("included", "modules"),
        "notes/topic.md": ("included", "workspace"),
    }
    assert validate_catalog(catalog, config) == ()


def test_unmapped_markdown_is_a_validation_error(tmp_path: Path) -> None:
    root, config = configured_project(tmp_path)
    (root / "orphan.md").write_text("# Orphan\n", encoding="utf-8")

    issues = validate_catalog(build_catalog(config), config)

    assert [(issue.path.as_posix(), issue.message) for issue in issues] == [
        (
            "orphan.md",
            "Markdown is not mapped to a configured area or catalog exclusion",
        )
    ]
