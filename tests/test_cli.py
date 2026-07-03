from pathlib import Path

from docsystem.cli import catalog, doctor, initialize, show_config, validate
from docsystem.config import CONFIG_FILENAME


def test_init_creates_config_and_documentation_root(tmp_path: Path) -> None:
    assert initialize(tmp_path) == 0
    assert (tmp_path / CONFIG_FILENAME).is_file()
    assert (tmp_path / "plan").is_dir()


def test_init_does_not_overwrite_existing_config(tmp_path: Path) -> None:
    assert initialize(tmp_path) == 0
    original = (tmp_path / CONFIG_FILENAME).read_text(encoding="utf-8")
    assert initialize(tmp_path) == 1
    assert (tmp_path / CONFIG_FILENAME).read_text(encoding="utf-8") == original


def test_doctor_and_show_config_accept_initialized_project(tmp_path: Path) -> None:
    assert initialize(tmp_path) == 0
    assert doctor(tmp_path) == 0
    assert show_config(tmp_path) == 0
    assert catalog(tmp_path) == 0
    assert validate(tmp_path) == 0


def test_doctor_reports_unreachable_markdown(tmp_path: Path) -> None:
    assert initialize(tmp_path) == 0
    area = tmp_path / "plan" / "roadmap"
    area.mkdir()
    (area / "release.md").write_text("# Release\n", encoding="utf-8")

    assert doctor(tmp_path) == 1
    assert validate(tmp_path) == 1
