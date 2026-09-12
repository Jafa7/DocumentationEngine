# Retrieval cost measurement

This benchmark measures engine work separately from context-response size. It
answers where a targeted request still scales with the complete catalog before
Documentation Engine narrows internal materialization. It does not replace the
[context reduction measurement](context-efficiency.md), which measures how much
verbatim task content is returned to an AI client.

## Reproducible synthetic runner

Run the developer benchmark from the repository checkout:

```bash
uv run python scripts/measure_retrieval_cost.py \
  --documents 120 \
  --body-bytes 4096 \
  --graph-density 2 \
  --context-depth 2 \
  --repetitions 2 \
  --json \
  --output /tmp/docsystem-retrieval.json
```

The runner creates a temporary synthetic project, writes and verifies a
projection, and measures these paths in fresh child processes:

- one explicit section read;
- one depth-two semantic context request;
- one complete pinned provider snapshot export.

For read and context it measures both direct Markdown and verified-projection
serving and requires their stdout to be byte-identical. Repeated runs must also
be byte-identical. The synthetic corpus, parameters, Python/platform details,
Git revision and dirty-worktree state are included in JSON.

The `source_bytes_read` and `derived_bytes_read` counters cover complete-file
reads through `pathlib.read_bytes`/`read_text` below the synthetic source and
derived-state roots. They are instrumentation values, not filesystem block-I/O
counters. Instrumentation attaches to the internal retrieval service rather
than CLI compatibility aliases, so the counters follow the code path that
actually materializes direct or projected views. Wall time includes fresh
Python process startup. The runner does not evict operating-system caches, so
`first-process` must not be described as a genuinely cold-filesystem
measurement. Peak RSS uses
`resource.getrusage(RUSAGE_SELF).ru_maxrss` where the platform provides it.

## W09 baseline

The 2026-09-12 development candidate was measured on Python 3.12.3 under WSL2
Linux. It was based on Git revision `3f5f9cc` with the tracked W01-W09 candidate
changes present. The generated corpus contained 120 documents and 495,248
source bytes. Values below are the first process and median of two additional
fresh processes; they are evidence of work shape, not latency promises.

| Scenario | Serving | First process | Repeated median | Source read | Derived read | Parsed docs | Materialized docs | Response |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Section read | direct | 0.607 s | 0.524 s | 495,248 B | 0 B | 120 | 120 | 3,947 B |
| Section read | projection | 0.384 s | 0.355 s | 495,248 B | 373,247 B | 0 | 120 | 3,947 B |
| Depth-two context | direct | 0.574 s | 0.548 s | 495,248 B | 0 B | 120 | 120 | 1,457 B |
| Depth-two context | projection | 0.404 s | 0.496 s | 495,248 B | 373,247 B | 0 | 120 | 1,457 B |
| Complete snapshot export | projection | 0.673 s | 0.409 s | 0 B | 459,423 B | 0 | 0 | 172,503 B |

The projection removes Markdown parsing but currently verifies every included
source byte and materializes every document shard even for one section. The
returned content is already task-sized; the remaining cost is internal catalog
work, not excess AI context.

## Decision

Do not weaken freshness verification merely to improve this benchmark. Full
source hashing is the current contract that proves a projection still matches
Markdown truth.

The evidenced optimization candidate is query-scoped shard materialization
*after* that freshness gate: a section read needs one document shard, while a
context query should load only the shards reached by its explicit semantic
traversal. Implementing that cleanly belongs at the retrieval-service seam
rather than adding another CLI-only fast path. It therefore remains a named
post-service-extraction optimization. Any later implementation must rerun this
benchmark and preserve byte-identical direct/projected results, source/config
drift detection and complete graph answers.
