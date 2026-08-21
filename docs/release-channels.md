# Release channels: live vs tagged

cvcpkg has two publishing channels. Everything published today lands in the
**live** channel: the bundle's `release_tag` column is empty, it appears in the
catalog immediately, and it is treated as a cache-like artifact that GC may
evict. A **tagged release** (`release_tag` set, e.g. `v1.3.0`) is the opposite:
exempt from every automatic GC and removable only by an explicit admin `nuke`.

The `release_tag` plumbing exists end-to-end — a column on every package row,
filters on `GET /v1/packages` and `GET /v1/search`, a `release_tag` query
parameter on `POST /v1/publish` (and `cvcpkg publish --release-tag`), an
`/admin/releases` page, and the GC exemptions below — but **no server-side
release has been cut yet**. The freeze/promotion workflow that would turn a set
of live bundles into a tagged release is roadmap work (see the release workflow
and the admin-UI "release creation / promotion" follow-up in
[roadmap/CVCPKG-ROADMAP.md](roadmap/CVCPKG-ROADMAP.md)). In practice: every
bundle on cvcpkg.org is live.

| Channel | `release_tag` | Automatic GC | Removal path |
|---|---|---|---|
| Live (default) | `""` (empty) | Cache GC by age/storage/staleness; yank-retention purge | Yank, then retention or `nuke` |
| Tagged release | e.g. `v1.3.0` | Exempt from all of it | Yank (hides it), then admin `nuke` only |

## Searching by channel

`cvcpkg search` forwards a `--release` filter to `GET /v1/search`. The value
`live` is a virtual tag meaning "empty `release_tag`" — bundles not part of any
release:

```bash
cvcpkg search boost --release live      # unreleased (today: everything)
cvcpkg search fft --release v1.3.0      # a tagged release, once one exists
```

The same `release` parameter (including `live`) works on `GET /v1/packages`;
search responses also carry a `releases` facet. See
[api-reference.md](api-reference.md).

## Pinning installs

What actually pins today:

- **Version pins** — `cvcpkg install zlib==1.3.1+cvc.1`. Published variants are
  immutable (below), so a version pin names exact bytes.
- **Catalog snapshots** — every catalog publish carries a monotonically
  increasing `revision`, and `cvcpkg catalog-generate` writes an immutable
  `<revision>.yaml` next to `latest.yaml`. Point `--catalog` at a snapshot URL
  or local file to resolve against a frozen view:

  ```bash
  cvcpkg catalog --show                    # current revision + bundle count
  cvcpkg catalog --pin 42                  # fetch + cache revision 42
  cvcpkg install zlib boost --prefix ./deps \
      --catalog https://transfix.github.io/libcvc-deps/catalog/42.yaml
  ```

- **The lockfile** — `install` records the catalog revision it resolved against
  (plus each entry's exact version and sha256) in
  `<prefix>/share/libcvc-deps/lockfile.yaml`.

Two `install` flags exist for this model but are **not yet enforced**:
`--release VER` is recorded in the requirements (the `libcvc-deps:` key of
`cvc-requirements.yaml`) but resolution does not consult it, and
`--catalog-revision REV` is accepted but not applied to the catalog fetch.
Until they are wired up, use an explicit `--catalog` snapshot to pin. (The
`source_release` field shown by `cvcpkg info` is related provenance: the
libcvc-deps release that first shipped a bundle, or `source-build` for local
fallback builds.)

## Yank and unyank

Yanking retires a published bundle without deleting its bytes. It is stronger
than cargo's yank: `GET /v1/catalog` **omits** yanked bundles rather than
flagging them, so resolution never sees them and even an exact version pin
stops resolving — that install now fails, or builds from source with
`--fallback-to-source` / `--local`.

```bash
# retire one broken variant (publisher token: own or org packages only)
cvcpkg yank readline 8.3+cvc.1 --platform linux --arch x86_64 \
    --config release --link shared

# retire every variant of a version, no prompt
cvcpkg yank mypkg 1.2.3+cvc.1 --yes
```

Scope flags (`--platform/--arch/--config/--link`) are tri-state: omit one and
it matches every value, omit all and every variant of the version is yanked.
The CLI previews the affected bundles and warns when a yank would leave a
platform tuple with no installable bundle at all.

Listings hide yanked bundles by default. `cvcpkg search --include-yanked` adds
them with a `State` column; `--yanked-only` shows just them and prints a
copy-pasteable restore command:

```bash
cvcpkg search readline --yanked-only
cvcpkg unyank readline 8.3+cvc.1 --platform linux --arch x86_64
```

`unyank` requires an **admin** token — a publisher cannot un-retire even its
own package. A mirror may serve bundles its upstream yanked; clients treat the
upstream as authoritative unless `--trust-mirror` / `CVCPKG_TRUST_MIRROR=1`.

Yanked live bundles are eventually purged (row **and** archive) by the
yank-retention GC once `CVCPKG_YANK_RETENTION_DAYS` is set (default `0` =
disabled; recommended `365`). `cvcpkg nuke` deletes a yanked bundle immediately
— admin-only, and it refuses a bundle that is not already yanked. Details and
endpoints: [api-reference.md](api-reference.md).

## GC protection for tagged releases

Every destructive automatic path filters on `release_tag = ''`:

- the cache endpoints (`GET /v1/cache`, `DELETE /v1/cache`, `POST /v1/cache/gc`)
  list and purge only non-release entries, whether by age, storage pressure, or
  stale recipe chain hash;
- the yank-retention purge (background loop and `POST /v1/admin/gc/yanked`)
  exempts tagged releases, so even a *yanked* tagged release never ages out.

The only way a tagged release loses bytes is an explicit admin `nuke`.

## Immutability and cvc_revision

A published variant — the `(name, version, platform, arch, build_type, link)`
tuple — is immutable. Re-publishing it returns `409 Conflict`:

```
zlib==1.3.1+cvc.1 (linux/x86_64/release/shared) already published.
Publish a new revision, or ask an admin to nuke the existing bundle first.
```

So a recipe fix that does not bump `cvc_revision` (the `+cvc.N` version suffix,
required by the recipe schema) changes nothing for consumers: the publish
409s and every install keeps resolving the old bytes. This has bitten
repeatedly — bump the revision, republish, and verify by downloading. `cvcpkg
pack --bump` picks the next free revision automatically (`cvcpkg
next-revision` to preview, `cvcpkg cascade-bump` for dependents), and yank the
broken revision afterwards if it is actively harmful.

See also: [api-reference.md](api-reference.md) for the endpoints,
[recipe-authoring.md](recipe-authoring.md) for `cvc_revision` in recipes, and
[operator-runbook.md](operator-runbook.md) for server operations.
