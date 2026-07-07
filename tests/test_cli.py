import json
from pathlib import Path

from docsystem.cli import build_parser, catalog, doctor, initialize, show_config, validate
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


def test_catalog_explain_is_deterministic_and_preserves_regular_output(
    tmp_path: Path, capsys
) -> None:
    assert initialize(tmp_path) == 0
    config_path = tmp_path / CONFIG_FILENAME
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace(
            "exclude = []", 'exclude = ["templates/*.md"]'
        ),
        encoding="utf-8",
    )
    root = tmp_path / "plan"
    architecture = root / "architecture"
    templates = root / "templates"
    architecture.mkdir()
    templates.mkdir()
    (architecture / "README.md").write_text(
        "---\nid: DOC-001\nrevision: 1\n---\n# Architecture\n",
        encoding="utf-8",
    )
    (templates / "draft.md").write_text("# Template\n", encoding="utf-8")
    (root / "orphan.md").write_text("# Orphan\n", encoding="utf-8")
    capsys.readouterr()

    assert catalog(tmp_path) == 0
    assert capsys.readouterr().out == "architecture\tarchitecture/README.md\n"

    assert catalog(tmp_path, explain=True) == 0
    assert capsys.readouterr().out == (
        "included\tarchitecture\tarchitecture/README.md\n"
        "unmapped\tno configured area\torphan.md\n"
        "excluded\ttemplates/*.md\ttemplates/draft.md\n"
    )
    args = build_parser().parse_args(["catalog", str(tmp_path), "--explain"])
    assert args.explain is True


def test_catalog_json_is_deterministic_and_machine_readable(
    tmp_path: Path, capsys
) -> None:
    assert initialize(tmp_path) == 0
    config_path = tmp_path / CONFIG_FILENAME
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace(
            "exclude = []", 'exclude = ["templates/*.md"]'
        ),
        encoding="utf-8",
    )
    root = tmp_path / "plan"
    architecture = root / "architecture"
    templates = root / "templates"
    architecture.mkdir()
    templates.mkdir()
    (architecture / "README.md").write_text(
        "---\nid: DOC-001\nrevision: 1\n---\n# Architecture\n",
        encoding="utf-8",
    )
    (templates / "draft.md").write_text("# Template\n", encoding="utf-8")
    (root / "orphan.md").write_text("# Orphan\n", encoding="utf-8")
    capsys.readouterr()

    assert catalog(tmp_path, json_output=True) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "schema_version": 1,
        "documents": [{"path": "architecture/README.md", "role": "architecture"}],
    }

    assert catalog(tmp_path, explain=True, json_output=True) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "schema_version": 1,
        "memberships": [
            {
                "state": "included",
                "path": "architecture/README.md",
                "role": "architecture",
                "reason": None,
            },
            {
                "state": "unmapped",
                "path": "orphan.md",
                "role": None,
                "reason": "no configured area",
            },
            {
                "state": "excluded",
                "path": "templates/draft.md",
                "role": None,
                "reason": "templates/*.md",
            },
        ],
    }

    args = build_parser().parse_args(["catalog", str(tmp_path), "--explain", "--json"])
    assert args.json_output is True


def test_doctor_and_validate_reject_unmapped_markdown(
    tmp_path: Path, capsys
) -> None:
    assert initialize(tmp_path) == 0
    (tmp_path / "plan" / "orphan.md").write_text("# Orphan\n", encoding="utf-8")
    capsys.readouterr()

    assert validate(tmp_path) == 1
    assert "Markdown is not mapped" in capsys.readouterr().err
    assert doctor(tmp_path) == 1
    assert "Markdown is not mapped" in capsys.readouterr().err
