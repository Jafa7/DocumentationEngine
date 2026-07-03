"""Provider-neutral structured Markdown documentation engine."""

from docsystem.catalog import DependencyEdge, DependencyGraph, MarkdownCatalog
from docsystem.metadata import DocumentMetadata, MetadataReference
from docsystem.sections import MarkdownSection

__version__ = "0.1.0"

__all__ = [
    "DependencyEdge",
    "DependencyGraph",
    "DocumentMetadata",
    "MarkdownCatalog",
    "MarkdownSection",
    "MetadataReference",
]
