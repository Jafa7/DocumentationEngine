---
id: DOC-002
revision: 2
depends_on: [README.md]
related: [review.md]
derived_from: [https://example.com/source]
---

# Design

## Summary

This document is the target workstream for the generic adopter fixture.

## Contents

- [Decision](#decision)

## Decision

The design depends on the root index through a legacy relative path that
`relations.legacy_paths = "resolve-with-warning"` resolves to `DOC-001`.
