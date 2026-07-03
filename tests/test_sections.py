# ruff: noqa: RUF001

from docsystem.sections import extract_navigation, extract_section, parse_sections


def test_sections_ignore_fences_and_disambiguate_unicode_anchors() -> None:
    text = """\
---
id: DOC-001
revision: 1
---

# Обзор

Введение.

```markdown
## Не раздел
```

## Работа ИИ

Текст.

### Детали

Подробности.

## Работа ИИ

Продолжение.
"""

    sections = parse_sections(text)

    assert [(section.title, section.anchor) for section in sections] == [
        ("Обзор", "обзор"),
        ("Работа ИИ", "работа-ии"),
        ("Детали", "детали"),
        ("Работа ИИ", "работа-ии-1"),
    ]
    assert extract_section(text, sections[1]) == (
        "## Работа ИИ\n\nТекст.\n\n### Детали\n\nПодробности.\n"
    )
    assert extract_navigation(text, sections) == (
        "---\nid: DOC-001\nrevision: 1\n---\n\n# Обзор\n\nВведение.\n\n"
        "```markdown\n## Не раздел\n```\n"
    )


def test_fence_closes_only_with_compatible_marker_and_no_info_string() -> None:
    text = """\
````text
```python
## Hidden after shorter marker
````
## Visible after long close

```text
```python
## Hidden after marker with info
```
## Visible after plain close
"""

    assert [section.title for section in parse_sections(text)] == [
        "Visible after long close",
        "Visible after plain close",
    ]
