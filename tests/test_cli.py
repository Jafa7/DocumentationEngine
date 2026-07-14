import json
import shutil
from pathlib import Path

from docsystem.cli import (
    agent_instructions,
    build_parser,
    catalog,
    doctor,
    initialize,
    show_config,
    validate,
)
from docsystem.config import CONFIG_FILENAME, DEFAULT_CONFIG


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


_AGENT_INSTRUCTIONS_FIXTURE_CONFIG = (
    DEFAULT_CONFIG.replace(
        "[areas]\n"
        'foundation = "foundation"\n'
        'architecture = "architecture"\n'
        'decisions = "decisions"\n'
        'roadmap = "roadmap"\n'
        'scratch = "scratch"\n'
        'reviews = "reviews"\n'
        'experiments = "experiments"\n'
        'modules = "modules"\n',
        "[areas]\n"
        'workspace = "."\n'
        'guides = "guides"\n',
    )
    .replace(
        "[identifiers]\n"
        'document = "DOC"\n'
        'decision = "DEC"\n'
        'roadmap = "RM"\n',
        "[identifiers]\n"
        'document = "DOC"\n'
        'guide = "GUIDE"\n',
    )
    .replace('root = "plan"', 'root = "docs-root"')
    .replace('language = "en"', 'language = "fr"')
)


def test_agent_instructions_snapshot_reflects_custom_config(
    tmp_path: Path, capsys
) -> None:
    (tmp_path / CONFIG_FILENAME).write_text(
        _AGENT_INSTRUCTIONS_FIXTURE_CONFIG, encoding="utf-8"
    )
    capsys.readouterr()

    assert agent_instructions(tmp_path) == 0
    output = capsys.readouterr().out

    assert output == (
        "## Documentation with Documentation Engine\n"
        "\n"
        "This project uses `docsystem` for structured Markdown documentation "
        "rooted at `docs-root` (language: fr).\n"
        "\n"
        "Configured areas and identifier namespaces:\n"
        "\n"
        "- guides -> guides\n"
        "- workspace -> .\n"
        "- DOC (document)\n"
        "- GUIDE (guide)\n"
        "\n"
        "Agent rules:\n"
        "\n"
        "- Always pass the project root explicitly; do not rely on the "
        "current working directory matching the intended project.\n"
        f"- Start read-only with `docsystem readiness {tmp_path} --json` and "
        "follow its `next_command` field.\n"
        "- Prefer `--json` on commands that support it instead of parsing "
        "human-readable text output.\n"
        "- Without a configured view, expand context with `--depth`, "
        "`--include` or `--include-related` instead of assuming an omitted "
        "document or section is irrelevant.\n"
        "- Prefer `docsystem context ID PROJECT --compact --json` when fetching "
        "content: it emits each overlapping source range once while preserving "
        "every stable address, inclusion reason and omission in the manifest.\n"
        "- Use `docsystem graph-health PROJECT --json` for broad planning or "
        "graph diagnosis, not as mandatory overhead for every edit; metrics "
        "are facts and configured signals remain advisory.\n"
        "- If an additional read materially changes the plan, scope, decision, "
        "verification or result, finish the task and draft a sanitized "
        "`docsystem report context-gap`, then preserve its classification and "
        "report state in `docsystem finish`; ordinary precautionary expansion "
        "is not a product issue.\n"
        "- Never run `docsystem init`, `docsystem migrate --apply`, "
        "`docsystem index --write`, `docsystem maintenance --write` or "
        "`docsystem maintenance-recover` without explicit approval.\n"
        "- Before mutating ignored/local-only documentation state, follow "
        "this project's local backup policy if one exists.\n"
        "\n"
        "See `docs/agent-contract.md` in the Documentation Engine repository "
        "for the full agent contract.\n"
    )
    # No personal/machine-specific paths beyond the project root the caller
    # passed in.
    assert "/home/" not in output.replace(str(tmp_path), "")


def test_agent_instructions_is_deterministic_for_the_same_config(
    tmp_path: Path, capsys
) -> None:
    (tmp_path / CONFIG_FILENAME).write_text(
        _AGENT_INSTRUCTIONS_FIXTURE_CONFIG, encoding="utf-8"
    )
    capsys.readouterr()

    assert agent_instructions(tmp_path) == 0
    first = capsys.readouterr().out
    assert agent_instructions(tmp_path) == 0
    second = capsys.readouterr().out
    assert first == second


def test_agent_instructions_include_configured_context_views(
    tmp_path: Path, capsys
) -> None:
    config = _AGENT_INSTRUCTIONS_FIXTURE_CONFIG + """
[context.views.map]
tier = 1
delivery = "outline"
direction = "forward"
depth = 0
relations = []
layers = ["authored"]
"""
    (tmp_path / CONFIG_FILENAME).write_text(config, encoding="utf-8")

    assert agent_instructions(tmp_path) == 0
    output = capsys.readouterr().out
    assert "Configured purpose context views:" in output
    assert "- map: tier 1, outline, forward, depth 0, authored relations none" in output
    assert "Prefer the lowest configured `docsystem context --view NAME` tier" in output
    assert "inspect every `view_omissions` row" in output
    assert "Without a configured view, expand context" in output
    assert "Prefer `docsystem context ID PROJECT --compact --json`" in output
    assert "Use `docsystem graph-health PROJECT --json` for broad planning" in output

    assert show_config(tmp_path) == 0
    normalized = capsys.readouterr().out
    assert (
        "context.view.map=tier:1,delivery:outline,direction:forward,depth:0,"
        "relations:-,layers:authored\n"
    ) in normalized
    assert "graph_health.required_metadata=-\n" in normalized
    assert "graph_health.report_orphans=false\n" in normalized


def test_agent_instructions_json_carries_exact_text_output(
    tmp_path: Path, capsys
) -> None:
    assert initialize(tmp_path) == 0
    capsys.readouterr()

    assert agent_instructions(tmp_path) == 0
    text_output = capsys.readouterr().out

    assert agent_instructions(tmp_path, json_output=True) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == {"schema_version": 1, "text": text_output}

    args = build_parser().parse_args(["agent-instructions", str(tmp_path), "--json"])
    assert args.json_output is True


def test_agent_instructions_reports_missing_configuration(
    tmp_path: Path, capsys
) -> None:
    capsys.readouterr()

    assert agent_instructions(tmp_path) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.startswith("ERROR: configuration not found: ")


def test_agent_instructions_works_without_documentation_root(
    tmp_path: Path, capsys
) -> None:
    assert initialize(tmp_path) == 0
    shutil.rmtree(tmp_path / "plan")
    capsys.readouterr()

    assert agent_instructions(tmp_path) == 0
    output = capsys.readouterr().out
    assert output.startswith("## Documentation with Documentation Engine\n")


_INTAKE_CRITERION_CONFIG = """
[[intake.criteria]]
id = "idea-placement"
revision = 1
allowed_decisions = ["update-existing", "create-draft", "create-workstream"]
max_candidates = 8
safe_fallback = "blocked"
draft = { area = "architecture", type = "architecture", identifier = "document", width = 3 }
workstream = { area = "roadmap", type = "workstream", identifier = "roadmap", width = 3 }
"""

_ADMISSION_CRITERION_CONFIG = """
[[admission.criteria]]
id = "bounded-local"
revision = 1
max_autonomy = "A2"
allowed_actions = ["inspect", "plan", "edit-local", "run-checks"]
required_authorizations = ["edit-local"]
allowed_verification = ["focused", "full"]
max_risk = "medium"
max_targets = 12
required_sections = ["mandate", "boundaries", "review-gate"]
safe_fallback = "blocked"
"""

_WORKSTREAM_CRITERION_CONFIG = """
[[workstreams.criteria]]
id = "verified-delivery"
revision = 1
required_sections = ["mandate"]
required_evidence = ["checks", "review", "returns"]
max_attempts = 2
safe_fallback = "blocked"
"""

_HANDOFF_BULLET = (
    "- Immediately before an external executor acts, build the immutable "
    "packet with `docsystem execution-handoff ID PROJECT --admission "
    "REQUEST --json`, save that output as `PACKET`, then give the executor "
    "the exact `docsystem "
    "execution-handoff ID PROJECT --admission REQUEST --verify PACKET "
    "--json` re-check to run first; a failed or non-zero verification "
    "stops the executor before any edit, and neither the admission nor "
    "the packet is itself a permission grant."
)
_ADMISSION_BULLET = (
    "- Before implementation, validate the bounded workstream intent "
    "with `docsystem admission ID PROJECT --request REQUEST --json`; "
    "do not execute a blocked intent or treat authorization assertions "
    "as authenticated identity."
)
_EXECUTION_RESULT_BULLET = (
    "- When the packet carries `source_scope`, require a machine-readable "
    "`RESULT` from the runtime after execution and run `docsystem "
    "execution-result ID PROJECT --packet PACKET --result RESULT --json`; "
    "treat it as caller-declared inventory, stop on omitted scoped or "
    "out-of-scope paths, and do not replace an authoritative host diff with "
    "worker prose."
)
_INTAKE_BULLET = (
    "- Convert a new human idea into a bounded request, run `docsystem "
    "intake PROJECT --request REQUEST --json`, and follow only its "
    "explicit decision; a blocked result requires owner input."
)
_WORKSTREAM_BULLET = (
    "- For governed workstreams, inspect `docsystem criteria PROJECT "
    "--json`, validate `RECORD` with `docsystem workstream ID PROJECT "
    "--record RECORD --json`, require `ready_to_finish` to be true, then "
    "run `docsystem finish ID PROJECT --workstream-record RECORD --json`; "
    "never claim completion from an in-progress record."
)


def test_agent_instructions_omits_lifecycle_bullets_without_criteria(
    tmp_path: Path, capsys
) -> None:
    assert initialize(tmp_path) == 0
    capsys.readouterr()

    assert agent_instructions(tmp_path) == 0
    output = capsys.readouterr().out

    assert _INTAKE_BULLET not in output
    assert _ADMISSION_BULLET not in output
    assert _HANDOFF_BULLET not in output
    assert _EXECUTION_RESULT_BULLET not in output
    assert _WORKSTREAM_BULLET not in output


def test_agent_instructions_intake_only_adds_intake_bullet(
    tmp_path: Path, capsys
) -> None:
    assert initialize(tmp_path) == 0
    config_path = tmp_path / CONFIG_FILENAME
    config_path.write_text(
        config_path.read_text(encoding="utf-8") + _INTAKE_CRITERION_CONFIG,
        encoding="utf-8",
    )
    capsys.readouterr()

    assert agent_instructions(tmp_path) == 0
    output = capsys.readouterr().out

    assert _INTAKE_BULLET in output
    assert _ADMISSION_BULLET not in output
    assert _HANDOFF_BULLET not in output
    assert _EXECUTION_RESULT_BULLET not in output
    assert _WORKSTREAM_BULLET not in output


def test_agent_instructions_admission_only_adds_admission_and_handoff_bullets(
    tmp_path: Path, capsys
) -> None:
    assert initialize(tmp_path) == 0
    config_path = tmp_path / CONFIG_FILENAME
    config_path.write_text(
        config_path.read_text(encoding="utf-8") + _ADMISSION_CRITERION_CONFIG,
        encoding="utf-8",
    )
    capsys.readouterr()

    assert agent_instructions(tmp_path) == 0
    output = capsys.readouterr().out

    assert _INTAKE_BULLET not in output
    assert _ADMISSION_BULLET in output
    assert _HANDOFF_BULLET in output
    assert _EXECUTION_RESULT_BULLET in output
    assert _WORKSTREAM_BULLET not in output
    # The handoff bullet must directly follow the admission bullet.
    assert output.index(_ADMISSION_BULLET) < output.index(_HANDOFF_BULLET)


def test_agent_instructions_workstream_only_adds_workstream_bullet(
    tmp_path: Path, capsys
) -> None:
    assert initialize(tmp_path) == 0
    config_path = tmp_path / CONFIG_FILENAME
    config_path.write_text(
        config_path.read_text(encoding="utf-8") + _WORKSTREAM_CRITERION_CONFIG,
        encoding="utf-8",
    )
    capsys.readouterr()

    assert agent_instructions(tmp_path) == 0
    output = capsys.readouterr().out

    assert _INTAKE_BULLET not in output
    assert _ADMISSION_BULLET not in output
    assert _HANDOFF_BULLET not in output
    assert _EXECUTION_RESULT_BULLET not in output
    assert _WORKSTREAM_BULLET in output


def test_agent_instructions_full_lifecycle_orders_intake_admission_handoff_finish(
    tmp_path: Path, capsys
) -> None:
    assert initialize(tmp_path) == 0
    config_path = tmp_path / CONFIG_FILENAME
    config_path.write_text(
        config_path.read_text(encoding="utf-8")
        + _INTAKE_CRITERION_CONFIG
        + _ADMISSION_CRITERION_CONFIG
        + _WORKSTREAM_CRITERION_CONFIG,
        encoding="utf-8",
    )
    capsys.readouterr()

    assert agent_instructions(tmp_path) == 0
    output = capsys.readouterr().out

    for bullet in (
        _INTAKE_BULLET,
        _ADMISSION_BULLET,
        _HANDOFF_BULLET,
        _EXECUTION_RESULT_BULLET,
        _WORKSTREAM_BULLET,
    ):
        assert bullet in output

    assert (
        output.index(_INTAKE_BULLET)
        < output.index(_ADMISSION_BULLET)
        < output.index(_HANDOFF_BULLET)
        < output.index(_EXECUTION_RESULT_BULLET)
        < output.index(_WORKSTREAM_BULLET)
    )


def test_agent_instructions_json_carries_exact_text_with_full_lifecycle(
    tmp_path: Path, capsys
) -> None:
    assert initialize(tmp_path) == 0
    config_path = tmp_path / CONFIG_FILENAME
    config_path.write_text(
        config_path.read_text(encoding="utf-8")
        + _INTAKE_CRITERION_CONFIG
        + _ADMISSION_CRITERION_CONFIG
        + _WORKSTREAM_CRITERION_CONFIG,
        encoding="utf-8",
    )
    capsys.readouterr()

    assert agent_instructions(tmp_path) == 0
    text_output = capsys.readouterr().out

    assert agent_instructions(tmp_path, json_output=True) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == {"schema_version": 1, "text": text_output}
