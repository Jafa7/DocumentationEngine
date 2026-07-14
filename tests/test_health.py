import json
from pathlib import Path

from docsystem.cli import build_parser, graph_health, index_projection
from docsystem.config import CONFIG_FILENAME, DEFAULT_CONFIG, load_config
from docsystem.health import (
    HealthBoundary,
    HealthDocument,
    HealthEdge,
    HealthFacts,
    evaluate_graph_health,
)


def _project(tmp_path: Path) -> None:
    configured = DEFAULT_CONFIG.replace(
        "[areas]\n", '[areas]\nworkspace = "."\n'
    ).replace(
        "required_metadata = []\nreport_orphans = false",
        'hub_in_degree = 2\nmax_weak_components = 1\n'
        'required_metadata = ["type", "status"]\nreport_orphans = true',
    )
    (tmp_path / CONFIG_FILENAME).write_text(configured, encoding="utf-8")
    root = tmp_path / "plan"
    root.mkdir()
    (root / "README.md").write_text(
        "---\nid: DOC-001\nrevision: 1\ntype: index\nstatus: active\n"
        "depends_on: [DOC-002]\n---\n# Index\n\n[Target](target.md#details)\n"
        "[Missing](target.md#missing)\n",
        encoding="utf-8",
    )
    (root / "target.md").write_text(
        "---\nid: DOC-002\nrevision: 1\ntype: guide\nstatus: active\n---\n"
        "# Target\n\n<a id=\"details\"></a>\n## Details\n\nBody.\n",
        encoding="utf-8",
    )


def test_graph_health_policy_signals_are_advisory_and_deterministic(
    tmp_path: Path,
) -> None:
    configured = DEFAULT_CONFIG.replace(
        "required_metadata = []\nreport_orphans = false",
        'hub_in_degree = 2\nhub_out_degree = 2\nboundary_count = 1\n'
        'stale_pin_count = 1\nmax_weak_components = 1\n'
        'required_metadata = ["type", "status"]\nreport_orphans = true',
    )
    (tmp_path / CONFIG_FILENAME).write_text(configured, encoding="utf-8")
    facts = HealthFacts(
        documents=(
            HealthDocument("DOC-001", 1, None, None, 1),
            HealthDocument("DOC-002", 2, "guide", "active", 2),
            HealthDocument("DOC-003", 1, "guide", "active", 1),
            HealthDocument("DOC-004", 1, "guide", "active", 1),
        ),
        edges=(
            HealthEdge("DOC-001", "DOC-002", "depends_on", "authored", 1),
            HealthEdge("DOC-003", "DOC-002", "references", "observed"),
        ),
        boundaries=(
            HealthBoundary("DOC-001", "external"),
            HealthBoundary("DOC-001", "missing-anchor"),
        ),
    )
    report = evaluate_graph_health(facts, load_config(tmp_path))
    assert [signal.code for signal in report.signals] == [
        "boundary-concentration",
        "dead-reference",
        "high-in-degree",
        "missing-metadata",
        "orphan-document",
        "stale-pin-concentration",
        "weak-components",
    ]
    assert report.stale_pin_count == 1
    assert report.weak_component_sizes == (3, 1)
    assert report.orphan_documents == ("DOC-004",)


def test_graph_health_direct_and_projection_outputs_are_identical(
    tmp_path: Path, capsys
) -> None:
    _project(tmp_path)
    assert graph_health(tmp_path) == 0
    direct_text_capture = capsys.readouterr()
    direct_text = direct_text_capture.out
    assert "projection absent; using direct Markdown" in direct_text_capture.err
    assert graph_health(tmp_path, json_output=True) == 0
    direct_json_capture = capsys.readouterr()
    direct_json = direct_json_capture.out
    assert index_projection(tmp_path, write=True) == 0
    capsys.readouterr()
    assert graph_health(tmp_path) == 0
    assert capsys.readouterr().out == direct_text

    assert graph_health(tmp_path, json_output=True) == 0
    projected_json = capsys.readouterr()
    assert projected_json.out == direct_json
    payload = json.loads(projected_json.out)
    assert payload["metrics"]["documents"] == 2
    assert payload["metrics"]["edges_by_authority"] == {
        "authored": 1,
        "generated": 3,
        "observed": 1,
    }
    assert payload["metrics"]["boundaries_by_category"] == {"missing-anchor": 1}
    assert [signal["code"] for signal in payload["signals"]] == ["dead-reference"]
    assert projected_json.err == ""

    reference_shard = next(
        (tmp_path / ".docsystem" / "cache" / "generations").glob(
            "*/references/**/*.json"
        )
    )
    corrupted = json.loads(reference_shard.read_text(encoding="utf-8"))
    corrupted["id"] = "DOC-999"
    reference_shard.write_text(json.dumps(corrupted), encoding="utf-8")
    assert graph_health(tmp_path, json_output=True) == 0
    fallback = capsys.readouterr()
    assert fallback.out == direct_json
    assert "projection references shard invalid" in fallback.err


def test_graph_health_fails_closed_on_structural_graph_errors(
    tmp_path: Path, capsys
) -> None:
    configured = DEFAULT_CONFIG.replace(
        "[areas]\n", '[areas]\nworkspace = "."\n'
    )
    (tmp_path / CONFIG_FILENAME).write_text(configured, encoding="utf-8")
    root = tmp_path / "plan"
    root.mkdir()
    (root / "bad.md").write_text(
        "---\nid: DOC-001\nrevision: 1\ndepends_on: [DOC-999]\n---\n# Bad\n",
        encoding="utf-8",
    )
    assert graph_health(tmp_path, json_output=True) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "references unknown ID DOC-999" in captured.err


def test_graph_health_cli_parser_supports_json_and_workspace_selection() -> None:
    args = build_parser().parse_args(
        ["graph-health", "/project", "--json", "--source", "private"]
    )
    assert args.command == "graph-health"
    assert args.project == Path("/project")
    assert args.json_output is True
    assert args.workspace_source == "private"
