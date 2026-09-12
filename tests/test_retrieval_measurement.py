import json
import os
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "measure_retrieval_cost.py"


def _run(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=cwd,
        env=dict(os.environ, TMPDIR="/tmp", TMP="/tmp", TEMP="/tmp"),
        capture_output=True,
        text=True,
        check=False,
    )


def test_synthetic_retrieval_measurement_is_bounded_and_self_checking(
    tmp_path: Path,
) -> None:
    result = _run(
        [
            "--documents",
            "8",
            "--body-bytes",
            "256",
            "--graph-density",
            "2",
            "--context-depth",
            "2",
            "--repetitions",
            "1",
            "--json",
        ],
        tmp_path,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["schema_version"] == 1
    assert payload["corpus"]["documents"] == 8
    assert payload["semantic_equality"] == {
        "context_direct_vs_projection": True,
        "read_direct_vs_projection": True,
        "repeated_outputs_byte_identical": True,
    }
    assert len(payload["runs"]) == 10
    direct_read = next(
        item
        for item in payload["runs"]
        if item["scenario"] == "read"
        and item["serving"] == "direct"
        and item["run_kind"] == "first-process"
    )
    projected_read = next(
        item
        for item in payload["runs"]
        if item["scenario"] == "read"
        and item["serving"] == "projection"
        and item["run_kind"] == "first-process"
    )
    export = next(
        item
        for item in payload["runs"]
        if item["scenario"] == "export"
        and item["run_kind"] == "first-process"
    )
    assert direct_read["parsed_documents"] == 8
    assert projected_read["parsed_documents"] == 0
    assert projected_read["materialized_documents"] == 8
    assert direct_read["source_bytes_read"] == payload["corpus"]["source_bytes"]
    assert projected_read["source_bytes_read"] == payload["corpus"]["source_bytes"]
    assert export["source_bytes_read"] == 0
    assert export["response_bytes"] > 0
    assert str(tmp_path) not in result.stdout


def test_synthetic_retrieval_measurement_rejects_invalid_size(tmp_path: Path) -> None:
    result = _run(["--documents", "2", "--json"], tmp_path)

    assert result.returncode == 1
    assert result.stdout == ""
    assert "documents must be at least 3" in result.stderr
