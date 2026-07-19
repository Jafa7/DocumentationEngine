import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from docsystem import __version__
from docsystem.cli import _configure_utf8_stream, build_parser
from docsystem.config import CONFIG_FILENAME, DEFAULT_CONFIG


def _legacy_locale_environment() -> dict[str, str]:
    environment = os.environ.copy()
    environment["PYTHONUTF8"] = "0"
    environment.pop("PYTHONIOENCODING", None)
    return environment


def _write_unicode_project(project: Path) -> None:
    (project / CONFIG_FILENAME).write_text(DEFAULT_CONFIG, encoding="utf-8")
    architecture = project / "plan" / "architecture"
    architecture.mkdir(parents=True)
    (architecture / "README.md").write_text(
        """\
---
id: DOC-001
revision: 1
---
# Привет
## Резюме
Исходный текст остаётся доступен полностью.
""",
        encoding="utf-8",
    )


def test_utf8_stream_configuration_overrides_a_legacy_encoding() -> None:
    buffer = io.BytesIO()
    stream = io.TextIOWrapper(buffer, encoding="cp1252", errors="strict")

    _configure_utf8_stream(stream)
    stream.write("Привет")
    stream.flush()

    assert stream.encoding.lower() == "utf-8"
    assert buffer.getvalue().decode("utf-8") == "Привет"


def test_global_version_option_uses_the_distribution_version(capsys) -> None:
    with pytest.raises(SystemExit) as raised:
        build_parser().parse_args(["--version"])

    assert raised.value.code == 0
    assert capsys.readouterr().out == f"docsystem {__version__}\n"


def test_module_cli_emits_utf8_json_without_python_utf8_mode(tmp_path: Path) -> None:
    _write_unicode_project(tmp_path)

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "docsystem",
            "context",
            "DOC-001",
            str(tmp_path),
            "--json",
        ],
        capture_output=True,
        env=_legacy_locale_environment(),
        check=False,
    )

    assert result.returncode == 0, result.stderr.decode("utf-8")
    payload = json.loads(result.stdout.decode("utf-8"))
    assert "# Привет" in payload["documents"][0]["navigation"]
    assert "резюме" in payload["documents"][0]["omitted_h2"]


def test_module_cli_emits_utf8_diagnostics_without_python_utf8_mode(
    tmp_path: Path,
) -> None:
    _write_unicode_project(tmp_path)

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "docsystem",
            "read",
            "DOC-001",
            str(tmp_path),
            "--anchor",
            "отсутствует",
        ],
        capture_output=True,
        env=_legacy_locale_environment(),
        check=False,
    )

    assert result.returncode == 1
    assert result.stdout == b""
    assert "отсутствует" in result.stderr.decode("utf-8")


def test_module_cli_version_is_available_without_a_subcommand() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "docsystem", "--version"],
        capture_output=True,
        env=_legacy_locale_environment(),
        check=False,
    )

    assert result.returncode == 0
    assert result.stdout.decode("utf-8").splitlines() == [
        f"docsystem {__version__}"
    ]
    assert result.stderr == b""
