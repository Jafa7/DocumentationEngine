"""Deterministic, atomic migration of resolved legacy relation values.

This module rewrites only the exact YAML scalar spans for legacy relative
path values in `derived_from`, `depends_on`, `related` and `supersedes` that
`build_catalog` already classified as unambiguously resolved
(`MarkdownCatalog.relation_migrations`). Nothing else in the file — the rest
of the front matter, unknown fields, comments, quoting style of untouched
values and the document body — is changed.
"""

from __future__ import annotations

import re
import shutil
import tempfile
from dataclasses import dataclass, replace
from itertools import pairwise
from pathlib import Path, PurePosixPath

import yaml

from docsystem.catalog import (
    MarkdownCatalog,
    RelationMigration,
    build_catalog,
    validate_catalog,
)
from docsystem.config import ProjectConfig

_ANCHOR_PREFIX = re.compile(r"&\S+[ \t]+")


@dataclass(frozen=True)
class MigrationChange:
    """One deterministic legacy-value-to-stable-ID replacement."""

    path: PurePosixPath
    source_id: str
    relation: str
    old_value: str
    new_value: str


@dataclass(frozen=True)
class MigrationPlan:
    """A computed, not-yet-validated set of source-file rewrites."""

    changes: tuple[MigrationChange, ...]
    updated_contents: tuple[tuple[PurePosixPath, str], ...]
    original_contents: tuple[tuple[PurePosixPath, str], ...]
    blocking_issues: tuple[str, ...]


def _front_matter_bounds(content: str) -> tuple[int, int] | None:
    """Return the (start, end) line indices of the YAML front matter block."""

    lines = content.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return None
    closing = next(
        (index for index in range(1, len(lines)) if lines[index].strip() == "---"),
        None,
    )
    if closing is None:
        return None
    return 1, closing


def _rewrite_yaml_values(
    yaml_text: str, replacements: list[tuple[str, str, str]]
) -> str:
    """Replace only the exact scalar spans matching `(relation, old_value)`.

    A YAML anchor and every alias referencing it compose to the *same*
    node object, with marks pointing at the anchor's definition. Two
    relations that share one legacy value through such an anchor/alias
    therefore produce two logical replacements for one physical span; those
    are only safe to fold into a single physical rewrite when they agree on
    the replacement text, otherwise the physical span is rewritten twice
    against stale offsets and the file is corrupted.
    """

    node = yaml.compose(yaml_text, Loader=yaml.SafeLoader)
    if not isinstance(node, yaml.MappingNode):
        raise ValueError("front matter is not a YAML mapping")

    remaining = list(replacements)
    spans: list[tuple[int, int, str, str, str]] = []
    for key_node, value_node in node.value:
        if not isinstance(value_node, yaml.SequenceNode):
            continue
        relation = key_node.value
        for item in value_node.value:
            if not isinstance(item, yaml.ScalarNode):
                continue
            for index, (want_relation, want_old, want_new) in enumerate(remaining):
                if want_relation == relation and item.value == want_old:
                    start, end = item.start_mark.index, item.end_mark.index
                    # An anchored scalar's marks span its `&name ` tag too;
                    # skip it so the tag survives the rewrite and any alias
                    # elsewhere referencing it keeps resolving.
                    anchor_match = _ANCHOR_PREFIX.match(yaml_text, start, end)
                    if anchor_match is not None:
                        start = anchor_match.end()
                    spans.append((start, end, want_new, relation, want_old))
                    del remaining[index]
                    break
    if remaining:
        unmatched = ", ".join(
            f"{relation}={old!r}" for relation, old, _ in remaining
        )
        raise ValueError(f"could not locate legacy value(s) to rewrite: {unmatched}")

    spans_by_range: dict[tuple[int, int], list[tuple[int, int, str, str, str]]] = {}
    for span in spans:
        spans_by_range.setdefault((span[0], span[1]), []).append(span)

    deduped: list[tuple[int, int, str]] = []
    for (start, end), group in spans_by_range.items():
        new_values = {entry[2] for entry in group}
        if len(new_values) > 1:
            detail = ", ".join(
                f"{relation}={old!r}->{new!r}" for _, _, new, relation, old in group
            )
            raise ValueError(
                "unsupported YAML anchor/alias: one physical value is shared by "
                f"relations that resolve to different stable IDs ({detail}); "
                "migrate this document manually"
            )
        deduped.append((start, end, group[0][2]))

    ordered = sorted(deduped, key=lambda span: span[0])
    for previous, current in pairwise(ordered):
        if current[0] < previous[1]:
            raise ValueError(
                "unsupported YAML anchor/alias: overlapping physical replacement "
                f"spans at {previous[:2]} and {current[:2]}; migrate this "
                "document manually"
            )

    new_text = yaml_text
    for start, end, new_value in sorted(deduped, key=lambda span: span[0], reverse=True):
        new_text = new_text[:start] + new_value + new_text[end:]
    return new_text


def _apply_relation_rewrites(
    content: str, replacements: list[tuple[str, str, str]]
) -> str:
    bounds = _front_matter_bounds(content)
    if bounds is None:
        raise ValueError("YAML front matter is required")
    start, end = bounds
    lines = content.splitlines(keepends=True)
    yaml_text = "".join(lines[start:end])
    new_yaml_text = _rewrite_yaml_values(yaml_text, replacements)
    return "".join(lines[:start]) + new_yaml_text + "".join(lines[end:])


def build_migration_plan(config: ProjectConfig, catalog: MarkdownCatalog) -> MigrationPlan:
    """Compute (without writing) the rewrite for every resolved legacy value."""

    root = config.documentation_root
    paths_by_source_id = {
        document.metadata.document_id: document.path
        for document in catalog.documents
        if document.metadata is not None
    }
    grouped: dict[PurePosixPath, list[RelationMigration]] = {}
    for item in catalog.relation_migrations:
        path = paths_by_source_id[item.source_id]
        grouped.setdefault(path, []).append(item)

    changes: list[MigrationChange] = []
    updated_contents: list[tuple[PurePosixPath, str]] = []
    original_contents: list[tuple[PurePosixPath, str]] = []
    blocking_issues: list[str] = []
    for path in sorted(grouped, key=PurePosixPath.as_posix):
        items = grouped[path]
        original = (root / path).read_bytes().decode("utf-8")
        try:
            new_content = _apply_relation_rewrites(
                original,
                [(item.relation, item.value, item.target_id) for item in items],
            )
        except ValueError as error:
            blocking_issues.append(f"{path.as_posix()}: {error}")
            continue
        updated_contents.append((path, new_content))
        original_contents.append((path, original))
        changes.extend(
            MigrationChange(path, item.source_id, item.relation, item.value, item.target_id)
            for item in items
        )
    def _change_key(change: MigrationChange) -> tuple[str, str, str]:
        return (change.path.as_posix(), change.relation, change.old_value)

    return MigrationPlan(
        changes=tuple(sorted(changes, key=_change_key)),
        updated_contents=tuple(updated_contents),
        original_contents=tuple(original_contents),
        blocking_issues=tuple(blocking_issues),
    )


def validate_plan(config: ProjectConfig, plan: MigrationPlan) -> tuple[str, ...]:
    """Re-validate the whole catalog as if the plan were already applied.

    The check runs against a scratch copy of the documentation root so a
    preview or a failed validation never touches the real project files.
    """

    if plan.blocking_issues:
        return plan.blocking_issues
    if not plan.updated_contents:
        return ()

    doc_relative = config.documentation_root.relative_to(config.project_root)
    with tempfile.TemporaryDirectory(prefix="docsystem-migrate-") as staging:
        staging_root = Path(staging)
        staged_project_root = staging_root / "project"
        staged_documentation_root = staged_project_root / doc_relative
        shutil.copytree(config.documentation_root, staged_documentation_root)
        for path, new_content in plan.updated_contents:
            (staged_documentation_root / path).write_bytes(
                new_content.encode("utf-8")
            )
        staged_config = replace(
            config,
            project_root=staged_project_root,
            documentation_root=staged_documentation_root,
        )
        staged_catalog = build_catalog(staged_config)
        problems = [
            f"{issue.path.as_posix()}: {issue.message}"
            for issue in validate_catalog(staged_catalog, staged_config)
            if issue.severity != "warning"
        ]
        touched_ids = {change.source_id for change in plan.changes}
        remaining = [
            item
            for item in staged_catalog.relation_migrations
            if item.source_id in touched_ids
        ]
        if remaining:
            unresolved = ", ".join(
                f"{item.source_id}.{item.relation}={item.value!r}" for item in remaining
            )
            problems.append(
                f"migration is not idempotent; still resolvable after apply: {unresolved}"
            )
        return tuple(problems)


def apply_migration_plan(config: ProjectConfig, plan: MigrationPlan) -> None:
    """Write every planned change atomically, or leave the tree untouched.

    Every source file is first re-read and compared against the content the
    plan was computed from; a mismatch (for example, a concurrent edit
    between planning and applying) aborts before touching any file. Every new
    file is then first written to a sibling temporary file; only once every
    temporary file has been written successfully are the files renamed into
    place. If a rename fails partway through, already-renamed files are
    restored from their cached original bytes so a mid-migration OS failure
    cannot leave a partially migrated multi-file change.
    """

    root = config.documentation_root
    expected_originals = dict(plan.original_contents)
    originals: dict[Path, bytes] = {}
    for path, _ in plan.updated_contents:
        final_path = root / path
        raw = final_path.read_bytes()
        if raw.decode("utf-8") != expected_originals[path]:
            raise ValueError(
                f"{path.as_posix()} changed since the migration plan was computed; "
                "re-run migrate to recompute the plan"
            )
        originals[final_path] = raw

    temp_files: list[tuple[Path, Path]] = []
    try:
        for path, new_content in plan.updated_contents:
            final_path = root / path
            descriptor, temp_name = tempfile.mkstemp(
                prefix=f".{final_path.name}.", suffix=".tmp", dir=str(final_path.parent)
            )
            with open(descriptor, "wb") as handle:
                handle.write(new_content.encode("utf-8"))
            temp_path = Path(temp_name)
            shutil.copymode(final_path, temp_path)
            temp_files.append((temp_path, final_path))
    except OSError:
        for temp_path, _ in temp_files:
            temp_path.unlink(missing_ok=True)
        raise

    committed: list[Path] = []
    try:
        for temp_path, final_path in temp_files:
            temp_path.replace(final_path)
            committed.append(final_path)
    except OSError:
        for final_path in committed:
            final_path.write_bytes(originals[final_path])
        for temp_path, final_path in temp_files:
            if final_path not in committed:
                temp_path.unlink(missing_ok=True)
        raise
