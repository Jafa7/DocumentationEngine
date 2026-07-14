"""Read-only managed maintenance preview: markers, drift and diff.

This module implements the deterministic HTML-comment marker contract for
`RM-006`: one canonical `source` block owns authored bytes, and a bounded set
of declared `occurrences` may hold a `managed` replica. Only a `current`
occurrence is preview eligible; `historical`, `example`, `snapshot` and
`unmanaged` occurrences are visible, excluded evidence and are never diffed.
This module never edits Markdown, never reads Markdown itself (callers pass
already-resolved document text) and has no write authority. The public
contract is documented in `docs/agent-contract.md`.

Marker syntax::

    <!-- docsystem:source target=NAME -->
    ...canonical block...
    <!-- /docsystem:source target=NAME -->

    <!-- docsystem:managed target=NAME -->
    ...replica block...
    <!-- /docsystem:managed target=NAME -->

A marker must occupy its own line exactly. Markers inside fenced code are
inert (never matched), so documentation that shows the marker syntax as an
example is never mistaken for a real managed block.
"""

from __future__ import annotations

import difflib
import hashlib
import re
from dataclasses import dataclass

from docsystem.sections import FENCE_PATTERN, MarkdownSection

MARKER_PATTERN = re.compile(
    r"^<!--\s*(/?)docsystem:(source|managed)\s+target=([A-Za-z][A-Za-z0-9_-]*)\s*-->\s*$"
)
MARKER_CANDIDATE_PATTERN = re.compile(
    r"^\s*<!--\s*/?docsystem:(?:source|managed)\b.*$"
)

SOURCE = "source"
MANAGED = "managed"

CURRENT = "current"
CLEAN = "clean"
DRIFTED = "drifted"
EXCLUDED = "excluded"


@dataclass(frozen=True)
class MarkerSpan:
    """One well-formed marker pair, identified by its own marker line numbers."""

    kind: str  # "source" | "managed"
    start_line: int  # 1-based line of the opening marker itself
    end_line: int  # 1-based line of the closing marker itself


@dataclass(frozen=True)
class MarkerScanResult:
    """Every well-formed marker pair for one target, plus diagnostics."""

    spans: tuple[MarkerSpan, ...]
    issues: tuple[str, ...]


def _mask_fenced_lines(lines: tuple[str, ...]) -> tuple[str, ...]:
    """Blank fenced-code line contents so markers inside them are inert.

    Uses the same `sections.FENCE_PATTERN` fence definition as the section
    parser and `docsystem.graph`'s link masking, so all three agree on what
    counts as fenced code.
    """

    masked: list[str] = []
    fence_character: str | None = None
    fence_length = 0
    for line in lines:
        fence = FENCE_PATTERN.match(line)
        if fence_character is None and fence:
            marker = fence.group(1)
            if marker[0] == "`" and "`" in line[fence.end() :]:
                masked.append(line)
                continue
            fence_character = marker[0]
            fence_length = len(marker)
            masked.append("")
            continue
        if fence_character is not None:
            masked.append("")
            if (
                fence is not None
                and fence.group(1)[0] == fence_character
                and len(fence.group(1)) >= fence_length
                and not line[fence.end() :].strip()
            ):
                fence_character = None
                fence_length = 0
            continue
        masked.append(line)
    return tuple(masked)


def scan_markers(content: str, target: str) -> MarkerScanResult:
    """Scan one document for well-formed `source`/`managed` pairs of `target`.

    Only markers naming `target` participate; other targets' markers are
    ignored entirely, so unrelated maintenance targets never interfere with
    each other in the same document. Detection is a single stack-based pass:
    a self-nested pair (two `source` starts before either closes) collapses
    into two recorded spans of the same kind, which `resolve_marker` reports
    as a duplicate marker pair; a pair crossed with the other kind (for
    example `<source> <managed> </source> </managed>`) is reported as an
    explicit crossed-markers diagnostic plus a missing-end-marker diagnostic
    for whichever span never legally closed.
    """

    lines = content.splitlines()
    masked = _mask_fenced_lines(lines)
    stack: list[tuple[str, int]] = []
    spans: list[MarkerSpan] = []
    issues: list[str] = []
    for line_number, line in enumerate(masked, start=1):
        match = MARKER_PATTERN.match(line)
        if match is None:
            if (
                MARKER_CANDIDATE_PATTERN.match(line)
                and re.search(
                    rf"\btarget\s*=\s*[\"']?{re.escape(target)}[\"']?"
                    r"(?=\s|-->)",
                    line,
                )
            ):
                issues.append(
                    f"line {line_number}: malformed marker for target {target!r}"
                )
            continue
        is_end, kind, name = match.group(1) == "/", match.group(2), match.group(3)
        if name != target:
            continue
        if not is_end:
            stack.append((kind, line_number))
            continue
        if not stack:
            issues.append(
                f"line {line_number}: end marker for {kind}:{target} has no "
                "matching start"
            )
            continue
        top_kind, top_line = stack[-1]
        if top_kind == kind:
            stack.pop()
            spans.append(MarkerSpan(kind, top_line, line_number))
        else:
            issues.append(
                f"line {line_number}: crossed markers -- expected end for "
                f"{top_kind}:{target} opened at line {top_line}, found end "
                f"for {kind}:{target}"
            )
    for kind, line_number in stack:
        issues.append(f"line {line_number}: missing end marker for {kind}:{target}")
    return MarkerScanResult(tuple(spans), tuple(issues))


def resolve_marker(
    result: MarkerScanResult, kind: str
) -> tuple[MarkerSpan | None, tuple[str, ...]]:
    """Resolve exactly one marker span of `kind`, or return diagnostics.

    Any scan issue (missing end, crossed markers) makes the whole target
    ambiguous in this document, so it is reported before a per-kind count is
    even considered.
    """

    if result.issues:
        return None, result.issues
    matches = [span for span in result.spans if span.kind == kind]
    if not matches:
        return None, (f"no {kind} marker pair found",)
    if len(matches) > 1:
        return None, (f"duplicate {kind} marker pair ({len(matches)} found)",)
    return matches[0], ()


def span_within_section(span: MarkerSpan, section: MarkdownSection) -> bool:
    """Return whether a marker pair is fully contained by its declared section."""

    return section.start_line <= span.start_line and span.end_line <= section.end_line


def block_lines(content: str, span: MarkerSpan) -> tuple[str, ...]:
    """Return the exact interior lines between a marker pair, markers excluded.

    Uses `str.splitlines(keepends=True)` so original line terminators and a
    missing final newline are preserved byte-for-byte; the markers
    themselves are never part of the returned payload.
    """

    lines = tuple(content.splitlines(keepends=True))
    return lines[span.start_line : span.end_line - 1]


def block_text(content: str, span: MarkerSpan) -> str:
    """Return the exact block payload as one string, markers excluded."""

    return "".join(block_lines(content, span))


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def unified_block_diff(
    *,
    before: tuple[str, ...],
    after: tuple[str, ...],
    from_label: str,
    to_label: str,
) -> str:
    """Deterministic unified diff between two exact-byte block line sequences.

    `before`/`after` must come from `block_lines` (each line already carries
    its own original terminator), so the returned diff preserves the
    original newline style instead of normalizing it.
    """

    return "".join(
        difflib.unified_diff(
            list(before), list(after), fromfile=from_label, tofile=to_label
        )
    )
