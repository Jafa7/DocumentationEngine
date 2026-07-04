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
