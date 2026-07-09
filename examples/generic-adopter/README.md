# Generic adopter fixture

This small fixture demonstrates the reusable adopter workflow without relying
on a private project:

```bash
docsystem readiness examples/generic-adopter
docsystem migration-report examples/generic-adopter
docsystem context DOC-002 examples/generic-adopter --depth 1
docsystem finish DOC-002 examples/generic-adopter --include-related
docsystem report draft examples/generic-adopter \
  --project-name "Generic Adopter" \
  --type adoption-finding \
  --source codex \
  --component adoption
docsystem index examples/generic-adopter --write
docsystem changes examples/generic-adopter
```

The fixture intentionally contains:

- a root area fallback;
- one excluded Markdown template;
- a legacy relative relation that resolves to a stable ID;
- one external/resource boundary;
- a review document whose stale pin is classified as a historical snapshot.
