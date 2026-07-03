"""Provider-neutral Markdown discovery and navigation validation."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit

from docsystem.config import ProjectConfig

INDEX_NAMES = frozenset({"readme.md", "index.md"})
INLINE_LINK_PATTERN = re.compile(r"(?<!!)\[[^\]]*]\(\s*(?:<([^>]+)>|([^) \t]+))")
REFERENCE_DEFINITION_PATTERN = re.compile(
    r"^\s{0,3}\[([^\]]+)]:\s*(?:<([^>]+)>|(\S+))", re.MULTILINE
)
REFERENCE_USE_PATTERN = re.compile(r"(?<!!)\[([^\]]+)]\[([^\]]*)]")


@dataclass(frozen=True)
class MarkdownDocument:
    """A Markdown source file assigned to a configured logical area."""

    role: str
    path: PurePosixPath
    links: tuple[PurePosixPath, ...]
    is_index: bool


@dataclass(frozen=True)
class MarkdownCatalog:
    """A deterministic snapshot of configured Markdown source files."""

    documents: tuple[MarkdownDocument, ...]


@dataclass(frozen=True)
class ValidationIssue:
    """A deterministic, human-readable catalog validation result."""

    path: PurePosixPath
    message: str


def _area_for(path: PurePosixPath, config: ProjectConfig) -> str | None:
    matches = [
        (len(area.parts), role)
        for role, area in config.areas.items()
        if path == area or area in path.parents
    ]
    if not matches:
        return None
    return max(matches)[1]


def _normalize_link(
    source: PurePosixPath, raw_target: str, documentation_root: Path
) -> PurePosixPath | None:
    target = urlsplit(raw_target)
    if target.scheme or target.netloc or not target.path:
        return None
    decoded = unquote(target.path).replace("\\", "/")
    candidate = (documentation_root / source.parent / decoded).resolve()
    try:
        relative = candidate.relative_to(documentation_root.resolve())
    except ValueError:
        return None
    if candidate.is_dir():
        for index in sorted(candidate.iterdir(), key=lambda item: item.name.lower()):
            if index.is_file() and index.name.lower() in INDEX_NAMES:
                relative = index.relative_to(documentation_root.resolve())
                break
    return PurePosixPath(relative.as_posix())


def _markdown_links(path: Path, relative: PurePosixPath, root: Path) -> tuple[PurePosixPath, ...]:
    content = path.read_text(encoding="utf-8")
    definitions = {
        match.group(1).casefold(): match.group(2) or match.group(3)
        for match in REFERENCE_DEFINITION_PATTERN.finditer(content)
    }
    inline_links = (
        match.group(1) or match.group(2)
        for match in INLINE_LINK_PATTERN.finditer(content)
    )
    reference_links = (
        definitions[label.casefold()]
        for match in REFERENCE_USE_PATTERN.finditer(content)
        if (label := match.group(2) or match.group(1)).casefold() in definitions
    )
    links = {
        normalized
        for raw in (*inline_links, *reference_links)
        if (normalized := _normalize_link(relative, raw, root)) is not None
        and normalized.suffix.lower() == ".md"
    }
    return tuple(sorted(links, key=PurePosixPath.as_posix))


def build_catalog(config: ProjectConfig) -> MarkdownCatalog:
    """Discover Markdown files below configured logical area paths."""

    root = config.documentation_root
    if not root.is_dir():
        return MarkdownCatalog(documents=())

    documents: list[MarkdownDocument] = []
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        if not path.is_file() or path.suffix.lower() != ".md":
            continue
        relative = PurePosixPath(path.relative_to(root).as_posix())
        role = _area_for(relative, config)
        if role is None:
            continue
        documents.append(
            MarkdownDocument(
                role=role,
                path=relative,
                links=_markdown_links(path, relative, root),
                is_index=path.name.lower() in INDEX_NAMES,
            )
        )
    return MarkdownCatalog(documents=tuple(documents))


def validate_reachability(
    catalog: MarkdownCatalog, config: ProjectConfig
) -> tuple[ValidationIssue, ...]:
    """Require every document to be listed by its nearest hierarchical index."""

    indexes_by_directory: dict[PurePosixPath, list[MarkdownDocument]] = {}
    for document in catalog.documents:
        if document.is_index:
            indexes_by_directory.setdefault(document.path.parent, []).append(document)

    issues: list[ValidationIssue] = []
    for directory, indexes in indexes_by_directory.items():
        if len(indexes) > 1:
            names = ", ".join(index.path.name for index in indexes)
            issues.append(
                ValidationIssue(directory, f"multiple navigation indexes found: {names}")
            )

    for document in catalog.documents:
        area_root = config.areas[document.role]
        directory = document.path.parent if not document.is_index else document.path.parent.parent
        nearest: MarkdownDocument | None = None
        while directory == area_root or area_root in directory.parents:
            indexes = indexes_by_directory.get(directory, [])
            if len(indexes) == 1:
                nearest = indexes[0]
                break
            if directory == area_root:
                break
            directory = directory.parent

        if nearest is None:
            if document.is_index and document.path.parent == area_root:
                continue
            issues.append(
                ValidationIssue(
                    document.path,
                    f"no README.md or index.md found between document and area '{document.role}'",
                )
            )
        elif document.path not in nearest.links:
            issues.append(
                ValidationIssue(
                    document.path,
                    f"not linked from nearest index {nearest.path.as_posix()}",
                )
            )

    return tuple(sorted(issues, key=lambda issue: (issue.path.as_posix(), issue.message)))
