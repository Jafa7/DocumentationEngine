"""Deterministic Markdown ATX heading and section extraction."""

from __future__ import annotations

import re
from dataclasses import dataclass

ATX_HEADING_PATTERN = re.compile(
    r"^ {0,3}(#{1,6})[ \t]+(.+?)(?:[ \t]+#+[ \t]*)?$"
)
FENCE_PATTERN = re.compile(r"^ {0,3}(`{3,}|~{3,})")
EXPLICIT_ANCHOR_PATTERN = re.compile(
    r"^ {0,3}<a\s+(?:id|name)=([\"'])([^\"']+)\1\s*></a>\s*$",
    re.IGNORECASE,
)
ANCHOR_TAG_START_PATTERN = re.compile(r"^\s*<a\b", re.IGNORECASE)
ATTRIBUTE_NAME_PATTERN = re.compile(r"(?:^|\s)([^\s=/>]+)(?=\s|=|/|$)")


@dataclass(frozen=True)
class MarkdownSection:
    """A stable, addressable section in a Markdown source."""

    title: str
    anchor: str
    level: int
    start_line: int
    end_line: int


@dataclass(frozen=True)
class SectionParseResult:
    """Addressable sections plus deterministic parser diagnostics."""

    sections: tuple[MarkdownSection, ...]
    issues: tuple[str, ...]


def is_valid_anchor(value: str) -> bool:
    """Return whether a canonical anchor uses the supported stable syntax."""

    return bool(value) and value[0].isalnum() and all(
        character.isalnum() or character in "-_.:" for character in value
    )


def heading_anchor(title: str) -> str:
    """Create a deterministic GitHub-like Unicode heading anchor."""

    characters = [
        character.casefold()
        for character in title
        if character.isalnum() or character in {" ", "-", "_", "\t"}
    ]
    return re.sub(r"\s+", "-", "".join(characters)).strip("-")


def _is_explicit_anchor_candidate(line: str) -> bool:
    """Detect id/name attribute tokens without inspecting quoted values."""

    start = ANCHOR_TAG_START_PATTERN.match(line)
    if start is None:
        return False
    opening: list[str] = []
    quote: str | None = None
    for character in line[start.end() :]:
        if quote is not None:
            if character == quote:
                quote = None
            opening.append(" ")
        elif character in {'"', "'"}:
            quote = character
            opening.append(" ")
        elif character == ">":
            break
        else:
            opening.append(character)
    names = {
        match.group(1).casefold()
        for match in ATTRIBUTE_NAME_PATTERN.finditer("".join(opening))
    }
    return bool(names & {"id", "name"})


def parse_sections_result(text: str) -> SectionParseResult:
    """Parse ATX headings and explicit anchors without repairing ambiguity."""

    lines = text.splitlines()
    headings: list[tuple[str, str, int, int]] = []
    issues: list[str] = []
    anchor_counts: dict[str, int] = {}
    anchor_owners: dict[str, tuple[str, int]] = {}
    explicit_owners: dict[str, int] = {}
    pending_anchors: list[tuple[str, int]] = []
    fence_character: str | None = None
    fence_length = 0

    def orphan_pending() -> None:
        for anchor, anchor_line in pending_anchors:
            issues.append(
                f"orphaned explicit anchor {anchor!r} at line {anchor_line}; "
                "it must directly precede an ATX heading"
            )
        pending_anchors.clear()

    for line_number, line in enumerate(lines, start=1):
        fence = FENCE_PATTERN.match(line)
        if fence_character is None and fence:
            orphan_pending()
            marker = fence.group(1)
            if marker[0] == "`" and "`" in line[fence.end() :]:
                continue
            fence_character = marker[0]
            fence_length = len(marker)
            continue
        if fence_character is not None:
            if (
                fence is not None
                and fence.group(1)[0] == fence_character
                and len(fence.group(1)) >= fence_length
                and not line[fence.end() :].strip()
            ):
                fence_character = None
                fence_length = 0
            continue

        explicit_match = EXPLICIT_ANCHOR_PATTERN.match(line)
        if explicit_match is not None:
            value = explicit_match.group(2)
            if not is_valid_anchor(value):
                orphan_pending()
                issues.append(
                    f"malformed explicit anchor {value!r} at line {line_number}"
                )
                continue
            pending_anchors.append((value, line_number))
            continue
        if _is_explicit_anchor_candidate(line):
            orphan_pending()
            issues.append(f"malformed explicit anchor at line {line_number}")
            continue

        match = ATX_HEADING_PATTERN.match(line)
        if match is None:
            orphan_pending()
            continue
        title = match.group(2).strip()
        explicit = len(pending_anchors) == 1
        if len(pending_anchors) > 1:
            rendered = ", ".join(
                f"{anchor!r} at line {anchor_line}"
                for anchor, anchor_line in pending_anchors
            )
            issues.append(
                f"multiple explicit anchors before heading at line {line_number}: "
                f"{rendered}"
            )
        if explicit:
            anchor, anchor_line = pending_anchors[0]
        else:
            base_anchor = heading_anchor(title)
            occurrence = anchor_counts.get(base_anchor, 0)
            anchor_counts[base_anchor] = occurrence + 1
            anchor = (
                base_anchor if occurrence == 0 else f"{base_anchor}-{occurrence}"
            )
            anchor_line = line_number
        pending_anchors.clear()

        owner = anchor_owners.get(anchor)
        if explicit and anchor in explicit_owners:
            issues.append(
                f"duplicate explicit anchor {anchor!r} at line {anchor_line}; "
                f"first used at line {explicit_owners[anchor]}"
            )
        elif owner is not None:
            owner_kind, owner_line = owner
            current_kind = "explicit" if explicit else "generated"
            issues.append(
                f"anchor collision {anchor!r}: {owner_kind} anchor at line "
                f"{owner_line} and {current_kind} anchor at line {anchor_line}"
            )
        if explicit:
            explicit_owners.setdefault(anchor, anchor_line)
        anchor_owners.setdefault(
            anchor, ("explicit" if explicit else "generated", anchor_line)
        )
        headings.append((title, anchor, len(match.group(1)), line_number))
    orphan_pending()

    sections: list[MarkdownSection] = []
    for index, (title, anchor, level, start_line) in enumerate(headings):
        end_line = len(lines)
        for _, _, next_level, next_line in headings[index + 1 :]:
            if next_level <= level:
                end_line = next_line - 1
                break
        sections.append(
            MarkdownSection(title, anchor, level, start_line, end_line)
        )
    return SectionParseResult(tuple(sections), tuple(issues))


def parse_sections(text: str) -> tuple[MarkdownSection, ...]:
    """Parse addressable sections while preserving the original public API."""

    return parse_sections_result(text).sections


def extract_section(text: str, section: MarkdownSection) -> str:
    """Return one section including its nested subsections."""

    lines = text.splitlines()
    return "\n".join(lines[section.start_line - 1 : section.end_line]).rstrip() + "\n"


def navigation_issues(
    sections: tuple[MarkdownSection, ...], extend_through: tuple[str, ...]
) -> tuple[str, ...]:
    """Validate document-specific navigation extension anchors."""

    issues: list[str] = []
    for anchor in extend_through:
        for section in sections:
            if section.anchor == anchor and section.level != 2:
                issues.append(
                    f"navigation.extend_through anchor {anchor!r} resolves to "
                    f"H{section.level}, expected H2"
                )
    return tuple(issues)


def extract_navigation(
    text: str,
    sections: tuple[MarkdownSection, ...],
    extend_through: tuple[str, ...] = (),
) -> str:
    """Return the default prefix or extend it through configured H2 sections."""

    first_h2 = next((section for section in sections if section.level == 2), None)
    lines = text.splitlines()
    matching = [
        section
        for section in sections
        if section.level == 2 and section.anchor in extend_through
    ]
    if matching:
        end_line = max(section.end_line for section in matching)
        return "\n".join(lines[:end_line]) + "\n"
    if first_h2 is None:
        return text if text.endswith("\n") else f"{text}\n"
    return "\n".join(lines[: first_h2.start_line - 1]).rstrip() + "\n"
