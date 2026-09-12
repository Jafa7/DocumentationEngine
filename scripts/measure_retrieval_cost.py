#!/usr/bin/env python3
"""Measure Documentation Engine retrieval work on a synthetic corpus.

This developer benchmark creates no authored project data. It deliberately
labels first and repeated process runs without claiming control of the host's
filesystem cache.
"""

from __future__ import annotations

import argparse
import json
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass
class RunMeasurement:
    scenario: str
    serving: str
    run_kind: str
    wall_seconds: float
    peak_rss: int | None
    peak_rss_unit: str | None
    source_read_calls: int
    source_bytes_read: int
    derived_read_calls: int
    derived_bytes_read: int
    catalog_builds: int
    parsed_documents: int
    materialized_documents: int
    stdout_bytes: int
    stderr_bytes: int
    response_bytes: int


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--documents", type=int, default=120)
    parser.add_argument("--body-bytes", type=int, default=4096)
    parser.add_argument("--graph-density", type=int, default=2)
    parser.add_argument("--context-depth", type=int, default=2)
    parser.add_argument("--repetitions", type=int, default=2)
    parser.add_argument("--work-dir", type=Path)
    parser.add_argument("--keep", action="store_true")
    parser.add_argument("--json", action="store_true", dest="json_output")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--_scenario", choices=("read", "context", "export"))
    parser.add_argument("--_project", type=Path)
    parser.add_argument("--_generation")
    parser.add_argument("--_metrics", type=Path)
    parser.add_argument("--_artifact", type=Path)
    return parser


def _require_positive(name: str, value: int, *, minimum: int = 1) -> None:
    if value < minimum:
        raise ValueError(f"{name} must be at least {minimum}")


def _document_text(
    number: int, total: int, body_bytes: int, graph_density: int
) -> str:
    dependencies = [
        f"DOC-{candidate:04d}"
        for candidate in range(number + 1, min(total + 1, number + graph_density + 1))
    ]
    metadata = ["---", f"id: DOC-{number:04d}", "revision: 1"]
    if dependencies:
        metadata.append("depends_on: [" + ", ".join(dependencies) + "]")
    metadata.extend(("---", f"# Synthetic document {number}", "", "## Summary", ""))
    summary = "A deterministic synthetic summary used only for retrieval measurement."
    prefix = "\n".join([*metadata, summary, "", "## Details", ""])
    line = f"Synthetic detail line for document {number:04d}.\n"
    repetitions = max(1, (body_bytes - len(prefix.encode("utf-8"))) // len(line) + 1)
    return prefix + line * repetitions


def _create_corpus(
    project: Path, documents: int, body_bytes: int, graph_density: int
) -> dict[str, int]:
    from docsystem.config import DEFAULT_CONFIG

    docs = project / "plan"
    docs.mkdir(parents=True)
    config = DEFAULT_CONFIG.replace(
        "[areas]\n", '[areas]\nworkspace = "."\n'
    ).replace(
        "[provider]\n",
        '[provider]\nid = "synthetic-measurement"\nvisibility = "private"\n',
    )
    (project / ".docsystem.toml").write_text(config, encoding="utf-8")
    links = "\n".join(
        f"- [Document {number}](documents/doc-{number:04d}.md)"
        for number in range(2, documents + 1)
    )
    (docs / "README.md").write_text(
        "---\nid: DOC-0001\nrevision: 1\n---\n"
        "# Synthetic corpus\n\n## Catalog\n\n"
        f"{links}\n",
        encoding="utf-8",
    )
    document_dir = docs / "documents"
    document_dir.mkdir()
    for number in range(2, documents + 1):
        (document_dir / f"doc-{number:04d}.md").write_text(
            _document_text(number, documents, body_bytes, graph_density),
            encoding="utf-8",
        )
    sources = tuple(sorted(docs.rglob("*.md")))
    sizes = [path.stat().st_size for path in sources]
    return {
        "documents": len(sources),
        "source_bytes": sum(sizes),
        "minimum_document_bytes": min(sizes),
        "maximum_document_bytes": max(sizes),
    }


def _classify(path: Path, source_root: Path, derived_root: Path) -> str | None:
    try:
        resolved = path.resolve(strict=False)
    except OSError:
        return None
    if resolved.is_relative_to(source_root):
        return "source"
    if resolved.is_relative_to(derived_root):
        return "derived"
    return None


def _child_measure(args: argparse.Namespace) -> int:
    if not all((args._scenario, args._project, args._metrics)):
        raise ValueError("internal scenario arguments are incomplete")
    import docsystem.cli as cli
    import docsystem.retrieval as retrieval

    project = args._project.resolve()
    source_root = (project / "plan").resolve()
    derived_root = (project / ".docsystem").resolve()
    counts = {
        "source_read_calls": 0,
        "source_bytes_read": 0,
        "derived_read_calls": 0,
        "derived_bytes_read": 0,
        "catalog_builds": 0,
        "parsed_documents": 0,
        "materialized_documents": 0,
    }
    original_read_bytes = Path.read_bytes
    original_read_text = Path.read_text
    original_cli_build_catalog = cli.build_catalog
    original_retrieval_build_catalog = retrieval.build_catalog
    original_from_catalog = retrieval.views_from_catalog
    original_from_projection = retrieval._views_from_projection

    def record(path: Path, size: int) -> None:
        category = _classify(path, source_root, derived_root)
        if category is not None:
            counts[f"{category}_read_calls"] += 1
            counts[f"{category}_bytes_read"] += size

    def measured_read_bytes(path: Path) -> bytes:
        value = original_read_bytes(path)
        record(path, len(value))
        return value

    def measured_read_text(
        path: Path, encoding: str | None = None, errors: str | None = None
    ) -> str:
        value = original_read_text(path, encoding=encoding, errors=errors)
        record(path, len(value.encode(encoding or "utf-8", errors or "strict")))
        return value

    def measured_build_catalog(config):
        value = original_retrieval_build_catalog(config)
        counts["catalog_builds"] += 1
        counts["parsed_documents"] += len(value.documents)
        return value

    def measured_from_catalog(catalog):
        value = original_from_catalog(catalog)
        counts["materialized_documents"] += len(value[0])
        return value

    def measured_from_projection(projection):
        value = original_from_projection(projection)
        counts["materialized_documents"] += len(value[0])
        return value

    Path.read_bytes = measured_read_bytes
    Path.read_text = measured_read_text
    cli.build_catalog = measured_build_catalog
    retrieval.build_catalog = measured_build_catalog
    retrieval.views_from_catalog = measured_from_catalog
    retrieval._views_from_projection = measured_from_projection
    response_bytes = 0
    try:
        if args._scenario == "read":
            exit_code = cli.read_document(project, "DOC-0002", anchor="details")
        elif args._scenario == "context":
            exit_code = cli.context(
                project, "DOC-0002", depth=args.context_depth
            )
        else:
            if args._artifact is None or args._generation is None:
                raise ValueError("export scenario requires generation and artifact")
            exit_code = cli.provider_export_snapshot(
                project, args._generation, output=args._artifact
            )
            if args._artifact.is_file():
                response_bytes = args._artifact.stat().st_size
    finally:
        Path.read_bytes = original_read_bytes
        Path.read_text = original_read_text
        cli.build_catalog = original_cli_build_catalog
        retrieval.build_catalog = original_retrieval_build_catalog
        retrieval.views_from_catalog = original_from_catalog
        retrieval._views_from_projection = original_from_projection

    try:
        import resource

        peak_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        peak_unit = "KiB" if sys.platform.startswith("linux") else "platform-native"
    except ImportError:
        peak_rss = None
        peak_unit = None
    args._metrics.write_text(
        json.dumps(
            {
                **counts,
                "peak_rss": peak_rss,
                "peak_rss_unit": peak_unit,
                "response_bytes": response_bytes,
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return exit_code


def _run_one(
    *,
    project: Path,
    generation: str,
    scenario: str,
    serving: str,
    run_kind: str,
    context_depth: int,
    scratch: Path,
    index: int,
) -> tuple[RunMeasurement, bytes]:
    metrics = scratch / f"metrics-{serving}-{scenario}-{index}.json"
    artifact = scratch / f"artifact-{index}.json"
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--_scenario",
        scenario,
        "--_project",
        str(project),
        "--_generation",
        generation,
        "--_metrics",
        str(metrics),
        "--_artifact",
        str(artifact),
        "--context-depth",
        str(context_depth),
    ]
    started = time.perf_counter()
    process = subprocess.run(command, capture_output=True, check=False)
    wall = time.perf_counter() - started
    if process.returncode != 0:
        raise RuntimeError(
            f"{scenario}/{serving} failed ({process.returncode}): "
            + process.stderr.decode("utf-8", errors="replace")
        )
    child = json.loads(metrics.read_text(encoding="utf-8"))
    response_bytes = child["response_bytes"] or len(process.stdout)
    return (
        RunMeasurement(
            scenario=scenario,
            serving=serving,
            run_kind=run_kind,
            wall_seconds=round(wall, 6),
            peak_rss=child["peak_rss"],
            peak_rss_unit=child["peak_rss_unit"],
            source_read_calls=child["source_read_calls"],
            source_bytes_read=child["source_bytes_read"],
            derived_read_calls=child["derived_read_calls"],
            derived_bytes_read=child["derived_bytes_read"],
            catalog_builds=child["catalog_builds"],
            parsed_documents=child["parsed_documents"],
            materialized_documents=child["materialized_documents"],
            stdout_bytes=len(process.stdout),
            stderr_bytes=len(process.stderr),
            response_bytes=response_bytes,
        ),
        artifact.read_bytes() if scenario == "export" else process.stdout,
    )


def _git_state(repo: Path) -> dict[str, Any]:
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True
    )
    dirty = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=no"],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    return {
        "revision": revision.stdout.strip() if revision.returncode == 0 else None,
        "tracked_worktree_dirty": bool(dirty.stdout.strip()),
    }


def _measure(args: argparse.Namespace) -> dict[str, Any]:
    _require_positive("documents", args.documents, minimum=3)
    _require_positive("body-bytes", args.body_bytes, minimum=128)
    _require_positive("graph-density", args.graph_density, minimum=0)
    _require_positive("context-depth", args.context_depth, minimum=0)
    _require_positive("repetitions", args.repetitions)

    owned_temp = args.work_dir is None
    workspace = (
        Path(tempfile.mkdtemp(prefix="docsystem-retrieval-measurement-"))
        if owned_temp
        else args.work_dir.resolve()
    )
    project = workspace / "project"
    scratch = workspace / "runs"
    try:
        project.mkdir(parents=True)
        scratch.mkdir()
        corpus = _create_corpus(
            project, args.documents, args.body_bytes, args.graph_density
        )
        build = subprocess.run(
            [sys.executable, "-m", "docsystem", "index", str(project), "--write"],
            capture_output=True,
            text=True,
            check=False,
        )
        if build.returncode != 0:
            raise RuntimeError(f"projection build failed: {build.stderr}")
        pointer = project / ".docsystem" / "cache" / "current.json"
        generation = json.loads(pointer.read_text(encoding="utf-8"))["generation"]
        disabled_pointer = pointer.with_suffix(".benchmark-disabled")
        measurements: list[RunMeasurement] = []
        outputs: dict[tuple[str, str], bytes] = {}

        pointer.replace(disabled_pointer)
        for scenario in ("read", "context"):
            for index in range(args.repetitions + 1):
                result, output = _run_one(
                    project=project,
                    generation=generation,
                    scenario=scenario,
                    serving="direct",
                    run_kind="first-process" if index == 0 else "repeated-process",
                    context_depth=args.context_depth,
                    scratch=scratch,
                    index=index,
                )
                measurements.append(result)
                outputs.setdefault(("direct", scenario), output)
                if outputs[("direct", scenario)] != output:
                    raise RuntimeError(f"non-deterministic direct {scenario} output")
        disabled_pointer.replace(pointer)

        for scenario in ("read", "context", "export"):
            for index in range(args.repetitions + 1):
                result, output = _run_one(
                    project=project,
                    generation=generation,
                    scenario=scenario,
                    serving="projection",
                    run_kind="first-process" if index == 0 else "repeated-process",
                    context_depth=args.context_depth,
                    scratch=scratch,
                    index=index + 100,
                )
                measurements.append(result)
                outputs.setdefault(("projection", scenario), output)
                if outputs[("projection", scenario)] != output:
                    raise RuntimeError(f"non-deterministic projected {scenario} output")
        for scenario in ("read", "context"):
            if outputs[("direct", scenario)] != outputs[("projection", scenario)]:
                raise RuntimeError(f"direct/projection {scenario} output mismatch")

        return {
            "schema_version": 1,
            "environment": {
                "python": platform.python_version(),
                "platform": platform.platform(),
                "filesystem_cache": (
                    "uncontrolled; first-process is not a cold-filesystem claim"
                ),
                "peak_memory_method": (
                    "resource.getrusage(RUSAGE_SELF).ru_maxrss when available"
                ),
                **_git_state(Path(__file__).resolve().parents[1]),
            },
            "parameters": {
                "documents": args.documents,
                "body_bytes": args.body_bytes,
                "graph_density": args.graph_density,
                "context_depth": args.context_depth,
                "repeated_processes": args.repetitions,
            },
            "corpus": corpus,
            "generation": generation,
            "semantic_equality": {
                "read_direct_vs_projection": True,
                "context_direct_vs_projection": True,
                "repeated_outputs_byte_identical": True,
            },
            "measurement_scope": {
                "bytes_read": (
                    "full-file bytes read through pathlib read_bytes/read_text under "
                    "the synthetic source and derived-state roots"
                ),
                "wall_seconds": "fresh child process startup through exit",
            },
            "runs": [asdict(item) for item in measurements],
        }
    finally:
        if owned_temp and not args.keep:
            shutil.rmtree(workspace, ignore_errors=True)


def _render_text(result: dict[str, Any]) -> str:
    lines = [
        "scenario\tserving\trun\twall_s\tsource_B\tderived_B\tparsed\tmaterialized\tresponse_B"
    ]
    for item in result["runs"]:
        lines.append(
            f"{item['scenario']}\t{item['serving']}\t{item['run_kind']}\t"
            f"{item['wall_seconds']:.6f}\t{item['source_bytes_read']}\t"
            f"{item['derived_bytes_read']}\t{item['parsed_documents']}\t"
            f"{item['materialized_documents']}\t{item['response_bytes']}"
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    args = _parser().parse_args()
    if args._scenario is not None:
        return _child_measure(args)
    try:
        result = _measure(args)
    except (OSError, RuntimeError, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    output = (
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        if args.json_output
        else _render_text(result)
    )
    if args.output is None:
        sys.stdout.write(output)
    else:
        args.output.write_text(output, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
