"""Provider-neutral structured Markdown documentation engine."""

from docsystem.catalog import (
    CatalogMembership,
    DependencyEdge,
    DependencyGraph,
    MarkdownCatalog,
)
from docsystem.metadata import DocumentMetadata, MetadataReference
from docsystem.sections import MarkdownSection

__version__ = "0.3.0"

__all__ = [
    "CatalogMembership",
    "DependencyEdge",
    "DependencyGraph",
    "DocumentMetadata",
    "MarkdownCatalog",
    "MarkdownSection",
    "MetadataReference",
]
