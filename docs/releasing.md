# Releasing

Documentation Engine publishes to PyPI from `.github/workflows/release.yml`
using PyPA Trusted Publishing (OIDC). There are no API tokens: the workflow
authenticates as this repository, so the publishing identity cannot be copied
out of the project.

Pushing a `v*` tag is the entire release trigger. The workflow then runs the
project gate, builds the wheel and sdist once, refuses to continue unless the
tag matches the package version, publishes to TestPyPI, verifies that TestPyPI
serves back the exact bytes it built, installs and runs the result from the
index, and only then offers the same artifact to PyPI — where a required
reviewer must approve the upload by hand.

## External configuration contract

The workflow cannot create any of this. Configure it once, in the accounts
themselves, and keep it matching exactly — a Trusted Publishing pending
publisher is matched on the workflow *filename* and the *environment name*, so
a mismatch is rejected at upload time with an opaque OIDC error.

| Setting | Value |
| --- | --- |
| Owner | `Jafa7` |
| Repository | `DocumentationEngine` |
| Workflow | `release.yml` |
| PyPI / TestPyPI project | `documentation-engine` |
| GitHub environments | `testpypi` and `pypi` |

- **PyPI** → *Publishing* → add a pending publisher for project
  `documentation-engine` with the values above and environment `pypi`.
- **TestPyPI** → the same, with environment `testpypi`. TestPyPI is a separate
  account and database from PyPI.
- **GitHub** → *Settings → Environments* → create `testpypi` (no reviewers) and
  `pypi` **with a required reviewer**. That review is the manual approval gate,
  and it is the only thing standing between a tag push and an upload that
  cannot be taken back.
- Restrict who may push `v*` tags, since a tag push is what starts a release.

A pending publisher does not reserve the project name. The name is claimed on
the first successful upload.

## Releasing

1. Land everything on `main` and confirm CI is green on the commit you intend
   to tag. The tag must point at a green `main` commit; the release gate reruns
   the checks, but discovering a failure there wastes a version number's worth
   of ceremony.
2. Bump `__version__` in `src/docsystem/__init__.py` (the single source of
   truth — `pyproject.toml` derives the version from it) and add the
   `CHANGELOG.md` entry.
3. Tag that commit `vX.Y.Z` and push the tag.
4. Watch the run. Nothing reaches PyPI before the approval in step 5, but the
   tag is already immutable — see [Recovery](#recovery) for what a failure
   before that point does and does not permit.
5. Approve the `pypi` environment when the TestPyPI integrity and install smoke
   is green. **This step is irreversible.**

The workflow does not create tags or GitHub Releases and never requests write
access to the repository. Create and push the tag in step 3 to start the
workflow. Create the matching GitHub Release only after the production upload
lands successfully.

## Recovery

**A pushed release tag is never moved and never reused.** It is the immutable
identity of a release candidate: every downstream check — the tag/version gate,
the digest comparison against TestPyPI, the audit trail of what was uploaded —
means nothing if the commit a tag points at can change. No failure is ever
repaired by fixing the code and pointing an existing release tag at it.

That leaves exactly two cases when a release run fails.

- **A transient infrastructure failure** — a runner dying, a network timeout, an
  index being briefly unreachable — and nothing about the release changed. Then,
  and only then, re-run the failed workflow. It must run against the *same*
  immutable commit and produce *identical* artifacts; the digest gate against
  TestPyPI is what proves it did.
- **Anything that changes the source or the artifacts** — a code fix, a metadata
  fix, a dependency change, a rebuild that is not byte-identical. Then the
  candidate is a different release: bump `__version__`, add the `CHANGELOG.md`
  entry, commit, and push a **new** tag. The failed version number is spent.

Once a version has reached an index, Documentation Engine treats that release
as immutable. PyPI does not allow a distribution filename to be reused, even
after deleting the release. Recovery is therefore exactly one path: **yank the
bad version and publish a new one.** Yanking hides the version from resolvers
that are not pinned to it, while leaving it installable for anyone who already
pinned it. Never add replacement files, delete-and-reupload, or otherwise reuse
a published version.
