"""Deterministic Markdown ATX heading and section extraction."""

from __future__ import annotations

import re
from dataclasses import dataclass

ATX_HEADING_PATTERN = re.compile(
    r"^ {0,3}(#{1,6})[ \t]+(.+?)(?:[ \t]+#+[ \t]*)?$"
)
FENCE_PATTERN = re.compile(r"^ {0,3}(`{3,}|~{3,})")


@dataclass(frozen=True)
class MarkdownSection:
    """A stable, addressable section in a Markdown source."""

    title: str
    anchor: str
    level: int
    start_line: int
    end_line: int


def heading_anchor(title: str) -> str:
    """Create a deterministic GitHub-like Unicode heading anchor."""

    characters = [
        character.casefold()
        for character in title
        if character.isalnum() or character in {" ", "-", "_", "\t"}
    ]
    return re.sub(r"\s+", "-", "".join(characters)).strip("-")


def parse_sections(text: str) -> tuple[MarkdownSection, ...]:
    """Parse ATX headings, ignoring headings inside fenced code blocks."""

    lines = text.splitlines()
    headings: list[tuple[str, str, int, int]] = []
    anchor_counts: dict[str, int] = {}
    fence_character: str | None = None
    fence_length = 0

    for line_number, line in enumerate(lines, start=1):
        fence = FENCE_PATTERN.match(line)
        if fence:
            marker = fence.group(1)
            if fence_character is None:
                fence_character = marker[0]
                fence_length = len(marker)
            elif marker[0] == fence_character and len(marker) >= fence_length:
                fence_character = None
                fence_length = 0
            continue
        if fence_character is not None:
            continue
        match = ATX_HEADING_PATTERN.match(line)
        if match is None:
            continue
        title = match.group(2).strip()
        base_anchor = heading_anchor(title)
        occurrence = anchor_counts.get(base_anchor, 0)
        anchor_counts[base_anchor] = occurrence + 1
        anchor = base_anchor if occurrence == 0 else f"{base_anchor}-{occurrence}"
        headings.append((title, anchor, len(match.group(1)), line_number))

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
    return tuple(sections)


def extract_section(text: str, section: MarkdownSection) -> str:
    """Return one section including its nested subsections."""

    lines = text.splitlines()
    return "\n".join(lines[section.start_line - 1 : section.end_line]).rstrip() + "\n"


def extract_navigation(text: str, sections: tuple[MarkdownSection, ...]) -> str:
    """Return front matter, title and introduction before the first H2."""

    first_h2 = next((section for section in sections if section.level == 2), None)
    if first_h2 is None:
        return text if text.endswith("\n") else f"{text}\n"
    lines = text.splitlines()
    return "\n".join(lines[: first_h2.start_line - 1]).rstrip() + "\n"
