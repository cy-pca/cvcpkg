# Archive Format Handling Roadmap

**Status:** Proposed — no code yet. Two band-aid fixes have shipped in CI
workflows (see [Interim state](#interim-state-2026-08)); the underlying design
below is unimplemented.
**Author:** Joe + Claude
**Date:** 2026-08-18

---

## Problem Statement

A cvcpkg package archive can be one of three container formats:

| Format | Suffix | Where it is produced today |
|---|---|---|
| Zstandard tarball | `.tar.zst` | remote-builder rewrap + server storage only |
| Gzip tarball | `.tar.gz` | local `cvcpkg pack` on **non-Windows** |
| Zip | `.zip` | local `cvcpkg pack` on **Windows** |

Which suffix a given pack produces is decided by **platform conditionals
scattered across the codebase**, and the mapping is re-derived independently in
at least five places (see [Current State](#current-state)). There is no single
`archive_format` source of truth, and — critically — **nothing tells an external
consumer which extension a pack just produced**. So every consumer that needs to
find a freshly-packed archive hardcodes a glob, and the glob drifts out of sync
with reality.

This is not hypothetical. On 2026-08-18 the Windows publish lane
(`.github/workflows/windows-build.yml`) globbed `dist/*.tar.zst` in its
"is there anything to publish?" pre-check. But `cvcpkg pack` writes a **`.zip`**
on Windows, so the guard matched nothing, printed
`::warning::No archives found — nothing to publish`, and `exit 0`ed — **after a
fully successful pack**. The archive was built and then silently dropped on the
floor. This is the actual reason `python311/windows` had no published bundle
despite green CI runs. `macos-build.yml` carries the identical latent bug (it
globs `.tar.zst` but macOS packs to `.tar.gz`).

The immediate fix was to change the Windows glob to `.zip` and the macOS glob to
`.tar.gz` — but **that just relocates the magic string**. The moment a fourth
consumer, a format change, or a per-recipe override appears, the same class of
bug returns. The ugliness is structural: the archive suffix is an implementation
detail of cvcpkg's packer that is being reverse-engineered by everyone who
consumes its output.

**Two goals:**

1. **Use the appropriate extension for the given package** — from *one* place
   cvcpkg owns, never re-derived by a caller.
2. **Support different extensions properly** — make the container format an
   explicit, first-class choice (with sane per-platform defaults) rather than a
   platform accident, and make every producer and consumer format-agnostic.

---

## Current State

The consumption side is *already* format-agnostic — the problem is entirely on
the production side and in external consumers.

| Concern | Status | Location |
|---|---|---|
| Pack suffix chosen by `if platform == "windows"` | Ad-hoc conditional | `builder.py:create_archive` (2005–2010) |
| Remote-builder rewrap suffix switch (`.zip`/`.tar.gz`/`.tar.zst`) | **Triplicated** verbatim | `cli/_builder.py:1144, 1329, 1440` |
| Source-download suffix sniff (`.zip` vs `.tar.gz`) | Ad-hoc conditional | `builder.py:366–368` |
| Build-cache archive name | Hardcoded constant | `build_cache.py:103` (`install.tar.gz`) |
| Recipe-bundle archive name | Hardcoded `.tar.gz` | `cli/_recipe.py:220, 434, 527, 594, 705` |
| Publish **resolve** (find archives to publish) | ✅ **Already extension-agnostic** | `cli/_publish.py:_resolve_all_archives` — globs `*-{platform}-{arch}-{config}-{link}.*` |
| Publish **manifest read** (zip vs tar) | ✅ Already format-agnostic | `cli/_publish.py:_extract_manifest` (sniffs `.zip`) |
| Archive-like extension allow-list | ✅ Centralized (but local to one fn) | `cli/_publish.py:313–323` |
| CI "anything to publish?" pre-check | ❌ **Hardcodes the wrong glob** | `windows-build.yml`, `macos-build.yml`, `publish-cvcpkg.yml` |

The irony worth internalizing: **`cvcpkg publish --all` already does the right
thing.** It self-resolves archives with a `.*` glob and reads either container.
The CI pre-checks that gate it are pure redundancy — and they are exactly what
broke. Any design here should lean into the tool already being agnostic and
delete the callers' need to guess.

---

## Design

### A. One source of truth for the container format

Introduce a small `cvcpkg/archive_format.py` (name TBD) that owns *all*
format knowledge:

```python
class ArchiveFormat(enum.Enum):
    ZSTD = "zst"   # .tar.zst  — best ratio; canonical where tooling exists
    GZIP = "gz"    # .tar.gz   — portable legacy fallback
    ZIP  = "zip"   # .zip      — native double-click on Windows

    @property
    def suffix(self) -> str: ...          # ".tar.zst" / ".tar.gz" / ".zip"

def default_format(platform: str) -> ArchiveFormat: ...   # policy in ONE place
def detect_format(path: Path) -> ArchiveFormat: ...       # by suffix
def format_of(path: Path) -> ArchiveFormat | None: ...    # None if not an archive
```

Then **every** producer and sniffer calls it:

- `builder.py:create_archive` picks `default_format(platform)` (or an explicit
  request — see C) and asks it for the suffix + the writer.
- The three `cli/_builder.py` `suffix, kind = …` switches (1144/1329/1440)
  collapse to one `fmt = ArchiveFormat(kind)` / `fmt.suffix`.
- `builder.py:366` source-download sniff uses `detect_format`.
- `build_cache.py` and `cli/_recipe.py` bundle names reference the module's
  suffix rather than a literal.

No platform `if` for a suffix survives outside this module.

### B. Never make a consumer guess the extension

External consumers (CI, scripts, downstream tooling) must be able to act on a
pack's output **without knowing the container format**. Two complementary
levers:

1. **`cvcpkg pack` / `pack-all` emit the archive path(s).** Today `pack` prints
   a human line (`builder.py`/`_build.py:1287`); add a machine-readable channel
   — e.g. `--print-archives` (one path per line to stdout) or `--json`
   (`{"archives": ["dist/…zip"], "sha256": …}`). CI captures that instead of
   re-globbing. This is the *right* fix for the workflows: consume what the
   producer reports.
2. **A first-class discovery command** — `cvcpkg archives ls --output-dir …
   --platform … [--config … --link …]` that prints the archives cvcpkg would
   publish, reusing `_resolve_all_archives` verbatim. Any pre-check becomes
   `test -n "$(cvcpkg archives ls …)"` — format-agnostic by construction.

**Then delete the extension-globbing pre-checks entirely.** `cvcpkg publish
--all` already no-ops cleanly when `dist/` has no matches for the platform
tuple, so the guard can either be dropped or replaced with the discovery
command. The three CI workflows stop hardcoding `.tar.zst` / `.zip` / `.tar.gz`.

### C. Make the format an explicit, defaulted choice

Promote the container format from "platform accident" to a real option:

- **`--format {zst,gz,zip}`** on `pack`, `pack-all`, and `build`, threaded into
  `create_archive`. Absent → `default_format(platform)`.
- **Per-platform defaults** live in `default_format()`:
  - Windows → `zip` (native tooling, double-click extract) *or* `zst` once we
    decide the Windows deploy story — this is a **policy decision to make**, not
    a fait accompli.
  - Unix/macOS → `zst` (best ratio) with `gz` as the compatibility fallback.
  - The server already stores and serves whatever it is given; the download
    side already sniffs by suffix, so making `zst` available on the local pack
    path is a producer-side change only.
- **zstd available everywhere**, not just the remote rewrap — so a local pack
  and a fleet build can produce byte-comparable containers, which also
  simplifies the remote-builder rewrap (it may no longer need to re-container
  at all if the local format already matches the canonical one).

---

## Interim state (2026-08)

Shipped as band-aids while this design is unimplemented — recorded here so the
hardcoded globs are understood as temporary, not endorsed:

- [x] `windows-build.yml` publish pre-check glob `*.tar.zst` → `*.zip`
      (libcvc-deps `fix/python311-windows`). Unblocks the first Windows python
      publish.
- [x] `macos-build.yml` publish pre-check glob `*.tar.zst` → `*.tar.gz`
      (shipped in #516, commit `928ba29f` — fixed both the archive-count check
      and the publish guard; identical bug to the Windows one, different
      correct suffix — which is itself the argument for this roadmap).

Both are `s/one magic string/another magic string/`. They are correct today and
wrong the next time the format policy moves; Section B deletes the need for them.

---

## Phases

- [ ] **A1** — `archive_format` module: `ArchiveFormat`, `suffix`,
      `default_format`, `detect_format`, `format_of`. Unit-tested.
- [ ] **A2** — Refactor all producers/sniffers to the module: `create_archive`,
      the three `_builder.py` switches, the download sniff, the bundle names.
      No behavior change; pure de-duplication. (Guard with a test that asserts
      the produced suffix per platform matches today's behavior.)
- [ ] **B1** — `cvcpkg pack`/`pack-all` machine-readable archive output
      (`--print-archives` / `--json`).
- [ ] **B2** — `cvcpkg archives ls` discovery command over
      `_resolve_all_archives`.
- [ ] **B3** — Remove the extension-glob pre-checks from `windows-build.yml`,
      `macos-build.yml`, `publish-cvcpkg.yml`; rely on `publish --all`'s
      self-resolution or the B1/B2 output. **Closes the whole bug class.**
- [ ] **C1** — `--format {zst,gz,zip}` plumbed through pack/pack-all/build.
- [ ] **C2** — Decide + encode per-platform default policy in
      `default_format()`; make zstd available on the local pack path.
- [ ] **C3** — Simplify the remote-builder rewrap now that local and fleet
      formats can match.

## Relationship to other phases

Extends **Phase 15 (CLI UX & the Recipe-First Workflow)** — `pack`/`publish`
ergonomics — and cleans up the **remote-builder** rewrap machinery. B3 is the
highest-value, lowest-risk slice: it removes a silent-data-loss footgun from the
publish lanes and depends only on behavior `cvcpkg publish --all` already has.
