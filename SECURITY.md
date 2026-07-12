# Security policy

## Supported versions

Documentation Engine is pre-1.0. Until a broader support policy exists, only
the latest published `0.1.x` version is supported for security fixes. There is
no commitment to backport fixes to an older `0.1.x` version once a newer one is
available.

## Reporting a vulnerability

Report suspected vulnerabilities through
[GitHub Private Vulnerability Reporting](https://github.com/Jafa7/DocumentationEngine/security/advisories/new)
on this repository (the "Security" tab → "Report a vulnerability"). This
keeps the report private to maintainers until a fix is available.

**Do not open a public GitHub issue with exploitable details.** If you are
unsure whether Private Vulnerability Reporting is enabled for this
repository, open a public issue asking only that it be enabled, without
describing the vulnerability itself.

This repository does not publish a dedicated security contact email or a
response-time SLA. Private Vulnerability Reporting is the reporting channel.

## What counts as a security report

A security report is a defect with a plausible security impact: for example,
path traversal or arbitrary file write/read outside the documentation root,
unsafe deserialization, or an MCP tool that can be driven to act outside its
declared read-only/mutating contract.

Ordinary runtime, installation, adoption or compatibility problems are not
security reports. Use [the adopter reporting guide](docs/adopter-reporting.md)
and its issue templates for those instead.

## What to include

- A minimal reproduction, ideally against a synthetic fixture rather than
  private adopter content.
- The affected Documentation Engine version or commit.
- The impact: what an attacker could read, write or trigger, and any
  preconditions required.
- Sanitized evidence (commands, exit codes, diagnostics) — do not paste
  private document bodies, secrets or unrelated private paths, consistent
  with the [adopter reporting privacy rules](docs/adopter-reporting.md#privacy-rules).

## External configuration dependency

GitHub Private Vulnerability Reporting must be enabled in this repository's
settings (Settings → Security → Private vulnerability reporting) for this
channel to work. Enabling it is a repository settings change outside this
codebase and is expected to happen as part of preparing the release, not as
part of any code change.
