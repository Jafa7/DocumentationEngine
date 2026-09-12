"""Unambiguous JSON decoding for integrity-bearing process boundaries."""

from __future__ import annotations

import json
from typing import Any


class DuplicateJsonMemberError(ValueError):
    """A JSON object declared the same member more than once."""

    def __init__(self, member: str) -> None:
        super().__init__(f"duplicate JSON member: {member}")
        self.member = member


def _reject_duplicate_members(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise DuplicateJsonMemberError(key)
        value[key] = item
    return value


def loads_unique_json(value: str | bytes) -> object:
    """Decode JSON while rejecting last-member-wins ambiguity recursively."""

    return json.loads(value, object_pairs_hook=_reject_duplicate_members)
