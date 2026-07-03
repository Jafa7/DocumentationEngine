from pathlib import Path

import pytest

from docsystem.config import CONFIG_FILENAME, DEFAULT_CONFIG, load_config


def test_default_config_loads(tmp_path: Path) -> None:
    (tmp_path / CONFIG_FILENAME).write_text(DEFAULT_CONFIG, encoding="utf-8")
    config = load_config(tmp_path)
    assert config.documentation_root == tmp_path / "plan"
    assert config.areas["roadmap"].as_posix() == "roadmap"
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
