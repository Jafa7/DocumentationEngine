from pathlib import Path

import pytest

from docsystem.config import CONFIG_FILENAME, DEFAULT_CONFIG, load_config


def test_default_config_loads(tmp_path: Path) -> None:
    (tmp_path / CONFIG_FILENAME).write_text(DEFAULT_CONFIG, encoding="utf-8")
    config = load_config(tmp_path)
    assert config.documentation_root == tmp_path / "plan"
    assert config.areas["roadmap"].as_posix() == "roadmap"
    assert config.catalog_exclusions == ()
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
