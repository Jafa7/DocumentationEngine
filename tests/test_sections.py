# ruff: noqa: RUF001

from docsystem.sections import (
    extract_navigation,
    extract_section,
    parse_sections,
    parse_sections_result,
)


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


def test_backtick_in_backtick_fence_info_string_does_not_open_fence() -> None:
    text = """\
# Root
```bad`info
## Expected heading
```
"""

    assert [section.title for section in parse_sections(text)] == [
        "Root",
        "Expected heading",
    ]


def test_explicit_anchor_overrides_generated_anchor_and_preserves_value() -> None:
    result = parse_sections_result(
        """\
<a id="Stable.ID"></a>
## First title
<a name='second:anchor'></a>
## Second title
"""
    )

    assert [section.anchor for section in result.sections] == [
        "Stable.ID",
        "second:anchor",
    ]
    assert result.issues == ()


def test_explicit_anchor_diagnostics_reject_ambiguity() -> None:
    result = parse_sections_result(
        """\
<a id="bad value"></a>
## Malformed
<a id="orphan"></a>

<a id="first"></a>
<a id="second"></a>
## Multiple
<a id="dup"></a>
## Duplicate one
<a name="dup"></a>
## Duplicate two
## collision
<a id="collision"></a>
## Explicit collision
"""
    )

    assert any(
        "malformed explicit anchor 'bad value' at line 1" in issue
        for issue in result.issues
    )
    assert any("orphaned explicit anchor 'orphan' at line 3" in issue for issue in result.issues)
    assert any(
        "multiple explicit anchors before heading at line 7" in issue
        for issue in result.issues
    )
    assert any("duplicate explicit anchor 'dup'" in issue for issue in result.issues)
    assert any("anchor collision 'collision'" in issue for issue in result.issues)


def test_explicit_anchor_inside_fence_is_ignored() -> None:
    result = parse_sections_result(
        """\
```html
<a id="hidden"></a>
## Hidden
```
## Visible
"""
    )

    assert [(section.title, section.anchor) for section in result.sections] == [
        ("Visible", "visible")
    ]
    assert result.issues == ()
