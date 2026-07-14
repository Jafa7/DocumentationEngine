from pathlib import Path

import pytest

from docsystem.config import CONFIG_FILENAME, DEFAULT_CONFIG, load_config


def test_default_config_loads(tmp_path: Path) -> None:
    (tmp_path / CONFIG_FILENAME).write_text(DEFAULT_CONFIG, encoding="utf-8")
    config = load_config(tmp_path)
    assert config.documentation_root == tmp_path / "plan"
    assert config.areas["roadmap"].as_posix() == "roadmap"
    assert config.catalog_exclusions == ()
    assert config.navigation_extend_through == ()
    assert config.legacy_relation_mode == "strict"
    assert config.snapshot_document_types == ()
    assert config.projection_format == "sharded-json"


def test_area_paths_must_be_unique(tmp_path: Path) -> None:
    config = DEFAULT_CONFIG.replace('reviews = "reviews"', 'reviews = "roadmap"')
    (tmp_path / CONFIG_FILENAME).write_text(config, encoding="utf-8")
    with pytest.raises(ValueError, match="area paths must be unique"):
        load_config(tmp_path)


def test_parent_traversal_is_rejected(tmp_path: Path) -> None:
    config = DEFAULT_CONFIG.replace('root = "plan"', 'root = "../private"')
    (tmp_path / CONFIG_FILENAME).write_text(config, encoding="utf-8")
    with pytest.raises(ValueError, match="project-relative"):
        load_config(tmp_path)


def test_catalog_table_is_optional_for_existing_configuration(tmp_path: Path) -> None:
    config = DEFAULT_CONFIG.replace("[catalog]\nexclude = []\n\n", "")
    (tmp_path / CONFIG_FILENAME).write_text(config, encoding="utf-8")

    assert load_config(tmp_path).catalog_exclusions == ()


def test_catalog_must_be_a_table(tmp_path: Path) -> None:
    config = 'catalog = "invalid"\n' + DEFAULT_CONFIG.replace(
        "[catalog]\nexclude = []\n\n", ""
    )
    (tmp_path / CONFIG_FILENAME).write_text(config, encoding="utf-8")

    with pytest.raises(ValueError, match="catalog must be a table"):
        load_config(tmp_path)


def test_catalog_exclusions_are_ordered_and_normalized(tmp_path: Path) -> None:
    config = DEFAULT_CONFIG.replace(
        "exclude = []",
        'exclude = ["./templates//*-template.md", "resources/**/*.md"]',
    )
    (tmp_path / CONFIG_FILENAME).write_text(config, encoding="utf-8")

    assert load_config(tmp_path).catalog_exclusions == (
        "templates/*-template.md",
        "resources/**/*.md",
    )


def test_navigation_table_is_optional_and_preserves_anchor_order(tmp_path: Path) -> None:
    config = DEFAULT_CONFIG.replace(
        'extend_through = []', 'extend_through = ["резюме", "contents"]'
    )
    (tmp_path / CONFIG_FILENAME).write_text(config, encoding="utf-8")
    assert load_config(tmp_path).navigation_extend_through == (
        "резюме",
        "contents",
    )

    legacy = config.replace(
        '[navigation]\nextend_through = ["резюме", "contents"]\n\n', ""
    )
    (tmp_path / CONFIG_FILENAME).write_text(legacy, encoding="utf-8")
    assert load_config(tmp_path).navigation_extend_through == ()


def test_navigation_must_be_a_table(tmp_path: Path) -> None:
    config = 'navigation = "invalid"\n' + DEFAULT_CONFIG.replace(
        "[navigation]\nextend_through = []\n\n", ""
    )
    (tmp_path / CONFIG_FILENAME).write_text(config, encoding="utf-8")

    with pytest.raises(ValueError, match="navigation must be a table"):
        load_config(tmp_path)


def test_relations_table_is_optional_and_loads_adoption_policy(
    tmp_path: Path,
) -> None:
    config = DEFAULT_CONFIG.replace(
        'legacy_paths = "strict"',
        'legacy_paths = "resolve-with-warning"',
    ).replace(
        "snapshot_types = []",
        'snapshot_types = ["review", "experiment"]',
    )
    (tmp_path / CONFIG_FILENAME).write_text(config, encoding="utf-8")
    loaded = load_config(tmp_path)
    assert loaded.legacy_relation_mode == "resolve-with-warning"
    assert loaded.snapshot_document_types == ("review", "experiment")

    legacy = config.replace(
        '[relations]\nlegacy_paths = "resolve-with-warning"\n'
        'snapshot_types = ["review", "experiment"]\n\n',
        "",
    )
    (tmp_path / CONFIG_FILENAME).write_text(legacy, encoding="utf-8")
    loaded = load_config(tmp_path)
    assert loaded.legacy_relation_mode == "strict"
    assert loaded.snapshot_document_types == ()


@pytest.mark.parametrize(
    ("relations", "message"),
    [
        ("[[relations]]\n", "relations must be a table"),
        (
            '[relations]\nlegacy_paths = "accept"\nsnapshot_types = []\n',
            "relations.legacy_paths must be 'strict' or 'resolve-with-warning'",
        ),
        (
            '[relations]\nlegacy_paths = "strict"\nsnapshot_types = "review"\n',
            "relations.snapshot_types must be a list of non-empty strings",
        ),
        (
            '[relations]\nlegacy_paths = "strict"\nsnapshot_types = [""]\n',
            "relations.snapshot_types must be a list of non-empty strings",
        ),
        (
            '[relations]\nlegacy_paths = "strict"\n'
            'snapshot_types = ["review", "review"]\n',
            "relations.snapshot_types must be unique",
        ),
    ],
)
def test_invalid_relations_policy_is_rejected(
    tmp_path: Path, relations: str, message: str
) -> None:
    config = DEFAULT_CONFIG.replace(
        '[relations]\nlegacy_paths = "strict"\nsnapshot_types = []\n',
        relations,
    )
    (tmp_path / CONFIG_FILENAME).write_text(config, encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        load_config(tmp_path)


@pytest.mark.parametrize(
    ("replacement", "message"),
    [
        ('extend_through = ""', "navigation.extend_through must be a list"),
        (
            'extend_through = [""]',
            r"navigation\.extend_through\[0] must be a non-empty string",
        ),
        (
            "extend_through = [1]",
            r"navigation\.extend_through\[0] must be a non-empty string",
        ),
        (
            'extend_through = ["bad anchor"]',
            r"navigation\.extend_through\[0] has unsupported anchor syntax",
        ),
        (
            'extend_through = ["summary", "summary"]',
            "navigation.extend_through contains duplicate anchor 'summary'",
        ),
    ],
)
def test_invalid_navigation_configuration_is_rejected(
    tmp_path: Path, replacement: str, message: str
) -> None:
    config = DEFAULT_CONFIG.replace("extend_through = []", replacement)
    (tmp_path / CONFIG_FILENAME).write_text(config, encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        load_config(tmp_path)


@pytest.mark.parametrize(
    ("replacement", "message"),
    [
        ('exclude = ""', "catalog.exclude must be a list"),
        ('exclude = [""]', r"catalog\.exclude\[0] must be a non-empty string"),
        ('exclude = [1]', r"catalog\.exclude\[0] must be a non-empty string"),
        (
            'exclude = ["/templates/*.md"]',
            r"catalog\.exclude\[0] must be relative to the documentation root",
        ),
        (
            'exclude = ["../templates/*.md"]',
            r"catalog\.exclude\[0] must be relative to the documentation root",
        ),
        (
            'exclude = ["templates\\\\*.md"]',
            r"catalog\.exclude\[0] must use POSIX '/' separators",
        ),
        (
            'exclude = ["templates/*.md", "templates//*.md"]',
            "duplicate normalized pattern 'templates/\\*\\.md'",
        ),
    ],
)
def test_invalid_catalog_exclusions_are_rejected(
    tmp_path: Path, replacement: str, message: str
) -> None:
    config = DEFAULT_CONFIG.replace("exclude = []", replacement)
    (tmp_path / CONFIG_FILENAME).write_text(config, encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        load_config(tmp_path)


def test_maintenance_table_is_optional(tmp_path: Path) -> None:
    (tmp_path / CONFIG_FILENAME).write_text(DEFAULT_CONFIG, encoding="utf-8")
    assert load_config(tmp_path).maintenance_targets == ()


_MINIMAL_MAINTENANCE = """
[[maintenance]]
name = "install-version"
source_document = "DOC-001"
source_anchor = "install-block"

[[maintenance.occurrences]]
document = "DOC-002"
anchor = "quickstart"
role = "current"
"""

_EXTENDED_MAINTENANCE = """
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

[[maintenance]]
name = "second-target"
source_document = "DOC-004"
source_anchor = "canonical"

[[maintenance.occurrences]]
document = "DOC-005"
anchor = "replica"
role = "current"
"""


def test_minimal_maintenance_config_loads(tmp_path: Path) -> None:
    config = DEFAULT_CONFIG + _MINIMAL_MAINTENANCE
    (tmp_path / CONFIG_FILENAME).write_text(config, encoding="utf-8")
    loaded = load_config(tmp_path)
    assert len(loaded.maintenance_targets) == 1
    target = loaded.maintenance_targets[0]
    assert target.name == "install-version"
    assert target.source_document_id == "DOC-001"
    assert target.source_anchor == "install-block"
    assert len(target.occurrences) == 1
    assert target.occurrences[0].document_id == "DOC-002"
    assert target.occurrences[0].anchor == "quickstart"
    assert target.occurrences[0].role == "current"


def test_extended_maintenance_config_with_multiple_targets_loads(tmp_path: Path) -> None:
    config = DEFAULT_CONFIG + _EXTENDED_MAINTENANCE
    (tmp_path / CONFIG_FILENAME).write_text(config, encoding="utf-8")
    loaded = load_config(tmp_path)
    assert [target.name for target in loaded.maintenance_targets] == [
        "install-version",
        "second-target",
    ]
    assert len(loaded.maintenance_targets[0].occurrences) == 2
    assert loaded.maintenance_targets[0].occurrences[1].role == "historical"


@pytest.mark.parametrize(
    ("maintenance_toml", "message"),
    [
        ("maintenance = \"invalid\"\n", "maintenance must be a list of tables"),
        (
            "[[maintenance]]\n"
            'name = "t"\n'
            'source_document = "DOC-001"\n'
            'source_anchor = "install-block"\n'
            'extra_key = "x"\n\n'
            "[[maintenance.occurrences]]\n"
            'document = "DOC-002"\n'
            'anchor = "quickstart"\n'
            'role = "current"\n',
            r"maintenance\[0\] has unknown key\(s\): extra_key",
        ),
        (
            "[[maintenance]]\n"
            'name = ""\n'
            'source_document = "DOC-001"\n'
            'source_anchor = "install-block"\n\n'
            "[[maintenance.occurrences]]\n"
            'document = "DOC-002"\n'
            'anchor = "quickstart"\n'
            'role = "current"\n',
            r"maintenance\[0\]\.name must be a non-empty identifier-style string",
        ),
        (
            "[[maintenance]]\n"
            'name = "t"\n'
            'source_document = "DOC-001"\n'
            'source_anchor = "install-block"\n\n'
            "[[maintenance.occurrences]]\n"
            'document = "DOC-002"\n'
            'anchor = "quickstart"\n'
            'role = "current"\n\n'
            "[[maintenance]]\n"
            'name = "t"\n'
            'source_document = "DOC-004"\n'
            'source_anchor = "canonical"\n\n'
            "[[maintenance.occurrences]]\n"
            'document = "DOC-005"\n'
            'anchor = "replica"\n'
            'role = "current"\n',
            "maintenance target name is duplicated: 't'",
        ),
        (
            "[[maintenance]]\n"
            'name = "t"\n'
            'source_document = "DOC-001"\n'
            'source_anchor = "install-block"\n'
            "occurrences = []\n",
            r"maintenance\[0\]\.occurrences must be a non-empty list",
        ),
        (
            "[[maintenance]]\n"
            'name = "t"\n'
            'source_document = "not-an-id"\n'
            'source_anchor = "install-block"\n\n'
            "[[maintenance.occurrences]]\n"
            'document = "DOC-002"\n'
            'anchor = "quickstart"\n'
            'role = "current"\n',
            r"maintenance\[0\]\.source_document must use a configured stable ID prefix",
        ),
        (
            "[[maintenance]]\n"
            'name = "t"\n'
            'source_document = "DOC-001"\n'
            'source_anchor = "bad anchor"\n\n'
            "[[maintenance.occurrences]]\n"
            'document = "DOC-002"\n'
            'anchor = "quickstart"\n'
            'role = "current"\n',
            r"maintenance\[0\]\.source_anchor must use the supported stable anchor syntax",
        ),
        (
            "[[maintenance]]\n"
            'name = "t"\n'
            'source_document = "DOC-001"\n'
            'source_anchor = "install-block"\n\n'
            "[[maintenance.occurrences]]\n"
            'document = "DOC-002"\n'
            'anchor = "quickstart"\n'
            'role = "invented-role"\n',
            r"maintenance\[0\]\.occurrences\[0\]\.role must be one of:",
        ),
        (
            "[[maintenance]]\n"
            'name = "t"\n'
            'source_document = "DOC-001"\n'
            'source_anchor = "install-block"\n\n'
            "[[maintenance.occurrences]]\n"
            'document = "DOC-002"\n'
            'anchor = "quickstart"\n'
            'role = "current"\n'
            'extra = "x"\n',
            r"maintenance\[0\]\.occurrences\[0\] has unknown key\(s\): extra",
        ),
        (
            "[[maintenance]]\n"
            'name = "t"\n'
            'source_document = "DOC-001"\n'
            'source_anchor = "install-block"\n\n'
            "[[maintenance.occurrences]]\n"
            'document = "DOC-001"\n'
            'anchor = "install-block"\n'
            'role = "current"\n',
            r"maintenance\[0\]\.occurrences\[0\] overlaps the declared source address",
        ),
        (
            "[[maintenance]]\n"
            'name = "t"\n'
            'source_document = "DOC-001"\n'
            'source_anchor = "install-block"\n\n'
            "[[maintenance.occurrences]]\n"
            'document = "DOC-002"\n'
            'anchor = "quickstart"\n'
            'role = "current"\n\n'
            "[[maintenance.occurrences]]\n"
            'document = "DOC-002"\n'
            'anchor = "quickstart"\n'
            'role = "historical"\n',
            r"maintenance\[0\]\.occurrences\[1\] duplicates another occurrence at",
        ),
    ],
)
def test_invalid_maintenance_configuration_is_rejected(
    tmp_path: Path, maintenance_toml: str, message: str
) -> None:
    # A bare `key = value` line must precede every `[table]` header in TOML,
    # or it becomes a key of whatever table was last opened; only the
    # scalar-assignment case needs to be prepended for that reason.
    config = (
        maintenance_toml + DEFAULT_CONFIG
        if maintenance_toml.startswith("maintenance =")
        else DEFAULT_CONFIG + maintenance_toml
    )
    (tmp_path / CONFIG_FILENAME).write_text(config, encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        load_config(tmp_path)
