# Changelog

All notable changes to the **libcvc-deps** prebuilt bundles are
recorded here. Each tagged release of libcvc-deps records the
**exact upstream versions actually shipped** in the release
artifacts. Consumers who need bit-for-bit reproducible bundles
should consume a tagged release rather than the moving tip.

A given release of libcvc-deps is intended to be used with the
corresponding (or older) libcvc release. Bumping a major version
of Qt or VTK is reflected by bumping libcvc-deps's own version.

Some components are pinned in the workflow itself (VTK, Qt on
Windows, log4cplus, NFFT, vcglib, libiimod, levmar, the Windows
ImageMagick overlay port). Others are taken as-is from each
platform's package manager and therefore drift between releases.

The format follows [Keep a Changelog](https://keepachangelog.com/)
and the project adheres to [Semantic Versioning](https://semver.org/).

Starting with v1.3.0 the focus shifts from monolithic per-platform
bundles to **per-component archives served by `cvcpkg`** (the
`cvcpkg.org` registry).  The "Full component manifest" tables in the
older v1.0.x / v1.1.0 entries describe the legacy monolithic-zip
shape; from v1.3.0 onward consumers should consume individual
component bundles through the `cvcpkg` CLI, with upstream pins
documented per-recipe in `recipes/<name>/recipe.yaml`.

---

## v2.5.11

**A release so builders carry the Haiku delegation fixes (#517) and label
noarch bundles `none`/`none` (#508). #508 also changes the server (migration
036 and the `X-Cvcpkg-Neutral-Variants` catalog header), so the prod promote
must deploy before the builders update.**

- **noarch bundles are config/link-neutral: `none`/`none`.** A `platform: any`
  bundle (source packages, pure-Python wheels, data) is the same in release
  and debug, shared and static, but it was published under the build
  matrix's config/link. Up to four copies per version were built and
  published, each listed as "release"/"static" and so on. Now:
  - `pack`, `pack-all`, `build-all` and the builders label an any/noarch
    bundle `none`/`none` (`foo-1.0-any-noarch-none-none.tar.gz`) and build it
    as release/shared;
  - so is every other bundle of a recipe with an `any` entry: a pure-Python
    column's `windows` bundle, which exists only for the Windows layout
    (`Lib/site-packages`), is `windows`/`x86_64`/`none`/`none`;
  - `builds submit-dag` stages each noarch job once, and a pure-Python
    column's windows job once per target. With noarch jobs and more
    than one concrete config/link the whole submission is one DAG,
    `<dag-id>-all`, so the edges between them can still be expressed;
  - install and the builders' dependency fetch treat `none` as matching
    every config/link, as `any`/`noarch` already match every platform/arch;
  - the server records any noarch upload as `none`/`none`, so an older
    builder's release/shared, debug/static, ... uploads of one bundle land on
    one row (the rest get 409, which publish skips). It accepts `none`/`none`
    on a concrete platform and refuses `none` for only one of the two;
  - `/v1/catalog` lists each `none`/`none` bundle once per concrete variant
    for clients that do not send `X-Cvcpkg-Neutral-Variants` (cvcpkg 2.5.x
    and older filter by exact config/link). This client, the mirror sync and
    populate send it;
  - migration 036 relabels published noarch bundles, and the concrete bundles
    of each version that has one (the pure-Python windows bundles): one row per
    bundle becomes `none`/`none` (a live row first, then release/shared) and the
    other variants are yanked. A yanked row keeps its archive downloadable
    for pinned lockfiles and keeps its org visibility; the yank-retention GC
    removes it later.
- **Fix: a Haiku job could wait forever on an ssh session that never ended.**
  Haiku's sshd sometimes keeps a session open after its command has exited
  (about one run in three on phm's VM), and `cvcpkg.haikuhost` waited on ssh
  with no timeout: a prod sevenzip job sat 70 minutes in its recipe push. Each
  remote command now reports its own exit status on a marker line; once the
  marker is in, ssh gets 5 s to exit and is then stopped, and the job goes on
  with the reported status. Transfers, remote commands and the remote build
  also stop when no data moves for too long (`CVCPKG_HAIKU_IDLE_TIMEOUT`,
  `CVCPKG_HAIKU_BUILD_IDLE_TIMEOUT`, `CVCPKG_HAIKU_TRANSFER_TIMEOUT`), and file
  transfers now run in the job's process tree, so a cancelled job stops them.
- **Fix: autotools builds broke on Haiku over source timestamps.** BFS replaces
  a zero sub-second mtime with one taken from the moment of the call, so a tree
  pushed with tar came out with its files ordered by extraction, and make
  regenerated shipped files (gperf's manual, flex's Makefiles). The VM's clock
  also runs behind the builder's, so a tree stamped with the builder's time was
  in the future and automake's configure stopped with "newly created file is
  older than distributed files!" (m4, iconv, libtasn1). After the push the
  source tree is now re-stamped on the Haiku side: each file keeps its second
  with a fixed nonzero fraction, and the whole tree moves back below the Haiku
  clock when it has to. With it, gperf and m4 build for Haiku; their recipes
  get haiku rows separately.

## v2.5.10

**A release so builders rewrite Windows script shebangs (#506), and so
installs resolve noarch bundles for any config and link mode (#510). The server
needs nothing for #506; packing runs on the builders, so they need this
release.**

- **Fix: Windows text scripts kept the build's interpreter as their shebang.**
  pip writes a package's `scripts=` files (jmespath's `jp.py`, xlsxwriter's
  `vba_extract.py`) into `Scripts\` as text, with a `#!C:\...\python.exe` line
  naming the job's deps-prefix interpreter. Packing rewrote shebangs only in
  `bin/`, `sbin/` and `libexec/`, and only forward-slash paths, so these shipped
  as is. On the Windows builder that path is `C:\cvb\cvcpkg-job-...`, which any
  local user can create on a machine with the package, and the `py` launcher
  runs it. Now:
  - `Scripts/` is scanned too (its `.exe` launchers are still left to the
    launcher pass);
  - a Windows interpreter path matches with either slash and in any case,
    quoted or not;
  - it becomes `#!/usr/bin/env python`, which the launcher resolves on PATH.
    Versioned names such as `python3.13t` are kept, and a CRLF line stays CRLF.
- **Fix: a noarch bundle never resolved for a static or debug install.** A
  noarch recipe (`platform: any`: fonts, data sets, pure Python, JavaScript)
  publishes one any/noarch/release/shared bundle. The catalog filter matched it
  to every platform and arch but compared config and link exactly, so
  `cvcpkg install xterm-js --platform wasm` (wasm, wasm-mt, wasi and cosmo are
  static-only), `--link static` or `--config debug` failed with "no bundles
  found in catalog for this platform tuple". No recipe for those targets could
  list a noarch dep: libcvc's arigl-web had to install xterm-js separately.
  - A noarch bundle now serves every config and link mode, in `install`,
    `install-deps`, `sync` and `catalog`. A compiled bundle still needs its
    exact tuple.
  - A concrete bundle still beats a noarch one at the same version, as before.
  - When a noarch package has published several variants of one version, the
    one matching the request best is kept.
  - Client-only: the server serves the whole catalog, and the client filters
    it.

---

## v2.5.9

**A release so the builders' temp-path rewrite stops corrupting Windows
paths (#491). The server needs nothing; builders rewrite installed deps
themselves, so they need this release.**

- **Fix: rewriting a dependency's baked build path broke Windows scripts.**
  When a builder installs a dep, it rewrites the dep's build-time install dir
  (`.../cvcpkg-<name>-<id>/install`) in its scripts and data files to the
  job's prefix. The match stopped at the `:` after a drive letter, so on a
  Windows path the `C:` survived in front of the new one. glib's
  `bin/gdbus-codegen` became
  `path = os.path.join('C:C:\Users\...\share', 'glib-2.0')`, which Python
  cannot parse, so every gdbus-codegen run in gtk4's Windows build failed
  (job 24083, 2026-10-27). Now:
  - the match takes an optional drive letter, and either separator. The
    letter is not taken when it ends a path component, as in
    `/opt/b:/tmp/...`;
  - the replacement keeps the separators of the path it replaces. A
    forward-slash path gets the new prefix with forward slashes, the only
    spelling that is safe inside a Python or CMake string.

  glib 2.80.5+cvc.5 no longer bakes the path at all, so gtk4 built on 2.5.8
  builders too. This covers any other dep that does.

---

## v2.5.8

**A release so Windows recipes build on a host with no pwsh 7, as
lanparty-gamez's documented `--local --with-deps` flow needs on stock
Windows 11.**

- **Fix: Windows recipes could not build on a host with no pwsh 7.** On stock
  Windows 11 every `build.ps1` failed with "pwsh 7 not found", and declaring
  the `powershell` recipe as a dependency did not help: `--with-deps` builds it
  into `<prefix>.build`, its own build.ps1 needed a pwsh, and the targets never
  looked in the build prefix (lanparty-gamez on cmps-a91453, 2026-10-27). Now:
  - pwsh is looked for in the build prefix, then the prefix, then PATH;
  - with no pwsh 7 anywhere, a script that declares `#Requires -Version 5.1`
    (and not `-PSEdition Core`) runs under Windows PowerShell 5.1 with
    `-ExecutionPolicy Bypass`. A script without the declaration still needs
    pwsh 7;
  - the `powershell` recipe's build.ps1 declares it, so a recipe that
    build-depends on `powershell` gets pwsh 7 on a bare Windows host.

---

## v2.5.7

**A release so the fleet can publish a bundle with hundreds of runtime deps.
The server half went live on cvcpkg.org with #484. Builders publish through
the client, so they need this release.**

- **Fix: a bundle with hundreds of runtime deps could not be published.**
  `cvcpkg publish` sent `required_deps` and `provides` as query parameters,
  so wezterm-crates' 645 deps made a ~50 KB request line. cvcpkg.org's front
  end refuses anything over ~8 KB with 414, and the builder logged only
  "publish completed with 1 error(s)" (job 24063, 2026-10-27). Now:
  - the server also takes them as multipart fields on `POST /v1/publish` and
    as a JSON body on `POST /v1/upload/init`; a body value wins over the query
    parameter. `/healthz` advertises this as
    `features: ["publish-metadata-body"]`;
  - the client moves them to the body only when the query string would pass
    6 KB, and only for a server that advertises the feature. An older server
    would ignore the body and record no deps, so publishing such a bundle to
    it fails with a message saying so;
  - a `required_deps` or `provides` that is not a JSON list is refused with
    422. The DB path stored it verbatim, and catalog reads parse it as a list.

---

## v2.5.6

**A release so builders stop the jobs the server gave up on, and so the
single-file exe can read recipes that another account unpacked.**

- **Fix: a builder kept building jobs the server had already given up on.**
  The offline reaper fails every job of a builder whose heartbeats lapse for
  180 s and told the builder nothing. A builder only logged `job.timeout`, and
  it ignored `job.cancel` (a force-cancel). So the build ran on: its completion
  was refused, and meanwhile it kept the builder's slots, cores and memory
  while the scheduler, seeing nothing running there, dispatched it jobs it
  never started. On sandipaws (16 GB, two 20-core jobs) that kept the
  heartbeats stalling, and a dozen-plus jobs failed "went offline" in a cascade
  through their DAGs (2026-10-25/26). Builder #28 lost jobs the same way
  during a DNS outage. Now:
  - `builder run` stops a job on `job.cancel` or `job.timeout`, and whenever
    the server shows it no longer has the job dispatched or running on this
    builder. Stopping kills the build's whole process tree (its process group
    on POSIX; a Job Object plus `taskkill /T` on Windows, so no cl.exe, link,
    MSBuild or ninja is left behind), frees the slot at once (the next
    heartbeat's `current_jobs` drops), removes the job root, and reports
    nothing. The new `cvcpkg.proctree` does the process side; the build and
    test scripts and the WSL and Haiku delegations start through it.
  - Every heartbeat names the jobs being built (`job_ids`). The server answers
    with `stale_jobs`, those it no longer has on that builder, in the HTTP
    heartbeat's response and the socket's `heartbeat_ack`. The first beat
    that gets through after a gap brings out what the reaper failed
    meanwhile. Against a server too old to answer, the builder looks each job
    up (`GET /v1/builds/{id}`) instead. No answer, an error or an unclear
    answer never stops a build. Older builders send no `job_ids` and get the
    same answers as before.
  - The offline reaper pushes `job.cancel` for each job it fails, for a
    builder whose socket outlived its heartbeats.
  - Build scripts now run in a process group of their own on POSIX, so a stop
    signal sent to the builder no longer reaches them. The builder still
    drains in-flight jobs for up to 5 minutes. Then it kills the builds still
    running and lets them report the failure, so none outlives it.
    `CVCPKG_BUILDER_HEARTBEAT_INTERVAL` (default 60 s) sets the beat (#479).
- **Fix: the single-file exe died on recipes that another account had
  unpacked.** The frozen binary unpacks its embedded recipes once per digest
  into `<cache>/bundled-recipes/<digest>/`. It did that through a
  `tempfile.mkdtemp` directory, which it renamed into place. On Windows,
  Python 3.12.4+ gives a mkdtemp directory a protected ACL that admits only
  SYSTEM, Administrators and the directory's owner, and the rename keeps that
  ACL. If an elevated process unpacked a digest first, BUILTIN\Administrators
  owned the tree, and the same user could not list it unelevated. Every command
  that reads the recipe set (`cvcpkg build --local`, `cvcpkg recipes --list`)
  then died with `PermissionError: [WinError 5]`. The tree's `.complete` marker
  still stat'ed as a file, so the fallback never ran. Now:
  - The temp directory is created with a plain `mkdir`, so the tree inherits
    the cache's permissions. On POSIX its mode comes from the umask instead of
    0700.
  - A cached tree that cannot be listed is skipped with a warning that names
    it, and the recipes unpack into the bootloader's temp directory for that
    run.
  - A tree that an older release left unreadable stays where it is until it
    is removed from an elevated shell (#458).

---

## v2.5.5

**A release so Windows builders extract archive symlinks as files.**

- **Fix: tar symlinks came out unreadable on elevated Windows builders.** A
  source package can ship symlinks: wcwidth-src's `README.rst` points to
  `docs/intro.rst`. tarfile creates each link with `os.symlink` using the
  archive's target verbatim. An elevated process, which every Windows fleet
  builder is, gets a real link, and on Windows that link does not resolve: its
  relative target keeps the forward slash, and a link made before its target
  exists is a file link even for a directory. So hatchling failed "Readme file
  does not exist: README.rst" on builder #28 (wcwidth-cp311/313/313t), while
  the same build passed unelevated on the same machine, where tarfile copies
  the target instead. That fallback has gaps of its own: a directory link
  becomes an empty directory, and in a streamed extraction (the builder's
  `.tar.zst` dependency path) it raises `StreamError`.
  `cvcpkg._archive.materialize_symlinks` now replaces, on Windows, every
  symlink an extraction made with a copy of its target (settling links to
  links; leaving targets outside the destination alone), and
  `members_to_extract` skips symlink members in streamed extractions. Every
  tar extraction calls them: the builder's dependency installs, the
  installer, tarball sources and `safe_tar_extractall` (#470).

---

## v2.5.4

**A release so builders pick up two dependency-install fixes.** Builders
self-update only to a server version newer than their own, so the fleet ran
2.5.3's resolver through the 2026-10-19 populate. On Windows, its build-closure
installs put a second CPython into wheel prefixes and failed cp313 wheels with
"interpreter not found".

- **Fix: the fleet builder installed every dependency's build closure.** A
  job's dependency install walked the build deps of its deps, and of theirs,
  so it installed their source packages, compilers and build tools too. That is
  more than `cvcpkg install` installs, and on Windows it was wrong, not just
  slow. Every Windows CPython lays out python.exe, Lib\ and DLLs\ at the prefix
  root. A cp313 wheel's prefix also got python312 (pkg-config's build edge),
  whose python.exe replaced the wheel's own, and the wheel failed "interpreter
  not found". A cp312 wheel got python313 (python312's PCbuild build edge).
  - The builder now installs the recipe's own runtime, build and host_tools
    deps, then only the runtime deps beneath them, as `cvcpkg install` does
    with the catalog's `required_deps`. Version pins come from the same edges.
  - A `redistributable: false` dep is built in the job, so its build deps
    still come along.
  - Recipes that relied on a dep's build deps now declare what they use. The
    Windows meson users cairo, dav1d, dav1d-tools, fribidi, graphene, gtk4,
    harfbuzz, libepoxy, libpq, pango, pixman and proxy-libintl gain a Windows
    `python312` build dep for meson.cmd, as pkg-config and glib already had.

- **Fix: the fleet builder ignored version pins on dependencies.** A job's
  dependency install looked deps up by name and took the newest published row,
  whatever the recipes said. scipy-cp3XX pin `scipy-src ==1.17.1`; with an
  orphan scipy-src 1.18.0 also published, every scipy job on 2026-10-22 built
  from 1.18.0 (cp311 failed, as 1.18 needs Python 3.12, and cp312/313 would
  have published 1.18 code labelled 1.17.1).
  - The builder now collects every version constraint the recipes in the
    job's dependency closure put on a dep, and installs only a version that
    meets all of them (the resolver's rules: an exact pin naming `+cvc.N`
    holds the revision, ranges ignore it).
  - A dep whose published versions all fail its pins fails the job, naming
    each pin and the recipe that set it, instead of falling back.
  - Every pin in the tree (410) is met by the current cvcpkg.org catalog, so
    nothing that builds today stops building.
- **Fix: the builder broke `${pcfiledir}`-relative .pc files.** After it
  unpacks a job's deps, and after each merge in `cvcpkg build`, the builder
  repoints every .pc file's `prefix=` at the prefix root. A prefix written as
  `${pcfiledir}/<../ count>` is relative to the file's own directory, so for a
  .pc that a Python package ships inside site-packages the root is the wrong
  answer: pybind11's includedir became `<prefix>/include`, and all four
  scipy-cp3XX columns failed on the fleet on 2026-10-22 with
  `pybind11/pybind11.h: No such file or directory`. numpy's `numpy.pc` broke
  the same way. The rewrite now leaves a `${pcfiledir}` prefix alone; it
  already resolves wherever the file lands. Absolute prefixes baked at a
  build's temp install dir are repointed as before.

---

## v2.5.3

**A release so builders pick up the fixes that took sandipaws-dev offline.**
On Windows, staged `perl-src` trees leave ReadOnly files that 2.5.2 cannot
delete: job roots leak, and the work-dir sweeps spin on them, the hourly one
on the thread that sends heartbeats, so the server marks the builder offline
and fails its running job (2026-10-19 and again 2026-10-21, 67 leaked roots).

- **Fix: a slow disk sweep took a builder offline.** `builder run`'s hourly
  work-dir sweep ran on the same thread that sends heartbeats and reads the
  WebSocket, so while it ran the builder sent no heartbeats and took no work.
  On 2026-10-19 sandipaws-dev's sweep spent 20+ minutes on leaked ReadOnly
  job roots. Its socket stayed connected the whole time, because the
  websockets library answers the server's pings on a thread of its own. The
  server marked the builder offline and failed its running job, and since
  nothing had dropped, nothing reconnected. The sweep now runs on its own
  thread, one at a time.
- **Fix: Windows builders leaked every job root holding a read-only file.**
  Job cleanup used `rmtree(ignore_errors=True)`, and Windows refuses to unlink
  a file with the ReadOnly attribute. Every file a staged `perl-src` ships has
  it, so each such job left ~7,700 files behind, without a word. The startup
  sweep retried the same deletes and never cleared the attribute. On
  sandipaws-dev (2026-10-19) it spun on ~450,000 files for over ten minutes
  before the builder could register.
  - Both paths now share `builder_gc.remove_tree`, which clears the read-only
    bit and redoes a failed delete.
  - Job cleanup logs any tree it still cannot remove.
- **Builder: a failed dependency lookup fails the job instead of skipping the
  dep.**  The catalog answers an unknown name with 200 and no rows. Yet the
  builder logged any error status from `/v1/packages/{name}` as "not found on
  server (skipping)", and also skipped a dep whose download failed, then built
  without it. pango on Windows (2026-10-20) lost fribidi that way on a flaky
  network and failed in meson. Such an error now fails the job and names the
  dep. A 404 is still a missing dep. The builder's catalog GETs now also retry
  429 and 5xx responses, as they already did transport errors.

---

## v2.5.2

**A release so the builder fleet picks up the builder and pack fixes below.**
Builders self-update only to a server version newer than their own, so the
fleet stayed on v2.5.1 code through them; the 2026-10-19 populate hit several.

- **Pack: a NEEDED that names a scratch-prefix library by path becomes its
  basename** (#379).  OpenBSD libraries have no SONAME, and CMake and meson link
  dependencies by full path, so bundles carried `NEEDED /…/cvcpkg-prefix-*/lib/…`
  and failed the loader-path gate (openal-soft on OpenBSD).
- **Builder: a POSIX build's TMPDIR lives in its own work dir, and the recipe
  cache under `--work-dir`** (#398).  Scratch in the system /tmp accumulated
  until it filled, and the builder then failed every job in its first second
  with ENOSPC while reporting a healthy work volume (openbsd-build-2).
- **Server: no offline reaping straight after a restart** (#392).  Every deploy
  failed the running jobs of builders still reconnecting ("went offline") and
  cancelled their downstream.  Server-side; live since the 2026-10-19 deploys.
- **Builder: FreeBSD's `libdata/pkgconfig` is merged into `lib/pkgconfig`**
  after every build (#376).
- **Builder: a Windows job's noarch Python deps go into `Lib\site-packages`**
  (#342); `package.files` counts a dangling symlink as shipped (#337).
- **Pack refuses an any/noarch bundle holding a platform wheel** (#345).
- **install-deps:** build tools resolve from another config/link when theirs is
  unpublished (#321), and a build-tool variant clash is isolated instead of
  failing (#355).
- **submit-dag** weighs the glibc floor in its joint capability check (#332).

- **Builds, build logs and recipes are public reads.** `GET /v1/builds`,
  `/v1/builds/{id}`, `/v1/builds/{id}/log` (and its stream) and
  `GET /v1/recipes` no longer need a publisher token. As for builders and
  packages, a private org's jobs and recipes stay visible only to its members
  and admins. The `/builds`, `/build/{id}` and `/recipes` pages and the
  builder detail view work without a token.
- **Live builds and logs.** The builds page updates rows as jobs move, and
  the build detail page streams its log and status over `/v1/live/ws` (new
  `builds` topic and per-connection log tail; `build_jobs.updated_at`,
  migration 033). The log no longer travels over an EventSource with the
  token in its URL.
- **Fix: package pages showed no recipe.** The recipe section loaded only
  after `/v1/deps`, which on cvcpkg.org took 15 minutes. Whenever any recipe
  changed, it re-opened every pushed recipe bundle on the event loop, so it
  also stalled other responses: recipe archive downloads arrived truncated.
  The recipe now loads on its own. `/v1/deps` parses each bundle once
  (re-reading only those that change, in a worker thread), shares one rebuild
  among concurrent callers, and keeps the server's own recipes parsed between
  pushes. It also no longer includes a private org's recipes. The recipe
  section shows the commands to build, install and package from the downloaded
  recipe.

- **Activity feed.** Every publish, yank, unyank, delete and nuke is recorded
  per bundle variant (`package_events`, migration 032, backfilled from the
  catalog and tombstones). The new `/activity` page lists them with filters
  (action, package, platform, arch, org, actor) and pagination, and updates live
  over a WebSocket (`/v1/live/ws`). The data comes from `GET /v1/activity`. The
  feed is public except for a private org's events, which only its members and
  admins see.
- **Builders page.** Each builder card shows the builder's cvcpkg version and
  its host's CPU, memory and disk figures. Builders send the figures with each
  heartbeat (new `system` field, `builders.system_stats`). Clicking a card
  opens the builder's details: host, labels, capabilities, and its running and
  recent jobs. The page updates over the same WebSocket instead of reloading
  every 30 s.
- **Webhooks for the whole package lifecycle.** `package.yanked`,
  `package.unyanked`, `package.deleted` and `package.nuked` join
  `package.published`. Each fires once per package version and org, lists the
  variants that changed, and names the actor; delete and nuke add a `reason`
  (`deleted`, `gc`, `mirror-evict`, `manual`, `retention`). They come from the
  API, the `/admin` buttons, the cache GC, mirror eviction and the
  yank-retention GC.
- **Fix:** a chunked-upload publish fired `package.published` to the *global*
  webhooks even for an org's package (a private org's included), and never to
  the org's own webhooks. It is now scoped to the org, like a single-shot
  publish.
- **The single-file `cvcpkg` starts in about 1 s on Windows** (was 5-13 s).
  The standard recipes are now embedded in the executable as one zip in its
  Python archive, which the onefile bootloader never unpacks. They used to be
  ~3,300 data files that it unpacked to a temp dir on every start. A command
  that needs the default recipes (`validate`, `build`, `install`'s source
  fallback) writes them once per recipe set to
  `<cache>/bundled-recipes/<digest>/`, and later starts reuse that copy.
  `cvcpkg gc` leaves that directory alone. `--recipes-dir` and `./recipes`
  still override the defaults by name. Builders never read the bundled
  recipes, so a builder start no longer writes any recipe files.

---

## v2.5.1

**A release so the builder fleet updates.** Builders self-update only to a
server version newer than their own, and every prod deploy since v2.5.0 still
reported 2.5.0, so the fleet kept running v2.5.0 code (or older) through all
of the fixes below.  Notable for builders and populates:

- Dependency, toolchain and host-tool downloads retry transport errors (#215);
  a dropped connection to cvcpkg.org failed whole jobs (hatch-vcs-cp311 on
  2026-10-15).  Source downloads retry too, DNS failures included (#277).
- pack-all packs and publishes each recipe as soon as it is built, and one
  recipe failing to pack no longer stops the rest of the shard (#286).
  Recipes whose dependencies have no artifact or build for the host are
  skipped with the reason (#290).
- macOS: recipe scripts run with the OS's /bin/bash 3.2 (#264); bundles that
  load Homebrew are refused at pack time (#248); dylib compat ids are kept
  (#284).
- Tarball sources can pin per-platform artifacts (#288).
- Fixes to install (#252, #253, #259), the resolver (#251), publish/pack
  (#168, #210) and builder job reporting (#249, #217, #246).
- **Single-file builders self-update from the registry** (#303).  A builder
  running the single-file binary used to ignore `builder.update` -- it cannot
  pip-install itself -- so it had to be replaced by hand.  It now replaces
  itself with the registry's own `cvcpkg` package for its platform: the same
  path on Linux, macOS, Windows and the BSDs.  The download is checked against
  the registry's sha256 and the new binary must report the release it claims
  before it is swapped in; builders sharing a binary replace it once, and a
  fleet worker restarts through `builder fleet`.  See
  docs/cvcpkg-remote-builders.md.
- **ELF relocation on the BSDs** (#302, #303).  Without a patchelf the
  `$ORIGIN` pass did nothing and lib/*.so kept their scratch RPATH (readline
  and sqlite failed to pack on NetBSD); those now go to the in-place rewrite.
  Packaging uses cvcpkg's own `patchelf` package on Linux and FreeBSD, never
  the host's.  On NetBSD no patchelf rewrites an RPATH at all: growing one
  adds a PT_LOAD, which ld.elf_so refuses ("wrong number of segments").
- `recipes/cvcpkg` builds for FreeBSD and NetBSD too (#303).

---

## v2.5.0

**Recipes whose license forbids publishing them, starting with the NVIDIA
CUDA toolkit.** A recipe can declare `redistributable: false`. cvcpkg then
never packs or publishes it: `cvcpkg pack`, `pack --from-prefix`, `pack-all`,
build-all's server-cache push, `builds submit` / `submit-dag`, and the server's
`/v1/publish`, `/v1/builds` and `/v1/builds/dag` all refuse it. Instead, it is
built from its recipe on the machine that needs it, which downloads the
payload from the vendor under the vendor's license:

- `cvcpkg install` does this, and with no `--fallback-to-source` needed.
- The resolver's new `local_only` hook lets a published bundle depend on such
  a recipe; its own deps resolve from the catalog and the local builds run
  last, dependencies first.
- Builder agents build such deps into a job's prefix (#169, #172, #173).

- **`cuda-toolkit`** (new recipe): CUDA 12.5.1 nvcc, cudart and CCCL for
  linux-x86_64 and windows-x86_64, laid out under `<prefix>/cuda`. The
  archives come from NVIDIA's redist mirror and are pinned in `redist.lock`
  (`scripts/cuda_redist_lock.py`). On Linux, `-DCMAKE_PREFIX_PATH=<prefix>`
  alone makes `enable_language(CUDA)` and `find_package(CUDAToolkit)` work.
  On Windows, use `CUDAToolkit_ROOT` with Ninja, or `-T cuda=<prefix>\cuda`
  with Visual Studio.
- The 12 proprietary `nvidia-*-cu12-cp311` wheels (cuBLAS, cuDNN, cuFFT, ...)
  are `redistributable: false` and their bundles have been yanked from
  cvcpkg.org. `torch-cp311-cuda` still installs with this release: its NVIDIA
  wheels are pip-installed from PyPI on the installing machine. **Earlier
  cvcpkg versions can no longer install `torch-cp311-cuda`; upgrade.**
- **`msvc`** (new recipe): MSVC 14.44 and Windows SDK 26100 from Microsoft's
  own packages, also `redistributable: false`. It sits behind a license gate:
  a recipe can declare `license_acceptance`, and `cvcpkg install` then asks
  for consent, interactively, through the recipe's environment variable, or
  with `--accept-license <name>` (#170).

**Packaging and builder fixes**
- Relocatable bundles: executables are relocated, and a pack-time linkage gate
  checks them (#143). The reach pass and the gate count only runtime deps
  (#165).
- `pack-all` publishes the packed bundle, not the build-cache tarball (#154),
  and the server-cache push records the recipe's runtime deps (#152).
  `build-all --server-cache-push` likewise publishes the packed bundle, never
  the raw install tree (#203).
- The server backfills the runtime deps that catalog rows published before
  #152 were missing (#202).
- A builder no longer re-claims a job that already ran on it (#157).
- `cvcpkg install cvcpkg` / `install.sh` prefer the registry's own standalone
  build (#150, #151).
- `uninstall` identifies cached bundles by magic bytes, not filename (#175).
- Python recipe builds ignore the builder's user site (#153), and generated
  Python recipes scope each install layout to the platforms that produce it
  (#161).

**Recipes**
- Windows built from cvcpkg alone, with no vcpkg or Chocolatey:
  - gmp and mpfr native builds (#156);
  - openmp's MSVC build (#146);
  - llvm18 without the DIA SDK (#174);
  - wasmedge's LLVM, on Windows (#166) and Linux/macOS (#177);
  - shiboken6/pyside6 libclang only from llvm18 (#183).
- New recipes:
  - gfortran and gcc-runtime on Linux, with Fortran LAPACK in openblas and
    quadmath in boost (#155);
  - clang-format 18.1.8 and lcov 2.0 (#184);
  - Valkey 9.1.2 (#179);
  - Lua, LuaJIT and lupa (#180);
  - Node.js 22 LTS (#186);
  - Temurin 21 JRE and GreenMail 2.1.14 (#187).
- llvm, llvm18 and llvm20 declare zstd, drop terminfo, and ship clang's
  resource headers (#178). The fastapi/starlette/uvicorn/alembic/jinja2/...
  recipe columns are refreshed (#181).
- Other recipe fixes: openblas `NUM_THREADS=64` (#171); the macOS closure of
  the `cvcpkg` recipe (#163). The stale `tools/cvcpkg/` recipes copy is gone
  (#176). The POSIX/Windows site-packages globs are scoped per platform in
  439 Python recipes, with no rebuilds needed (#185).

**CI**
- Required checks fail closed for unlisted PR actors (#160, #162).
- populate-server counts noarch jobs (#159).
- New required `llm-gate` check rejects AI-assistant attribution in commits
  and PR text (#204).
- The Docker integration job queues on its one runner instead of being
  cancelled by its concurrency group (#205). The prod deploy job's timeout
  goes from 30 to 50 minutes (#206).

## v2.4.0

**refactor(deps)!** — breaking. The core install shrinks to `click` + `PyYAML`; every heavier dependency moves behind a role-keyed extra. Keeping the whole core off the heavy wheels is what lets cvcpkg install and run consistently across every target — down to the cosmo APE binaries and the thin-wheel BSD/Haiku platforms — where those wheels do not exist or do not build.

`pip install cvcpkg` now pulls **two** third-party packages, `click` and
`PyYAML`, and nothing else. `sqlalchemy`, `cryptography`, `httpx` and
`greenlet` were declared mandatory but are never imported by the client:
HTTP on the install path is `urllib.request` (`cvcpkg.storage`), integrity
is `hashlib.sha256`, signing is opt-in and lazily imported, and the ORM is
server-side. The four are now extras, named after the **entry point** that
needs them rather than the wheel they pull — `remote` (any command that
talks to a registry: search, `recipe pull`, `builds list/log/monitor`,
webhooks, token/user/org admin, `doctor --server`), `publish` (`publish`,
`recipe push`, `builds submit-dag`), `builder` (the agent), `signing`
(`key`/`sign`/`verify-sig`, `install --verify-signatures`), and `server`
(the ASGI stack plus SQLAlchemy, greenlet and httpx). The first three
resolve to the same `httpx`; the separate names exist so the error a user
gets names their own command's extra.

Reaching a command whose extra is missing is a single line — `httpx is
required to talk to a cvcpkg server over HTTP. Install it with: pip install
'cvcpkg[publish]'` — not a `ModuleNotFoundError` traceback; the guards live
in the new `cvcpkg.optional` and follow the shape `cvcpkg-server run` has
always used for a missing uvicorn. That covers the server's own
state-directory commands too — `cvcpkg-server bootstrap`, `token create`,
`audit log`/`verify` reach `cvcpkg.server.models` for its enums without ever
opening a database, so `pydantic` is guarded there alongside the SQLAlchemy
guards in `db.py`/`db_stores.py`. Two callers degrade instead: `cvcpkg
doctor --server` reports it as one failed check rather than aborting the
whole report, and `cvcpkg build`'s optional pull of the server recipe set
says so and falls back to the local recipes, since fetching them is an
optimization and not the command's job. `cvcpkg/__init__.py` also gained
a `PackageNotFoundError` fallback for `__version__`, so a source checkout on
`PYTHONPATH` imports at all — the pip-free install route.

`jsonschema` moves from the mandatory list into the `validate` extra,
keeping its `<4.18` ceiling (4.18 swapped pyrsistent for the Rust `rpds-py`,
which the BSD builders cannot build). Only `cvcpkg validate` and the
`kind: image` staged-tree gate construct a schema validator, and both go
through `require_jsonschema` now; recipe *installation* is untouched —
nothing on the catalog, resolve, download or lockfile path reads a schema —
so the core stays `click` + `PyYAML`, and `cvcpkg init`'s step 3 names the
extra up front when it is missing.

**What this changes for existing users:** nothing installs itself that you
did not ask for any more, so an environment that publishes, runs a builder,
signs, validates, or hosts the server must name its extra —
`pip install 'cvcpkg[publish]' / '[builder]' / '[signing]' / '[server,db]'`.
`pip install 'cvcpkg[all]'` is the one-word migration: a superset of what
the old mandatory list gave you.

**Every in-repo workflow that installs cvcpkg now names the extra its own
commands need**, because a bare `pip install .` on a host that then makes an
HTTP call is exactly the regression this split invites. Builder hosts take
`[builder,validate]` (`deploy-dev.yml`, `deploy-prod.yml`, `macos-drain.yml`
— `validate` because building a `kind: image` recipe checks the staged
`image.yaml` against its schema); the jobs that run `recipe push`/`push-all`,
`builds submit-dag`/`follow-dag` or `publish` take `[publish]`
(`deploy-prod.yml`, `populate-server.yml`, `populate-dev.yml`,
`pr-recipe-build-dev.yml`, `windows-build.yml`, `macos-build.yml`,
`linux-arm-build.yml`, `build-wasm-deps-catx03.yml`); registry admin
(`yank`/`unyank`/`nuke`) takes `[remote]` (`package-lifecycle.yml`, `bulk-yank.yml`);
`windows-recipe-check.yml` takes `[publish,validate]`; `ci.yml` installs
`[all]` so the unit suite's httpx/cryptography/sqlalchemy tests actually run
instead of silently skipping. The PyInstaller binaries take
`[remote,signing,validate]` (`[production,signing,validate]` for the
combined build) — those bundle a fixed closure, so the now-guarded imports
have to be present at build time and are named explicitly in the spec. Jobs
that only validate recipes or the dependency graph take `[validate]` and
nothing else (`cvcpkg-ci.yml`). `wheel-smoke.yml`, `cvcpkg-publish.yml`'s
live smoke and `source-fallback-ci.yml` deliberately stay on the bare
install: they are the standing proof that the core client works with no
extras at all. `docs/pypi-install.md` is the extras reference;
`docs/ci-cd-pipeline.md`, `docs/cvcpkg-remote-builders.md` and
`docs/cvcpkg-builder-wsl-debian.md` carry the same rule for hand-provisioned
hosts.

The point of the exercise is **minority platforms**: HaikuPorts ships
`click` 8.1.3 and `pyyaml` 6.0 but pins `cryptography` at 3.4.8 against a
`>=41` floor, `sqlalchemy` at 1.3.24 against `^2.0`, and has no `greenlet`
or `httpx` port at all — so four packages the client never imported were the
entire reason `pip install cvcpkg` failed there. It no longer does.

## v2.3.2

### Changed

- **`/v1/deps` no longer blocks the event loop or serialises against
  `/v1/search`.** The dependency-graph handler parsed hundreds of `recipe.yaml`
  files synchronously on *every* request, on the asyncio event loop, so it took
  several seconds and serialised the `/v1/search` the search page fires
  alongside it — this was the server-side root cause behind the v2.3.1
  search-page hang (the "separate server-side follow-up" that entry flagged).
  The whole response is now cached on a cheap `(local recipe signature, pushed
  signature)` and, on a miss, built in a worker thread (`asyncio.to_thread`), so
  the event loop stays free and a concurrent `/v1/search` returns immediately. A
  push bumps the pushed signature and busts the cache, so freshly pushed recipes
  still appear at once. No change to the response shape.

## v2.3.1

### Fixed

- **Search page no longer spins forever on a `?q=` query.** The v2.3.0 search
  fix loaded the recipe metadata (`/v1/deps`) in the background but still fired
  it *concurrently* with the first `/v1/search`; the server serializes the two
  and `/v1/deps` is slow (~10s for the full catalog), so `/v1/search` waited
  ~15s behind it and the results table sat on its spinner until the user cleared
  and retyped (by which point `/v1/deps` had finished and the retyped query ran
  alone in ~1s). The search now runs **first, on its own**, and metadata loads
  only afterwards — the query returns in ~1s. (The `/v1/deps` handler being slow
  and serializing against `/v1/search` is a separate server-side follow-up.)

## v2.3.0

Feature and fix roundup on top of v2.2.2 (all from cy-pca/cvcpkg).

### New

- **`cvcpkg repair`.** Detect and restore broken bundles in an installed prefix
  — a missing `manifest.yaml`, deleted payload files, or a corrupted download
  cache. It removes the broken bundle's footprint (protecting files a surviving
  package still owns and the shared metadata slot) and re-downloads +
  re-extracts from the lockfile-pinned archive. `cvcpkg verify` now also flags
  missing payload files, not just a missing/mismatched manifest, and points at
  `cvcpkg repair`. (#36)
- **Recipe-chosen archive format.** A recipe may set `package.archive_format`
  (`tar.gz` / `tar.xz` / `tar.bz2` / `zip`); when omitted the per-platform
  default is unchanged (`zip` on windows, `tar.gz` everywhere else). All formats
  are written deterministically, and the consumer sniffs magic bytes, so the
  choice only affects producer output. (#35)
- **`--install-dir` / `--version` flags on the installers.** `install.sh`
  (`curl … | sh -s -- --install-dir DIR`) and `install.ps1` (`-InstallDir`) can
  now be pointed at a custom location through the one-line pipe, with an
  explicit flag > env > default precedence and an actionable message when the
  target dir is not writable. (#31)
- **Path-based org package URLs.** Org-scoped package pages are now
  `/package/{org}/{name}`, with a 301 redirect from the legacy `?org=` form and
  a proper `canonical` / `og:url`. (#32)
- **`cvcpkg validate --strict-duplicates`.** A recipe name defined in more than
  one `--recipes-dir` now warns (later dir wins); `--strict-duplicates` makes
  such shadowing a hard error. (#34)
- **Package page shows `recipe.yaml`** above the Downloads section by default,
  with a clear note when a package has no recipe on file. (#33)

### Fixed

- **Search fires immediately on a `?q=` deep link.** Typing in the landing
  search bar and hitting Search navigated to `/search?q=…` but the query did
  not run until the field was cleared and retyped — the page awaited the slow
  `/v1/deps` recipe-metadata fetch before searching. Metadata now loads in the
  background (and re-enriches results when it arrives) so the search fires right
  away. (#41)
- **`PUT /v1/tags/{name}` no longer 500s.** `DbTagStore.update()` lazy-loaded
  `updated_at` after the async session had committed/expired it, raising
  `sqlalchemy.exc.MissingGreenlet` on the happy path. (#39)

### Internal

- Unit-test coverage raised above the 80% CI gate, including the previously
  0%-covered cloud storage backends (sftp / azure / gcs / s3-cli). (#38)
- Documentation pass: every user-facing CLI command verified against the actual
  CLI and corrected (e.g. `image ls`, `cache list`, `webhook register`,
  positional `yank`/`nuke` versions), plus stale `transfix/libcvc-deps`
  references updated to `cy-pca/cvcpkg`. (#40)
- CI hygiene: format-clean the RSS `github_url` line so the formatter gates pass
  on master. (#37)

## v2.2.2

First release cut from **cy-pca/cvcpkg**, the relocated home of cvcpkg (the
prior releases were cut from `transfix/libcvc-deps`, now frozen). Two critical
fixes:

### Package search renders results again

The dedicated `/search` page returned zero results for every query — a
perpetually stuck loading spinner. The shared `updateStatsFromResponse()`
wrote to the landing page's `stat-*` hero elements unconditionally, but the
`/search` page has no such elements, so `getElementById(...).textContent`
threw a `TypeError` that aborted `runSearch()` before `renderResults()` ran.
The `/v1/search` API and the `cvcpkg search` CLI were never affected. The
stat writes are now null-safe, and results render before the stats/filter
updates so a later throw can never block the table again. (#16)

### Installers point at cy-pca/cvcpkg

`install.sh` / `install.ps1` (and the site's GitHub links) hardcoded the
pre-relocation `transfix/libcvc-deps` for the release lookup and asset
download. They now default to `cy-pca/cvcpkg`, overridable via `CVCPKG_REPO`
for forks and staged cutovers. (#25)

## v2.2.0

### Multi-issuer SSO — cvcpkg as an OIDC client of several tx.wtf rings

A cvcpkg instance was an OIDC client of exactly one issuer. It can now be a
client of **several at once**, so users from different tx.wtf sites / federation
rings sign in to the same cvcpkg and land as distinct, correctly-scoped
identities.

- **Provider registry.** The bare `CVCPKG_OIDC_*` vars stay provider `default`
  (zero config change for cvcpkg.org). Additional providers are a single
  `CVCPKG_OIDC_EXTRA_PROVIDERS` JSON array — one env key, so the docker-compose
  `${VAR:-}` passthrough stays one line. `CVCPKG_OIDC_DISPLAY_NAME` labels the
  bare provider in the picker. Each provider carries its own group→role maps.
- **Login routing.** One provider → today's UX unchanged; several → a provider
  picker on `/login` and the `/admin` sign-in, and `cvcpkg login --provider`
  (or an interactive pick / the browser picker). New `GET /v1/auth/providers`
  and `cvcpkg auth providers`.
- **Mix-up safe.** The chosen provider rides the *signed* transaction cookie; the
  shared `/auth/oidc/callback` resolves the issuer from that cookie, never from
  its own query, so a code from issuer A can't be paired with provider B.
- **Loud config guards.** Two providers may not share an issuer; a malformed or
  half-configured extra provider fails the boot instead of silently vanishing.
- **No migration.** Identities were already namespaced by `(issuer, subject)`.

Also fixed: `cvcpkg login` with no `--role` exited with a click usage error under
click ≥ 8.1.6 (an empty-string default is not a valid `Choice`); it now defaults to
"no role requested". And a mistyped `cvcpkg login --provider <id>` is now rejected
up front instead of hanging until the browser-login timeout.

### Front page: fast landing, dedicated search, cvcpkg branding

The landing page ran a full `/v1/search` (every variant in the registry, with
facets and a size rollup) plus a `/v1/deps` fetch on first paint, so it got
slow to load as the catalog passed a few thousand packages.

- **The landing page no longer lists packages.** It is a branded overview that
  loads instantly with a single cheap `/healthz` call; the hero search box
  navigates to the search page rather than querying inline.
- **Package search moved to its own `/search` page** (a new "Search" nav item).
  It opens in an empty rest state and issues no `/v1/search` until you enter a
  query or pick a platform/architecture/tag filter. Recipe metadata (`/v1/deps`)
  is fetched lazily before the first query, not on load. A `?q=` deep link from
  the landing box opens directly on results.
- **cvcpkg branding.** The neon flaming-package mark replaces the placeholder
  gears as the site icon; the horizontal banner is the navbar logo; a hero
  graphic anchors the landing page. The palette adopts the house neon magenta +
  cyan on translucent navy panels with Gentium Book Basic / VT323 type and pill
  buttons, matching cyberpcangel.com. The brand images ship as package data and
  are served self-hosted from `/assets/`.

## v2.1.0

### Sign in to cvcpkg.org — the non-admin web surface

cvcpkg had no authenticated surface for anyone but an admin. The public site
had no login at all (`grep -ci login landing.py` → 0), and the OIDC callback
refused every role but `admin` — so `publisher` and `reader` were real on the
server and inert in a browser. The only way to get a publish token was for an
admin to mint one by hand and courier the secret out of band.

**That handoff is gone.** Sign in with tx.wtf, land on `/account`, mint your
own token. The secret is shown once and is not re-shown on reload.

**Machine tokens are untouched.** Every existing `cvctok_` keeps
`principal_id = NULL` and behaves exactly as before. There is no backfill.

Added:
- `/login`, `/logout`, `/account` — identity, role (and *why* you have it),
  organizations, API tokens with mint/revoke, and signed-in devices with
  per-session revoke.
- `/admin/principals` — the operator view: disable/enable an identity, sign it
  out everywhere, and an **orphan-membership panel** listing `org_members`
  rows that name neither a live token nor a principal. Each is a live grant
  attached to a name nobody holds; collectively they are exactly the set a
  name-claim could have hijacked.
- `principals` + `sessions` tables and a nullable `tokens.principal_id`
  (migration 028).
- Per-form CSRF tokens (`server/csrf.py`), bound to both the session and the
  form's purpose, so a token lifted from one form cannot drive another.

Two bugs the pre-release audit caught in this same work, worth naming because
both were invisible to a green suite:
- `list_tokens()` omitted `principal_id`, so every "is this token mine?" filter
  matched nothing — an empty token list, a 404 on self-revoke, and a rename
  guard that never fired, which would have let a handle owning packages and org
  memberships be freed and re-allotted.
- A disabled principal could still complete a login; a disabled *admin* got the
  dashboard back and could re-enable themselves.

How SSO identity reaches existing authorization, unchanged:
- A token minted by the account page is named `<principal>.<label>` and
  **verifies as its principal**: a row named `joe.laptop` presents as
  `TokenRecord.name == "joe"`. That one substitution is why none of the ~11
  org-membership predicates and ~105 `actor.name` sites needed editing.
- Two properties a bare `cvctok_` never had: disabling a principal invalidates
  every token it minted, immediately; and a delegated token is clamped to the
  principal's *current* role, so a demotion at the identity provider takes
  effect on the next request rather than at token expiry.

Security — name allocation is the boundary:
- A principal is resolved on `(issuer, subject)` and **never** on email.
  `tokens.email` is unvalidated free text, self-settable via
  `PATCH /v1/tokens/{name}/email`, carries no unique constraint, and is
  readable through an endpoint documented as requiring no authentication.
  Linking on it would hand an account to whoever typed the address first.
- The set of unavailable names includes **revoked** tokens.
  `uq_tokens_active_name` is a *partial* index and `revoke()` flips one boolean
  without touching `org_members`, so a revoked token named `joe` still owns
  every org it ever joined. It also includes orphan `org_members` rows (which
  `add_member` writes with no existence check) and `build_jobs.claimed_by`.
- The reverse guard: `DbTokenStore.create()` refuses a name already held by a
  principal. With `CVCPKG_REGISTRATION_MODE` defaulting to `open`, without it
  anyone could `POST /v1/register` a token named after a principal and inherit
  its memberships.
- The admin cookie is untouched. `/account` uses a **separate** `cvcpkg_session`
  cookie at `path=/`; `cvcpkg_admin_session`, `_has_admin_session` and the
  dashboard's `role != "admin"` gate are unchanged, so a publisher holding a
  session still gets the sign-in page at `/admin`.
- The session cookie is a reference, not a bearer: the row is re-read and the
  principal re-checked on every request, which is what makes signing out end
  the session rather than merely asking the client to forget it.

Also fixed here: the three **pre-existing** admin POSTs
(`/admin/tokens/create`, `/admin/tokens/revoke`, `/admin/packages/action`) had
no CSRF protection at all. `SameSite=Lax` does stop a cross-site form POST in
current browsers, but it is a browser-version-dependent mitigation that does
not cover a same-site subdomain — and those three routes create credentials,
revoke credentials and delete packages. They now carry and require a token on
the same terms as the account surface. Since the admin cookie signs only an
expiry and has no session id to key on, the binding is derived from a hash of
the cookie value: per-session and per-browser, without putting the cookie
itself into page HTML.


### Sign in with tx.wtf (OIDC) — server enablement

The Phase 13 OIDC relying-party code has shipped since 2026-07 and was
**dormant in production**: the route was registered and visible in the OpenAPI,
but `docker-compose.production.yml` never passed the four `CVCPKG_OIDC_*`
variables through, so `/admin/oidc/login` answered 404 on cvcpkg.org. This
release makes it configurable and hardens the flow it turns on. It is the first
stage of wiring cvcpkg.org logins to the tx.wtf federated identity network so
interactive users stop hand-cutting API tokens; see
`docs/roadmap/txwtf-sso-and-cli-login.md`.

**Machine tokens are untouched.** `cvctok_` secrets, their hashing, and every
`CVCPKG_TOKEN` code path behave exactly as before.

Added:
- `CVCPKG_OIDC_*` pass-through in `docker-compose.production.yml`, documented in
  `.env.production.example`.
- `CVCPKG_OIDC_READER_GROUPS` — a `reader` tier below `publisher`.
- `CVCPKG_OIDC_DEFAULT_ROLE` — role for a user who authenticates but matches no
  group. Empty keeps refuse-by-default; `reader` gives them a named identity an
  org owner can add to a private org by handle.
- `CVCPKG_COOKIE_SECURE` (default on) and `CVCPKG_SESSION_TTL_SECONDS`.
- `auth.derive_key()` — purpose-scoped subkeys, so new signing purposes stop
  sharing the raw token-hashing key. `_hash_token` deliberately still uses the
  raw key; rekeying it would invalidate every live token in the fleet.
- `AuditAction.login` / `.logout`. Dashboard logins were previously recorded as
  `token_create` ("closest existing action"), making a sign-in indistinguishable
  from minting a credential.

Fixed:
- **The OIDC `nonce` was minted, sent, and never checked**, so a callback was
  never bound to the login attempt that started it. It is now verified against
  the `id_token`, and a missing nonce claim is refused rather than accepted.
- **Session and login-transaction cookies now carry `Secure`.** The flag is *not*
  derived from the request scheme: Apache terminates TLS for cvcpkg.org and uvicorn
  is not run with `--proxy-headers`, so the app sees `http` there and scheme-sniffing
  would silently disable `Secure` in production. A genuinely plain-http deployment
  sets `CVCPKG_COOKIE_SECURE=0`, and is warned once if it has not — gated on the
  absence of `X-Forwarded-Proto: https`, so the warning stays quiet behind a proxy.
- **The server refuses to start on an unsafe group map.** Mapping a role to a
  group every account already holds (`user`, `everyone`, ...) made "can sign up
  on the IdP" equivalent to that role, and looked healthy until someone tried
  it. `CVCPKG_OIDC_DEFAULT_ROLE=admin` is refused for the same reason.
- `POST /admin/tokens/create` applied only `.strip()` to the token name while
  every other mint path validates it. A token name is the org-membership key,
  so a name with a space could not be typed back into `cvcpkg org add-member`.
- `cvcpkg install --token` set `CVCPKG_TOKEN` in `os.environ` and never removed
  it, leaving one command's credential as the standing configuration for
  everything after it in the same interpreter (same shape as the #497 leak).
- `registries.yaml` holds a bearer token per federated registry and had no
  permission check; it now warns when group/world-readable, as env files do.
- OIDC discovery was fetched twice per login; it is now cached for 5 minutes.

### Security — builder endpoint authorization

The builder endpoints authenticated a *role* (publisher or admin) and then trusted
whatever `org_slug` or `builder_id` the caller named. `POST /v1/builds` has always
gated org attachment with `is_member`; the builder endpoints never did.

- **Registering a builder for another org.** `POST /v1/builders/register` passed
  `body.org_slug` to the store with no membership check, so any publisher token
  could attach a builder to any organization. Now gated by the same check
  `/v1/builds` uses.
- **Re-registration takeover.** `register()` upserts on `(name, org_slug)` and
  reassigned `registered_by` unconditionally, so any publisher could re-register
  another builder's name, become its owner, and pass every ownership check below.
  A non-owner re-registration is now `409`; owner re-registration (what every
  builder does on restart) is unchanged.
- **Rewriting another builder's `served_namespaces`.** `PATCH /v1/builders/{id}`
  had no ownership check, and `_choose_builder` trusts `served_namespaces` for
  namespace isolation.
- **Assuming a builder's identity.** `heartbeat`, `next-job` and the WebSocket
  authenticated any publisher token against any `builder_id` — so a caller could
  take a job dispatched to another builder, or displace the real builder's socket,
  since the WS handler assigned `_ws_builders[builder_id]` unconditionally.

The rule now: attaching to an org needs membership in it; *being* a builder
(`heartbeat`, `next-job`, `ws`) is owner-or-admin only; *administering* one
(`PATCH`) additionally allows a member of the owning org. `DELETE` was already
admin-only.

No live builder changes behaviour: every registered builder has a non-empty
`registered_by`, and `cvcpkg builder run` uses one token for its whole lifecycle.
Token rotation is safe, since it preserves the name `registered_by` records. A
builder re-tokened under a *different name* gets a 409 on its next registration,
with an error saying to re-register with the owning token or ask an admin to
unregister it.

## v2.0.3

### `cvcpkg install` can install from a private org (2026-09-08)

`cvcpkg search --org <o> --token <t>` could see a private org's packages, but
`cvcpkg install <o>/<pkg>` failed with *"no bundles found in catalog for this
platform tuple."* The server was never the problem — `/v1/catalog` already
includes a private org's bundles for an authenticated member, and the
`/v1/download/…` archive is served to members too. The gap was on the client:
`search` sent the bearer token, but the install-time catalog fetch
(`catalog._fetch_url`) and archive download (`installer._download_from_url`)
went through the tokenless storage backend, so they got the anonymous catalog
(private bundles absent) and, past that, a 404 on the archive.

Install now attaches `Authorization: Bearer $CVCPKG_TOKEN` to both the catalog
fetch and the archive download, and `cvcpkg install` gains a `--token` flag for
parity with `search`. The token is **origin-scoped**: it is sent only to the
configured server/root host (`CVCPKG_SERVER_URL` / `CVCPKG_ROOT_URL`) and never
to the public GitHub Pages catalog fallback, a third-party mirror, or an
artifact CDN — any of which can legitimately appear as a fallback catalog URL,
an `archive_url`, or a `mirror_url`. A URL to our own server is also
canonicalised to the server's scheme before the token is attached, because the
server sits behind a TLS-terminating proxy and emits `http://` download URLs —
so the bearer is never sent over cleartext http (and the private archive is not
fetched anonymously).

```sh
CVCPKG_TOKEN=<tok> cvcpkg install myorg/private-pkg --prefix ./deps
# or
cvcpkg install myorg/private-pkg --token <tok> --prefix ./deps
```

## v2.0.0

Major release: production daemon with database backend, the
`cvcpkg.org` registry, distributed build infrastructure, and wasi
support.

### Secrets can live in an env file instead of `argv` (2026-08-12)

`--token` is accepted by 63 options across the CLI, and every one of them
puts the bearer token in the process command line — which is not private.
On Linux `/proc/<pid>/cmdline` is world-readable, so any local user could
read a builder's publisher token out of `ps`; Task Manager's command-line
column does the same on Windows. Exporting `CVCPKG_TOKEN` kept it out of
`argv` but only moved the problem, since the value still had to be written
somewhere to get there — in practice a plaintext literal in the launcher
script.

`cvcpkg --env-file PATH` (and `$CVCPKG_ENV_FILE`) reads `KEY=VALUE`
settings before Click resolves any option's environment variable, so one
option serves **every** existing `--token` site without changing any of
them. `./.cvcpkg.env`, `~/.config/cvcpkg/env` and `/etc/cvcpkg/env` are
read automatically when present (plus `%APPDATA%`/`%PROGRAMDATA%` on
Windows).

**Fully backward compatible:** `--token` and `CVCPKG_TOKEN` work exactly
as before and take precedence, so a file can only supply a value nobody
else set — adding one cannot change what an existing deployment resolves
to.

The format is deliberately inert: `#` comments, an optional `export`
prefix so the same file can be `source`d by an existing shell wrapper,
optional quoting, and **no** `$VAR` interpolation or command substitution
— a file whose only job is to hold credentials should not be able to
execute anything, and a token containing `$` must survive verbatim.
cvcpkg warns (but does not refuse) when the file is group/world-readable.

The variables are scoped to the invocation and removed when the command
finishes, so an embedding process (the server, a test session) never
inherits one command's env file as standing configuration — the same leak
removed from `--trust-mirror` in the entry above.

### Every advertised platform is now buildable (2026-08-05)

Recipes could advertise a platform they were incapable of building. A
dependency-closure audit found **95** `(recipe, platform)` pairs where a
recipe declared platform *P* while a dependency that applies on *P* had
no build for it — `gtk4` claimed windows without cairo/pango/gdk-pixbuf,
`numpy` claimed macos without openblas, CPython claimed
windows/wasm/wasi/cosmo while depending on readline and ncurses. Those
jobs could only fail once a builder claimed them, hours into a DAG.

That count is now **zero**, and `scripts/validate_all_recipes.py` runs in
CI so it stays there. Each violation was resolved by adding the missing
build where it is genuinely feasible, or by scoping the dependency edge
with `platforms:` where it is not.

New platform coverage (+72 build-matrix entries), each checked against
upstream or the platform's own ports tree rather than assumed: openblas
on macOS; the cairo/fontconfig/pango/gdk-pixbuf stack on Windows;
yaml-cpp and libpulse on all three BSDs; wayland and wayland-protocols
on OpenBSD/NetBSD; LLVM on NetBSD; portaudio and pipewire on FreeBSD;
wireguard-tools on NetBSD; and sqlite, libffi, zstd, abseil, libpng,
freetype and expat across the wasm/wasi/cosmo targets. Two new recipes:
`brotli` and `gperf`.

Cases that look like gaps but are not are now documented where they
live, so they are not re-investigated: portaudio on OpenBSD/NetBSD needs
sndio, which upstream has no host API for; libffi has no wasi backend
(its wasm trampolines are built with `EM_JS` and need a JavaScript
engine — the same reason CPython ships WASI without `_ctypes`); ncurses
and readline have no MSVC port.

**Hermeticity.** `gtk4` on Windows no longer shells out to gvsbuild, a
pipx-installed meta-builder that fetched and compiled its own copy of
the entire GTK stack — the bundle was non-hermetic and shipped
gvsbuild's cairo/pango/gdk-pixbuf rather than the packages the recipe
declares. It is now Meson + MSVC with the Win32 backend. `fontconfig`
declares the `gperf` dependency it always needed, instead of taking the
builder's system copy or fetching a meson wrap at setup time. `numpy`
links cvcpkg's own OpenBLAS on macOS rather than the system Accelerate
framework, and its cp313t column moved from a prebuilt PyPI wheel to the
same from-source build as the other three — that wheel carried the
vendored-OpenBLAS defect that breaks `import numpy` in a merged prefix.
`wand` now publishes on macOS and the BSDs.

**Removed: `dragonflybsd`.** It was canonical in `platform.py` and
offered in the server UI, but was never added to the recipe schema's
platform enum — so no recipe could declare it and such a host resolved
zero packages while the UI advertised the platform. DragonFly now
detects as `freebsd` in compat mode, the same treatment GhostBSD gets.

### Recipes can run their tests inside a throwaway VM (`test.vm`) (2026-08-04)

cvcpkg already had a test hook: `test.script`, a shell script run **on the
builder** after the build and before packing. That is right for a library and
worth nothing for an image package, whose artifact is an opaque guest disk — a
builder-side script can prove the file exists and `qemu-img info` parses it,
which is exactly the "trust me, it boots" that shipped a descriptor claiming
`disk_bus: virtio-blk`, a value that general-protection-faults the Haiku guest
before userland.

New `test.vm` block, additive alongside `test.script`: cvcpkg boots the image
the recipe just staged in an ephemeral VM, asserts, and destroys it.

```yaml
test:
  vm:
    requires_capabilities: [incus]   # skipped (green) without it
    hypervisors: [incus]             # incus | lxd; classic lxc cannot boot a VM
    image: self                      # the image THIS build produced
    connect: ssh                     # ssh | agent
    ssh: {key_env: HAIKU_BUILDER_SSH_KEY}
    script: vm-test.sh               # runs INSIDE the guest
    boot_timeout_seconds: 720
```

cvcpkg owns the lifecycle: reap stale `cvcpkg-vmtest-*` instances *and images*,
`image import` the importer metadata + disk, `init` a VM sized from the
descriptor's `boot:` block, pin the root device to `boot.disk_bus`, start, wait
for reachability, run the guest script, destroy both.

* **Skip, never fail.** Both gates (`requires_capabilities`, "any hypervisor
  present") and a missing SSH key resolve to a reported SKIP *before* any
  hypervisor state exists. `lxc` alone also skips, saying why: classic LXC is
  daemonless and containers-only, so it can never boot a VM.
* **The VM and its imported image are always destroyed** — pass, guest failure,
  boot timeout, a hypervisor CLI that throws, `SystemExit`, Ctrl-C and SIGTERM
  (converted to an exception so the `finally` runs at all). The teardown itself
  runs with SIGINT/SIGTERM *deferred and re-delivered afterwards*, because it is
  two deletes and a signal landing between them would strand the expensive half;
  it never raises, so a broken log callback cannot abort it midway. Teardown
  draws a *fresh* budget, so an expired deadline never means a leaked VM; a
  failed delete is reported, not swallowed.
* **The imported image is reaped, not just the instance.** Instances and images
  are separate namespaces — `incus list` never mentions an image — so cleanup
  asks twice (`incus list` **and** `incus image list`). Each run copies a
  multi-gigabyte qcow2 into the daemon's store; a reaper that knew only about
  instances would fill a builder's disk one image per killed run. A
  partially-failed import counts too: teardown asks the daemon whether the alias
  exists rather than trusting the exit code, which also keeps a bad metadata
  tarball (where nothing was created) from printing a false leak warning.
* **A SIGKILL is covered by the next run's reaper** — instances and image
  aliases are both named `cvcpkg-vmtest-<pkg>-<owning-pid>-<random>`, instances
  are reaped before images (a daemon refuses to delete an image an instance
  still uses), the prefix is the only handle so a pre-existing instance or image
  can never be touched, and something is only reaped when its owning process is
  gone — so two builds sharing a daemon cannot destroy each other's live VM or
  the image it booted from.
* **Bounded time.** One wall-clock deadline covers the phase and every
  subprocess call draws its timeout from it.

`test.vm.requires_capabilities` is deliberately NOT the recipe-level key: that
one gates whether the resolver will *select* the package, and an image must stay
installable on a host with no hypervisor.

Also new: `cvcpkg image test <name>` runs the same engine by hand against an
installed image (exit 0 on pass or skip, 6 on failure), and `haiku-image` now
ships `vm-test.sh`, which checks the guest is Haiku on x86_64, has a writable
BFS root, and can actually compile and run a binary.

### Image packages live in `share/<name>/`, and `cvcpkg image` finds them (2026-08-04)

The first image recipe (`haiku-image`, still unpublished) staged four files at
the ROOT of `CVC_INSTALL_DIR`. Because cvcpkg merges a staged tree into the
prefix preserving relative paths, `cvcpkg install haiku-image` dropped
`$PREFIX/metadata.yaml` and `$PREFIX/README-import.md` at the prefix root —
generic names that the *second* image package would have collided with on both.

An image package now owns exactly one directory, named after itself, with
**role-based** filenames:

```
<prefix>/share/haiku-image/image.yaml            canonical descriptor
<prefix>/share/haiku-image/image.env             POSIX KEY=value shim
<prefix>/share/haiku-image/disk.qcow2            the payload
<prefix>/share/haiku-image/SHA256SUMS            `sha256sum -c` format
<prefix>/share/haiku-image/README.md
<prefix>/share/haiku-image/incus/metadata.yaml
<prefix>/share/haiku-image/incus/metadata.tar.xz
```

The directory is the package name (unique in the catalog keyspace), so N images
co-install; the filenames carry no version and no guest arch, so
`$CVCPKG_PREFIX/share/<package>/disk.qcow2` is derivable from the package name
alone. Guest axes live in the package NAME and in `image.yaml`, never in a
filename. Only one payload format ships — the duplicate anyboot `.iso` is gone
(recover it with `qemu-img convert -f qcow2 -O raw`).

New `recipe.kind: image` is **enforced, not a label**: `cvcpkg validate` checks
`package.files` and `cvcpkg pack` checks the real staged tree, refusing any
image recipe that stages outside `share/<name>/` or ships no schema-valid
`image.yaml` (new bundled `image-schema.yaml`).

New `cvcpkg image` command group (core, no extra) — `ls`, `path`, `dir`,
`info`, `env`, `verify`, `export`. Discovery is a glob over
`<prefix>/share/*/image.yaml`: no index, no server call, no new state file.
`image path` prints one absolute path and exits 3 (not installed) or 4 (no such
role) so a `/bin/sh` provisioner can branch without parsing output; `image
verify` re-hashes the bytes **on disk**, which the installer's download-time
sha256 does not cover. `--prefix` now honours `CVCPKG_PREFIX` everywhere, and
build scripts get `CVC_FULL_VERSION`/`CVC_REVISION`.

```sh
export CVCPKG_PREFIX=/srv/cvcpkg/images
cvcpkg install haiku-image && cvcpkg image verify haiku-image
DISK=$(cvcpkg image path haiku-image)
META=$(cvcpkg image path haiku-image --role incus-metadata)
eval "$(cvcpkg image env haiku-image)"
incus image import "$META" "$DISK" --alias haiku-builder
incus config device set haiku-b1 root io.bus="$CVCPKG_IMAGE_DISK_BUS"
```

**Scope, so this entry is not read as more than it is:** what landed is the
*packaging* layer — layout, descriptor, schema, `cvcpkg image`, `test.vm`.
`haiku-image` itself is still unpublished **and does not yet boot** (truncated
anyboot partition table, an SSH key that was never actually injected, no
`sshd` launch job); repair is on a separate branch. The command lines above
are the contract, not a transcript of a working deployment — see
`docs/image-packages.md` and `recipes/haiku-image/README-import.md`.

### Upload cap is a setting, default 4 GiB (2026-08-02)

The server's maximum bundle size is now a first-class setting —
`cvcpkg server run --max-upload-bytes 8GB` or `CVCPKG_MAX_UPLOAD_BYTES` —
and defaults to **4 GiB**, up from a hard-coded 1 GiB (documented as
512 MiB; the docs had drifted from the code). Sizes accept a byte count
or a human suffix (`4GB`, `512MB`, `2TB`; units are binary), shared with
`CVCPKG_POPULATE_MAX_PACKAGE_BYTES`, which previously crashed on any
suffixed value. The old cap was below the largest bundles we publish —
the CUDA runtime wheels unpack to ~2 GiB. Parsing lives in
`cvcpkg.server.limits` so the CLI can validate the flag without importing
`server.app` (which would freeze its cap before uvicorn loads it); an
unparseable env var falls back to the default rather than refusing to
boot, while a bad CLI flag is a startup error.

### Python packaging: uniform per-interpreter columns (2026-08-02)

Every Python package is now a **per-interpreter column recipe** —
`<name>-cp311 / -cp312 / -cp313 / -cp313t` — pure wheels included; the
bare-name + cross-interpreter copy-fanout model (#389/#390) is retired.
Each column depends on *its* interpreter and its deps' matching columns
and installs only into its own `lib/pythonX.Y[t]/site-packages`, so the
dependency graph is honest (no more `python312` dragged in by every pure
wheel next to `python311` from every abi3 one), the free-threaded
(no-GIL) column exists wherever the whole closure ships cp313t wheels,
and adding a future `python314` is a new column, not a global republish.
`tools/gen_python_recipes.py` emits the entire matrix (poetry.lock
closure + curated seeds: pytest, black, setuptools, sympy, ...), prunes
non-viable columns transitively, and script-shipping tools declare
`provides: [<base>]` (mutually exclusive per prefix; bare-name install
sugar). The C++ `protobuf` recipe, clobbered by the generated Python
wheel of the same name, is restored — the Python columns are
`protobuf-cpNNN`. `pyside6`/`shiboken6`/`triton`/`nvidia-*-cu12` are
renamed to their explicit `-cp311` columns; `ruff` is a plain tool (no
runtime interpreter dep); `python313t` finally packages pip (as
`pip3.13t`). A new `python` meta owns `bin/python`/`bin/pip`; the
`python3` meta keeps `python3`/`pip3`/`python3-config`; activate no
longer synthesizes interpreter aliases behind the metas' back. The
concrete matrices grew to match: torch-cp312/-cp313/-cp313t (CPU wheels;
the cp313t column is gated by the build's no-GIL assertion),
h5py-cp312/-cp313 (from source vs cvcpkg hdf5), pyside6/shiboken6
-cp312/-cp313 (from source vs cvcpkg qt6), and wand-cp313t.

> **The project is now named `cvcpkg`.**  The `libcvc-deps` name is
> retired; the PyPI distribution, CLI, server, and recipe archive are all
> `cvcpkg`.  Backward-compat shims remain where downstream depends on the
> old name (e.g. `find_package(libcvc-deps)` still works via a generated
> compat config).

### Release-readiness changes (cvcpkg tool)

Landed while hardening 2.0.0 for its first `pip install cvcpkg`:

- **`cvcpkg doctor`** — diagnose the local toolchain (Python, CMake, Ninja,
  a C/C++ compiler, git) and optional server reachability.
- **`cvcpkg init`** — scaffold a schema-valid recipe (recipe.yaml + build
  scripts) for cmake / meson / autotools.
- **`cvcpkg upgrade`** — upgrade installed components to newer catalog
  versions in place (with `--dry-run`), updating the lockfile.
- **`cvcpkg install --require-signatures`** — enforce a valid Ed25519
  signature on every installed archive (unsigned = hard failure).
- **`cvcpkg install` writes `cvcpkgConfig.cmake`** into the prefix so
  downstream `find_package(cvcpkg CONFIG REQUIRED)` (or the `libcvc-deps`
  compat name) works with no manual `CMAKE_PREFIX_PATH`.
- **Server admin CLI** — `cvcpkg server stats`, `cvcpkg server backup`
  (sqlite/pg_dump/mysqldump), and `cvcpkg builder logs`.
- **Duplicate-publish gating** — the server returns HTTP 409 on a duplicate
  package variant at the store layer.
- **PostgreSQL recipes** — `postgresql-server` and `postgresql-client`
  (built from the same Meson tree as `libpq`).
- **Packaging fix** — the PyPI publish workflow now actually bundles the
  recipe files into the wheel/sdist (previously shipped zero recipes), and
  verifies the built wheel contains them.
- **Stability fixes** — file-backed SQLite uses `NullPool` to end an
  intermittent "no active connection" teardown failure on macOS CI; the
  Docker integration job's recipes mount was corrected.

### Highlights (2.0.0 infrastructure, 2026-06-06)

The `cvcpkg` 2.0 line is the first to drive the public
catalog at <https://cvcpkg.org> from a SQLModel/Alembic-managed
database (Phase 1 of the [cvcpkg 2.0 roadmap](docs/roadmap/cvcpkg-2.0.md))
instead of YAML state files.

### Highlights

- **Distributed builder fleet.**  Builders run as long-lived agents
  (`cvcpkg builder run`) that pull jobs from the server, register
  per-platform capabilities, and publish artifacts back via the
  authenticated API.  Includes builder pause/resume/cancel, log
  streaming (`builds follow-dag`), `--wait` flag on submit, and a
  public read endpoint for builder info.
- **DAG-based build submission.**  `builds submit-dag` schedules a
  full dependency graph across builders, with correct platform/arch
  pairing and automatic cross-platform fan-out.
- **wasi platform support.**  New `wasi-sdk` recipe (cross-toolchain)
  and wasi/wasm32 builds of zlib, zstd, libjpeg-turbo, yaml, xz, lerc.
  `--cross-platform wasi` is recognized by the builder.
- **CLI restructure.**  The 6,700-line `cli.py` is split into a
  `cli/` package (`_build`, `_publish`, `_install`, `_catalog`,
  `_server`, `_builder`, `_builds`, `_signing`, `_recipe`,
  `_webhook`, `_cache`, `_helpers`).  Default `recipes-dir` resolved
  automatically with a `--no-default-recipes` opt-out.
- **Recipe schema gains `runtime` deps and `cross_toolchain`.**
  `emsdk` advertises wasm tooling; the builder auto-installs host
  tools for cross-compilation and splits build vs. runtime deps.
- **Server URL fix.**  `_install_deps` / `_install_cross_toolchains`
  now prepend the configured server base URL to relative
  `/v1/download/...` paths (fixed 27 build failures from
  `UnsupportedProtocol`).
- **Dev/prod deploy workflows.**  Builder restart + auto-update in
  `deploy-dev` and `deploy-prod`; `sandipaws` Windows builder
  registered and updated via SSH from `star-00`.
- **Recipe push permissions.**  `publisher` role can now push
  recipes; populate workflow timeout raised to 2h.

No new upstream component pins are introduced; v2.0.0 is purely an
infrastructure release.  The catalog at `https://cvcpkg.org/v1/catalog`
serves the recipes pinned at recipe commit `6631d06`.

---

## v1.6.1 (2026-05-31)

BSD-focused bug-fix release.

- **NetBSD port complete.**  Port 7 failing recipes to NetBSD; add
  `lz4`, `readline`, `gettext` recipes.  Use `pkg_add`, fix static
  OpenSSL linking, ship a `python3` shim.
- **NetBSD CI hardening.**  Serialize `pkgin` with `flock` (lockf
  is FreeBSD-only) to prevent concurrent DB corruption, retry
  `pkgin update` if the DB is empty after the first attempt.
- **BSD post-build fixes.**  Export `BASE_DIR` to `GITHUB_ENV` so
  it's visible across steps under `set -u`; use `-G 'Unix Makefiles'`
  instead of `-G Ninja` for the marker cmake step because
  `/usr/pkg/bin/ninja` on NetBSD is the IRC client.

---

## v1.6.0 (2026-05-29)

Windows self-hosted runners, recipe schema upgrades, and the
`platform: any` recipe class.

- **Self-hosted Windows runners for static builds.**  Bypass GitHub
  runner timeouts on heavy Windows static jobs.  Bootstrap `vcpkg`
  if missing; add Git bash, PowerShell 7, Chocolatey bin, and the
  full Machine PATH to `GITHUB_PATH` so bash steps see choco-installed
  Python/NASM/etc.
- **`platform: any`.**  Recipes that produce platform-independent
  artifacts (header-only, scripts) declare `platform: any` and are
  built once and reused everywhere (#72).
- **Recipe schema: runtime deps + cross_toolchain.**  Build vs.
  runtime deps split; auto-build host tools for cross-compilation;
  cross-toolchain discovery so a single submit can pull in `wasi-sdk`,
  `emsdk`, etc.
- **Build log streaming.**  `cvcpkg builds follow-dag` tails live
  build logs over the server API.
- **wasm32 cross-compilation hardening.**  Multiple OpenSSL wasm
  fixes (AR line splitting under cmd.exe's 8191-char limit, keep
  `sh.exe` on PATH, only post-process `Makefile` not
  `configdata.pm`).
- **Server cache backend.**  Modernized release pipeline, server-side
  build cache, Qt6 built from source on all platforms.
- **Daemon mode + graceful shutdown + org ACL.**  Server runs as a
  proper daemon; identifier validation tightened.
- **riscv64 + other architecture support.**  Architecture is now a
  free-form string throughout cvcpkg (`riscv64`, `ppc64le`, `armv7l`,
  …) so new targets land without schema changes.
- **Landing page redesign.**  Tags as filter dropdown + grid view
  (#78); mobile burger menu fixes (#73); configurable branding;
  guide page; package detail pages show install `--prefix`; package
  count is distinct names.
- **Publish workflow.**  Merge `push` into `publish`; `publish`
  accepts recipe names instead of archive paths; `pack-all`
  cleanup; `clean` command for orphaned work dirs.
- **Infra.**  Backend container memory raised from 1G to 8G.

---

## v1.5.0 (2026-05-26)

WASM as a first-class platform.

- **WASM build matrix.**  26 recipes gain WASM build-matrix entries
  with host-platform sharding: Linux, macOS, and Windows hosts can
  all produce wasm32 artifacts.  `qt6-wasm` is unified into the
  main `qt6` recipe.
- **Self-hosted Linux/wasm runners.**  Both GitHub-hosted and
  self-hosted runners participate in Linux/wasm builds for capacity.
- **New host tooling recipes.**  `cmake`, `ninja`, `meson`, `flex`
  are now first-class recipes so cross-compilation hosts can pin
  reproducible toolchains.
- **Yanked-package handling.**  Yanked packages are hidden from the
  catalog API and are no longer uploaded to GitHub Releases.
- **Multi-backend DB tests.**  MySQL multi-backend tests run with
  `spawn` instead of `fork` to avoid the aiomysql greenlet bug
  (xfailed where the bug is unresolvable upstream); shared HMAC key
  volume between backend and test containers.
- **Docker integration tests.**  Stop shelling out to `docker exec`;
  force-rebuild of the test image in CI.

---

## v1.4.1 (2026-05-25)

Incremental-publish + upload-resilience point release.

- **Incremental per-platform publish.**  Each `package-*` matrix job
  publishes its bundles immediately after packaging instead of
  waiting for the whole matrix.  If one platform fails, the others
  still ship.  A final `publish-to-server` job sweeps up any
  stragglers.
- **Chunked / resumable uploads + stream-to-disk** at `/v1/publish`
  for large artifacts.
- **Production tuning.**  Workers reduced to 1 in prod to fix
  chunked-upload session loss; rate limit raised to 300 RPM; max
  upload raised to 1 GiB.
- **GitHub Releases.**  Strip top-level directory from zip archives
  during packaging; tolerate GitHub rate-limit errors in the release
  job; continue publishing remaining archives on individual failures.
- **CLI.**  Wire config/fallback into `install`, add `--source` flag;
  read `__version__` from package metadata instead of a hardcoded
  string.

---

## v1.4.0 (2026-05-25)

Production-deployment release.  The cvcpkg server moves from a
single-node lab process to a Docker + PostgreSQL deployment with a
public landing page at `pkg.tx.wtf` (later `cvcpkg.org`).

- **Docker + PostgreSQL production stack.**  CI-driven `deploy-prod`
  workflow on `prod` branch push; inline docker deploy; `.env.production`
  fetched from the host into the checkout workspace.
- **Production hardening.**  CORS, rate limiting, upload size limits,
  graceful shutdown, structured logging, DB pool improvements.
- **Landing page.**  Bulma-based landing page with package index,
  search, and release-channel tracking.
- **`cvcpkg publish` command + E2E lifecycle tests.**  First-class
  CLI publish flow with end-to-end test coverage.
- **Source-build fallback.**  When a prebuilt binary is unavailable,
  the client falls back to building from source.
- **Multi-backend DB tests.**  SQLite + PostgreSQL + MySQL fixture
  refactor; convenience scripts.
- **CI gating.**  PR-triggered jobs gated to `transfix` actor via
  `CI_ALLOWED_ACTORS` repo variable.
- **GitHub repo references parameterized** throughout the recipes
  and server so forks can deploy their own catalog.
- **Alembic migrations, metrics, and docs.**  All remaining
  production gaps closed for the v1.x line.

---

## v1.3.0 (2026-05-24)

**Split-distribution release.**  The first release built around
per-component archives rather than monolithic per-platform zips.
Establishes the recipe-driven architecture that the rest of the 1.x
and 2.x line builds on.

- **Per-recipe split packaging.**  25+ independent component bundles
  per platform/config/link combo, published as individual archives
  with their own metadata.  Consumers pull only what they need.
- **Ed25519 package signing and verification** (#38).  Optional
  signature + key fingerprint fields in manifests; signing pipeline
  scaffolding in `cvcpkg signing`.
- **Windows builds fully working.**  vcpkg used for GSL, OpenBLAS,
  and ImageMagick (replacing source/CMake builds); gRPC and its
  ecosystem (protobuf, abseil, re2) built as static libs with MSVC
  CRT alignment (`/MD`); NASM assembler installed; Strawberry/Git
  PATH entries removed so they don't shadow MSVC `link.exe`; OpenSSL
  Perl path fixed; abseil CRT mismatch fixed.
- **Release pipeline fixed** to upload per-component bundles + their
  indexes to the GitHub Release alongside the monolithic zips.
- **cvcpkg 2.0 roadmap.**  `docs/roadmap/cvcpkg-2.0.md` (#36) lays
  out the daemon-centric registry design with trust, identity, and
  multi-platform expansion.
- **BSD VM provisioning tools.**  Self-contained scripts to provision
  FreeBSD, OpenBSD, and NetBSD build VMs on the Incus cluster
  (`star-01`/`star-00`); README with lessons learned.
- **New recipes.**  JPEG, LZMA, WebP, zstd, LERC added so libtiff
  can re-enable its full codec set.
- **macOS Qt6.**  Resolve symlinks in Qt6 build dir; resolve symlinks
  in builder paths so Qt6 macOS builds succeed.

---

## v1.1.0 (2026-05-19)

Feature release adding the pieces needed to move TexMol and other
science applications off in-tree dependency copies and onto the shared
libcvc-deps distribution. The v1.1.0 artifacts are based on the v1.0.2
manifest, with these additions and notable packaging fixes:

- **levmar 2.6 added on all platforms.** Built from the vendored
  upstream 2.6 sources with LAPACK enabled and exported as
  `levmar::levmar`.
- **Windows: pthreads4w added.** The vcpkg `pthreads` port is staged so
  projects that include `<pthread.h>` can continue using CMake's normal
  `find_package(Threads)` / `Threads::Threads` target.
- **macOS: Boost header-only component config stubs.** Homebrew's Boost
  1.90 bottle omits several `boost_<component>-1.90.0` CMake package
  directories for header-only components such as `boost_system`. The
  bundle now synthesizes stubs forwarding those components to
  `Boost::headers`, so downstream `find_package(Boost COMPONENTS
  system ...)` works against the extracted archive.
- **Linux: HDF5 staging hardened.** The stage step now skips recursive
  self-referential symlinks in Ubuntu's HDF5 serial layout while still
  copying the real libraries and package metadata.
- **libyaml 0.2.5 added on all platforms.** Downstream projects can use
  `find_package(yaml CONFIG REQUIRED)` and link the `yaml` target for
  YAML configuration parsing/emitting.
- **Protocol Buffers + gRPC added on all platforms.** Downstream
  transport layers can use `find_package(Protobuf CONFIG REQUIRED)` and
  `find_package(gRPC CONFIG REQUIRED)`, then link
  `protobuf::libprotobuf` and `gRPC::grpc++`. Code-generation tools
  such as `protoc` and `grpc_cpp_plugin` are staged in `bin/`. On the
  Windows `*-static` bundle the Protobuf + gRPC stack (plus its
  transitive support libraries — Abseil, c-ares, OpenSSL, RE2, upb,
  utf8_range, zlib) is shipped as a shared `.dll` + import `.lib`
  fallback, mirroring how CGAL/GMP/MPFR are handled on the same
  bundle; the public CMake target surface is identical to the shared
  bundle (see `docs/known-issues.md`, "Windows static builds: grpc
  hang in vcpkg").
- **Linux: HDF5 / libtiff development layout completed.** The bundle
  now provides conventional versioned HDF5 aliases such as
  `libhdf5.so.<abi>` in addition to Ubuntu's `libhdf5_serial.so.<abi>`
  names, and stages the full public libtiff header surface plus
  relocated `libtiff*.pc` metadata for downstream projects that include
  libtiff directly.

### Full component manifest — v1.1.0

| Component | Linux (Ubuntu 24.04 apt) | macOS (arm64 Homebrew) | Windows (vcpkg / aqtinstall / MSYS2) |
|---|---|---|---|
| Boost | 1.83.0 | 1.90.0 | 1.90.0 (vcpkg) |
| HDF5 | 1.10.10 | 2.1.1 | 2.1.1 (vcpkg) |
| FFTW3 | 3.3.10 | 3.3.11 | 3.3.11 (MSYS2 `mingw-w64-x86_64-fftw`) |
| GSL | 2.7.1 | 2.8 | 2.8 (vcpkg) |
| log4cplus | 2.1.2 (source) | 2.1.2 | 2.1.2 (source) |
| libtiff | 4.5.1 | 4.7.1 | 4.7.1 (vcpkg `tiff`) |
| CGAL | 5.6 | 6.1.1 | 6.1.1 (vcpkg) |
| GMP | 6.3.0 | 6.3.0 | 6.3.0 (vcpkg) |
| MPFR | 4.2.1 | 4.2.2 | 4.2.2 (vcpkg) |
| ImageMagick | 6.9.x Q16 (apt) | 7.1.2-21 Q16HDRI | 7.1.2-21 Q16-HDRI (vcpkg overlay) |
| Qt | 6.4.2 (`qt6-base-dev`) | 6.11.0 (brew `qt@6` bottle) | 6.7.3 (`install-qt-action` `6.7.*`) |
| VTK | 9.5.0 (source) | 9.5.2 (brew bottle) | 9.5.0 (source) |
| NFFT3 | 3.5.3 (apt `libnfft3-dev`) | 3.5.3 (source) | 3.5.3 (source) |
| Eigen3 | 3.4.0 | 5.0.1 (brew bottle) | 3.4.x (vcpkg) |
| BLAS / LAPACK | Ubuntu reference 3.x | 3.12.1 (brew `lapack`) | OpenBLAS 0.3.29 (vcpkg) |
| vcglib | 2025.07 | 2025.07 | 2025.07 |
| libiimod | LabShare-Archive/IMOD `8c592ce4` | same | same |
| **levmar** | **2.6 (vendored source)** | **2.6 (vendored source)** | **2.6 (vendored source)** |
| **libyaml** | **0.2.5 (`libyaml-dev`)** | **0.2.5 (Homebrew `libyaml`)** | **0.2.5 (vcpkg `libyaml`)** |
| **Protobuf** | **3.21.12 (`libprotobuf-dev`)** | **34.1 (Homebrew `protobuf`)** | **6.33.4 (vcpkg `protobuf`)** |
| **gRPC** | **1.51.1 (`libgrpc++-dev`)** | **1.80.0 (Homebrew `grpc`)** | **1.76.0 (vcpkg `grpc`)** |
| **Abseil** | **20220623.1 (`libabsl-dev`)** | **20260107.1 (Homebrew `abseil`)** | **20260107.1 (vcpkg `abseil`)** |
| **c-ares** | **1.27.0 (`libc-ares-dev`)** | **1.34.6 (Homebrew `c-ares`)** | **1.34.6 (vcpkg `c-ares`)** |
| **OpenSSL** | **3.0.13 (`libssl-dev`)** | **3.6.2 (Homebrew `openssl@3`)** | **3.6.2 (vcpkg `openssl`)** |
| **RE2** | **20230301 (`libre2-dev`)** | **2025-11-05 (Homebrew `re2`)** | **2025-11-05 (vcpkg `re2`)** |
| **zlib** | **1.3 (`zlib1g-dev`)** | **macOS SDK / Homebrew dependency** | **1.3.2 (vcpkg `zlib`)** |
| **pthreads4w** | **n/a** | **n/a** | **vcpkg `pthreads`** |

(Components added or changed since v1.0.2 are shown in bold.)

### Pin-source notes for v1.1.0

- levmar 2.6 upstream tarball SHA256:
  `3bf4ef1ea4475ded5315e8d8fc992a725f2e7940a74ca3b0f9029d9e6e94bad7`.
  The source files are vendored under `third-party/levmar/upstream/`
  because the upstream HTTPS endpoint's certificate chain is unreliable
  on GitHub-hosted runners.
- levmar is installed as a static library in every archive flavor. Its
  exported `levmar::levmar` target links `LAPACK::LAPACK` transitively,
  so consumers do not need to remember the LAPACK link dependency.
- On Windows, levmar resolves LAPACK through vcpkg `clapack`'s CONFIG
  package (`lapack` + `f2c`) rather than the system FindLAPACK module.
  This avoids probing vcpkg `openblas.lib` for LAPACK symbols that are
  not present in the MSVC OpenBLAS build.
- Linux's `libyaml-dev` package does not ship an upstream CMake package,
  so libcvc-deps generates a small compatible `yamlConfig.cmake` that
  exposes both `yaml` and `yaml::yaml` targets. macOS and Windows use
  the package metadata provided by Homebrew / vcpkg.
- Protobuf and gRPC are intentionally consumed from each platform's
  package manager. Their CMake package names and target names are the
  upstream ones (`Protobuf`, `gRPC`, `protobuf::libprotobuf`,
  `gRPC::grpc++`), while platform-specific transitive dependencies
  such as Abseil, c-ares, OpenSSL, RE2, utf8-range, and zlib are staged
  alongside them so downstream projects can configure without installing
  those development packages separately.
- Protobuf support libraries such as `upb` and `utf8_range` are staged
  when the platform package manager exposes them as installed libraries
  or CMake/pkg-config metadata. They are treated as implementation
  support for Protobuf/gRPC rather than as a primary downstream API.

---

## v1.0.2 (2026-05-14)

Point release fixing the macOS volrover3 bundle and tightening the
Linux Qt6 layout discovered while exercising the v1.0.1 artifacts in
libcvc's release pipeline. **No upstream component versions changed
from v1.0.1; the package manifest is identical** — see the
[v1.0.0 manifest](#full-component-manifest--v100).

Fixes:

- **VTK Python wrapping disabled (#22).** Homebrew's `vtk` bottle
  links against `libpython3.13.dylib` from the brew Python keg.
  Consumers building libcvc on a runner that doesn't carry that
  exact Python were failing the macOS volrover3 link. The bundled
  VTK is now built with `VTK_WRAP_PYTHON=OFF` on every platform,
  removing the Python runtime dependency entirely. (Python wrapping
  was never used by libcvc or volrover3.)
- **Linux: Qt6 mkspecs at multiarch path (#21).** `Qt6CoreConfig.cmake`
  resolves `QT_HOST_DATA_DIRS` relative to its own location and then
  expects `mkspecs/` underneath. Apt installs the mkspecs at
  `/usr/share/qt6/mkspecs`, so the bundle now mirrors them under
  `lib/x86_64-linux-gnu/qt6/mkspecs/` to match.
- **Linux: Qt6 headers at multiarch include path (#20).** Same kind
  of relative-path issue: `Qt6CoreTargets.cmake` resolves
  `INTERFACE_INCLUDE_DIRECTORIES` to
  `<prefix>/include/x86_64-linux-gnu/qt6/QtCore`. Headers are now
  staged under `include/x86_64-linux-gnu/qt6/` instead of plain
  `include/qt6/`.
- **Linux + macOS: Boost shared libraries at the multiarch path,
  macOS install_name rewrite (#19).** Bundles the Boost SOs at
  `lib/x86_64-linux-gnu/` and rewrites the macOS install_names of
  bundled Boost dylibs to `@rpath/<dylib>` so the relocatable layout
  actually works from the consumer's extract location.
- **Linux: Boost cmake configs at multiarch path (#18).** Mirrors
  `BoostConfig.cmake` + `Boost*-1.83.0.cmake` etc. under
  `lib/x86_64-linux-gnu/cmake/Boost-*` so the `_IMPORT_PREFIX` walk
  in the generated targets file resolves to the correct prefix.
- **Windows: ship Qt6 release + debug variants side-by-side in both
  bundles (#17).** The Windows Debug bundle was previously pruning
  every non-`d`-suffixed Qt file (`Qt6Core.dll`, `Qt6Core.lib`, the
  imageformats / platforms / styles plugins, …). Qt6's CMake exports
  are multi-config and remap `RELEASE` / `MINSIZEREL` to
  `RELWITHDEBINFO` at `find_package` time, so `find_package(Qt6)` in
  consumer projects always tried to resolve the missing non-suffixed
  paths. The prune step is gone; both variants now ship in both
  bundles.
- **macOS CI: batch `dylibbundler` + cache brew bottles + cache NFFT3
  install prefix (#16).** No artifact change, but the macOS shared
  release jobs are now much faster on warm caches (was ~35-45 min
  per job, now ~5-8 min on a hit). See the PR for the breakdown.

---

## v1.0.1 (2026-05-13)

First point release. **Same upstream component manifest as v1.0.0**;
fixes target the Linux artifact layout and add per-config VTK +
log4cplus builds so the Debug Linux bundle no longer mixes release
binaries.

Fixes:

- **Linux: build VTK + log4cplus per `matrix.build_type` (#12).**
  The Linux Release and Debug bundles were both pulling VTK and
  log4cplus from a single cached install prefix, so the Debug
  archive shipped Release-compiled `libvtk*.so` / `liblog4cplus.so`.
  Both libraries are now built separately for each
  `matrix.build_type` and cached under per-build-type keys.
- **Linux: mirror Qt6 SOs to the multiarch path + bundle
  `BoostDetectToolset` (#13).** Stages the Qt6 SOs under
  `lib/x86_64-linux-gnu/` so `Qt6CoreConfig.cmake`'s
  `find_library(... PATHS "${_IMPORT_PREFIX}/lib/x86_64-linux-gnu")`
  resolves, and ships Boost's `BoostDetectToolset.cmake` so
  `find_package(Boost)` works without falling back to a system Boost.

---

## v1.0.0 (2026-05-13)

Initial release. Versions actually shipped in the `v1.0.0` release
artifacts. Where a platform's package manager picked a different
upstream version than the workflow's nominal pin (e.g. Homebrew's
VTK bottle), the shipped version is recorded here verbatim.

### Full component manifest — v1.0.0

| Component | Linux (Ubuntu 24.04 apt) | macOS (arm64 Homebrew) | Windows (vcpkg / aqtinstall / MSYS2) |
|---|---|---|---|
| Boost | 1.83.0 | 1.90.0 | 1.90.0 (vcpkg) |
| HDF5 | 1.10.10 | 2.1.1 | 2.1.1 (vcpkg) |
| FFTW3 | 3.3.10 | 3.3.11 | 3.3.11 (MSYS2 `mingw-w64-x86_64-fftw`) |
| GSL | 2.7.1 | 2.8 | 2.8 (vcpkg) |
| log4cplus | 2.1.2 (source) | 2.1.2 | 2.1.2 (source) |
| libtiff | 4.5.1 | 4.7.1 | 4.7.1 (vcpkg `tiff`) |
| CGAL | 5.6 | 6.1.1 | 6.1.1 (vcpkg) |
| GMP | 6.3.0 | 6.3.0 | 6.3.0 (vcpkg) |
| MPFR | 4.2.1 | 4.2.2 | 4.2.2 (vcpkg) |
| ImageMagick | 6.9.x Q16 (apt) | 7.1.2-21 Q16HDRI | 7.1.2-21 Q16-HDRI (vcpkg overlay) |
| Qt | 6.4.2 (`qt6-base-dev`) | 6.11.0 (brew `qt@6` bottle) | 6.7.3 (`install-qt-action` `6.7.*`) |
| VTK | 9.5.0 (source) | 9.5.2 (brew bottle) | 9.5.0 (source) |
| NFFT3 | 3.5.3 (apt `libnfft3-dev`) | 3.5.3 (source) | 3.5.3 (source) |
| Eigen3 | 3.4.0 | 5.0.1 (brew bottle) | 3.4.x (vcpkg) |
| BLAS / LAPACK | Ubuntu reference 3.x | 3.12.1 (brew `lapack`) | OpenBLAS 0.3.29 (vcpkg) |
| vcglib | 2025.07 | 2025.07 | 2025.07 |
| libiimod | LabShare-Archive/IMOD `8c592ce4` | same | same |

### Pin-source notes for v1.0.0

- `VTK_VERSION: 9.5.0`, `QT_VERSION_WINDOWS: 6.7.*`,
  `VCGLIB_VERSION: 2025.07`, `LOG4CPLUS_VERSION: 2.1.2`,
  `NFFT_VERSION: 3.5.3` are set in `.github/workflows/release.yml`.
- vcglib SHA256:
  `e49fc9342d5476b3e39a5e1939b965b57c91d7a17b4f97b8c5eaf01228b16cf0`.
- NFFT3 source tarball SHA256:
  `caf1b3b3e5bf8c33a6bfd7eca811d954efce896605ecfd0144d47d0bebdf4371`.
- libiimod is built from
  [LabShare-Archive/IMOD](https://github.com/LabShare-Archive/IMOD)
  commit `8c592ce4cfae5e0748314da56d73334de7465776` (archived,
  read-only since 2018-07-15).
- macOS Homebrew's `vtk` bottle currently ships 9.5.2 even though the
  workflow's nominal VTK pin is 9.5.0; the Linux and Windows builds use
  the pinned 9.5.0 source.
- macOS Homebrew's `eigen` bottle is on the 5.x release line; the
  Linux/Windows builds remain on the 3.4 series. Consumer code should
  not rely on Eigen ABI parity across platforms in v1.0.0.
- Linux Ubuntu 24.04 ships ImageMagick 6 (Q16) via apt while macOS and
  Windows ship ImageMagick 7. Code that includes `<Magick++.h>` builds
  on both, but ABI differs.
