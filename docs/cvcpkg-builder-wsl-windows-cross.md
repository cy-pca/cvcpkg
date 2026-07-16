# Windows Cross Builds from a WSL Builder (winhost)

A cvcpkg builder running inside a WSL2 distro can build **native
`windows/x86_64` packages** by delegating the build to its own Windows
host: the recipe's normal Windows build script (`build.ps1`) runs on the
host under the host's MSVC toolchain, while source fetching, packaging,
and publishing stay on the Linux side.

This is the same split the wasm cross-builds use — register a cross
target, let the scheduler dispatch foreign-platform jobs, and use a
"toolchain" to produce the artifacts — except here the toolchain is the
physical Windows machine the distro lives on, reached through
[WSL interop](https://learn.microsoft.com/en-us/windows/wsl/filesystems#interoperability-between-windows-and-linux-commands)
(`powershell.exe` is directly invocable from inside the distro).

```
        WSL distro (linux builder)                Windows host
  ┌──────────────────────────────────┐    ┌──────────────────────────────┐
  │ cvcpkg builder run               │    │                              │
  │   --cross-platform windows       │    │  winhost-run-job.ps1         │
  │                                  │    │    └─ pwsh 7                 │
  │ 1. claim windows/x86_64 job      │    │        └─ recipes build.ps1  │
  │ 2. fetch source + deps (linux)   │    │            └─ env-windows:   │
  │ 3. stage job ─────────────────────────▶  %USERPROFILE%\cvcpkg-winhost│
  │    (powershell.exe via interop)  │    │      \jobs\<job>\{source,    │
  │ 4. host builds with MSVC ◀───────┤    │       build,install,deps}    │
  │ 5. sync install/ back ◀───────────────┤                              │
  │ 6. pack + publish (linux)        │    │                              │
  └──────────────────────────────────┘    └──────────────────────────────┘
```

Implementation: `src/cvcpkg/winhost.py` (Linux-side orchestration) +
`recipes/_common/winhost-run-job.ps1` (host-side runner — ships with
every recipe bundle, so builders pick up updates with recipe pushes,
exactly like `env-windows.ps1`).

Recipes need **no changes**: the delegation reuses each recipe's
existing `platform: windows` matrix entry and script.

## How a job executes

1. The builder (a normal `linux/x86_64` agent) registers with
   `--cross-platform windows`.  The scheduler then dispatches
   `windows/x86_64` jobs to it like any other cross target.
2. The builder fetches the recipe bundle and the job's **Windows**
   dependency packages (e.g. `zlib` `windows/x86_64`) into a Linux-side
   deps prefix, as usual.
3. `run_build` detects target=windows on a Linux WSL host and hands off
   to the winhost module, which stages the job and invokes
   `winhost-run-job.ps1` on the host through interop.
4. The runner applies the job environment (`CVC_SOURCE_DIR`,
   `CVC_INSTALL_DIR`, `CVC_DEPS_PREFIX`, … in host-visible path form),
   picks PowerShell 7, and runs the recipe's `build.ps1`.
   `env-windows.ps1` does its normal MSVC auto-import
   (`Import-CvcMsvcEnv`), so the host only needs VS Build Tools
   installed — no pre-set developer environment.
5. The install tree is synced back to the Linux side, where the
   standard `pack` + publish flow produces the same
   `<name>-<ver>-windows-x86_64-<config>-<link>.zip` a native Windows
   builder would.

Build output streams back through the interop pipe, so `cvcpkg builds
log -f` shows the host build live.

## File exchange modes

| Mode | What happens | When to use |
|------|--------------|-------------|
| `exchange` (default) | Job staged into a directory in the Windows **user profile** (`%USERPROFILE%\cvcpkg-winhost\jobs\<job>`); host builds on native NTFS; `install/` synced back. | Always safe; also the fastest for compile-heavy builds (host I/O stays native). |
| `direct` | Host reads the WSL work dir directly via `\\wsl.localhost\<distro>\...` UNC paths — no copying. | Opt-in only. `cmd.exe` cannot use UNC working directories, and CMake's MSVC link step (`vs_link_exe`) runs through `cmd.exe`, so **CMake/MSVC recipes fail in direct mode**. Usable only for build systems that tolerate UNC cwds. |
| `auto` | Resolves to `exchange`. | Default. |

Dependency metadata (`.pc` / `.cmake` files) is rewritten while staging
so `prefix=` lines and absolute references point at the host-visible
location of the deps prefix.

## Configuration

All knobs are environment variables on the **builder** (set them in the
service environment):

| Variable | Default | Meaning |
|----------|---------|---------|
| `CVCPKG_WINHOST` | enabled | Set `0`/`false` to disable delegation entirely (windows jobs then fail on this builder). |
| `CVCPKG_WINHOST_MODE` | `auto` | `auto` \| `exchange` \| `direct`. |
| `CVCPKG_WINHOST_EXCHANGE` | `%USERPROFILE%\cvcpkg-winhost` | Windows path of the exchange root. |
| `CVCPKG_WINHOST_POWERSHELL` | auto-detected | WSL path of the host `powershell.exe` (rarely needed). |
| `CVCPKG_WINHOST_JOBS` | host CPU count | Overrides `CVC_JOBS` for host builds. |

## Host prerequisites

The Windows host needs the same toolchain a native Windows builder
uses (VS 2022 Build Tools with the C++ workload, CMake, Ninja, NASM,
Strawberry Perl, …) **plus PowerShell 7**:

- **PowerShell 7 is required.** Windows PowerShell 5.1 reads BOM-less
  UTF-8 scripts as ANSI; the recipe scripts contain UTF-8 punctuation,
  which 5.1 turns into parse errors (smart-quote mojibake terminates
  string literals).  Install with `choco install powershell-core`.  The
  Microsoft Store `pwsh` alias does **not** count — it refuses to start
  from non-interactive sessions, so the runner ignores it.
- Exclude the exchange directory from Windows Defender or long builds
  will crawl: `Add-MpPreference -ExclusionPath $env:USERPROFILE\cvcpkg-winhost`.

## WSL distro setup

Follow the generic WSL builder guide
([cvcpkg-builder-wsl-debian.md](cvcpkg-builder-wsl-debian.md)) for the
distro basics — systemd, toolchain bootstrap, sshd, disk hygiene.  The
cross build adds three requirements:

**1. Interop + automount in `/etc/wsl.conf`** (both are defaults, but
the cross build breaks without them — pin them):

```ini
[boot]
systemd=true

[automount]
enabled=true
options="metadata,uid=1000,gid=1000,umask=022"   # builder user must own /mnt/c

[interop]
enabled=true
appendWindowsPath=true
```

**2. Register with the cross target.**  The builder unit is the normal
Linux one plus `--cross-platform windows` (arch defaults to the host
arch, i.e. `windows/x86_64`):

```ini
ExecStart=/home/tfx/.local/bin/cvcpkg builder run \
    --server https://cvcpkg.org \
    --token cvctok_<builder-token> \
    --name <name> \
    --max-jobs 2 \
    --work-dir /tmp/cvcpkg-builder \
    --cross-platform windows
```

(Add `--cross-platform wasm` too if the builder should also take wasm
jobs — the flags combine.)

**3. Keep the distro and interop alive.**  WSL terminates a distro
shortly after its last *session* closes — systemd services do not count
— and every interop socket dies with it.  Run a trivial **anchor**
process from a Windows boot scheduled task:

```powershell
# The anchor is just:  sh -c 'while :; do sleep 3600; done'
schtasks /create /tn wsl-cvcpkg-cross-boot /sc onstart /rl highest ^
  /tr "wsl.exe -d <distro> -u tfx -e /usr/local/bin/cvcpkg-winhost-anchor"
```

Because the anchor is launched by `wsl.exe`, it owns a live interop
socket under `/run/WSL/` for the lifetime of the host.  The winhost
module resolves interop per call: inherited `WSL_INTEROP` →
`/run/WSL/1_interop` (systemd init socket) → newest live session
socket, probing each with a no-op `powershell.exe` call — so the
builder works from a systemd unit even though systemd services don't
inherit `WSL_INTEROP`.

## Trying it without a server

The delegation also drives local builds, which is how to iterate on a
new host:

```bash
cvcpkg build zlib --platform windows --host-platform linux \
    --local --no-deps --recipes-dir recipes
cvcpkg pack zlib --platform windows --host-platform linux \
    --local --recipes-dir recipes --output-dir /tmp/dist
```

`--host-platform linux` marks the build as a cross build (the remote
builder path sets this automatically when a job's platform differs from
the builder's).

## Tests

Recipe `test.sh` scripts run on the **Linux side** against the
synced-back install tree, with `CVC_WINHOST=1` exported.  File-existence
checks validate the real artifacts; compile-and-run checks generally
degrade to their non-fatal WARN branches (a Linux `cc` cannot link
Windows import libraries).  Test scripts that want to do more under
delegation can branch on `CVC_WINHOST`.

## Host tools stay out of the deliverable

Cross-building often pulls **host tools** — cmake, ninja, bazel/bazelisk, or a
whole cross toolchain — that run on the builder to produce target artifacts.
These are a build-time byproduct and must not end up in the deliverable you
ship to a consumer (a C# project ingesting `bin/`, say).  cvcpkg keeps them
separate:

- **Separate prefix.**  `cvcpkg build --prefix P …` installs host tools into a
  sibling **host-tools prefix**, `P.host-tools`, by default.  The deliverable
  `P` contains only target artifacts.  Override the location with
  `--host-tools-prefix DIR`, or pass the same path as `--prefix` to disable the
  separation (legacy behaviour).  This is what keeps `bazel`/`bazelisk` out of
  `P/bin`.

  A host tool is any recipe that declares a `cross_toolchain` block; its bundle
  manifest is flagged `bundle.host_tool: true`.

- **Recorded in the deliverable.**  When the separation is active, cvcpkg writes
  `P/share/libcvc-deps/host-tools.yaml` recording that host tools are present,
  where (`prefix`), which ones (`tools`), and whether they have been stripped:

  ```yaml
  schema_version: 1
  host_tools:
    present: true
    prefix: /abs/path/to/P.host-tools
    tools: [bazel, bazelisk]
    stripped: false
    stripped_at: ''
  ```

- **Stripped by default.**  Because the host-tools prefix is only needed during
  the build, it is **removed once the build/install completes**.  `cvcpkg build`
  strips it at the end of the build; `cvcpkg install` reads the record on
  finalize and strips it there.  Pass **`--keep-host-tools`** to either command
  to retain the toolchain (e.g. to reuse it for a subsequent build); the record
  then keeps `stripped: false`.  The strip never touches the deliverable prefix
  itself, and is a no-op when no record is present (a plain prebuilt install).

## Gotchas

- **Don't force direct mode for CMake recipes.**  `cl.exe` itself
  handles UNC paths, but CMake's link step runs through `cmd.exe`
  (`vs_link_exe`), which refuses UNC working directories.  This is why
  `auto` = `exchange`.
- **Windows PowerShell 5.1 as a last resort.**  If no pwsh 7 exists the
  runner falls back to 5.1 with a loud warning — expect UTF-8 parse
  errors on current recipes.  Install pwsh 7.
- **Exchange disk usage.**  Each job stages source + deps + install
  under the profile and is deleted after the job (kept with
  `--keep-build-dir`).  A crashed builder can leave orphans — clear
  `%USERPROFILE%\cvcpkg-winhost\jobs\*` freely; nothing persistent
  lives there.
- **No elevation.**  Interop-launched processes run as the logged-in
  Windows user, unelevated.  Recipe builds don't need elevation; host
  *provisioning* (installing pwsh, Defender exclusions) does.
- **`/tmp` work dir.**  Keep `--work-dir` on ext4 (`/tmp/cvcpkg-builder`)
  as usual.  The exchange copy is the only part that touches `/mnt/c`,
  by design.

## Troubleshooting

| Symptom | Likely cause / fix |
|---------|--------------------|
| `WSL interop is not reachable` | No live interop socket — the anchor isn't running (start the boot task), or `[interop] enabled=false`.  The error lists what was probed. |
| Parse errors mentioning `Unexpected token` in `env-windows.ps1` | Build ran under PowerShell 5.1 — install PowerShell 7 on the host. |
| `cannot cd to UNC path` / link step fails in `CMakeScratch` | Direct mode with a CMake recipe — use exchange mode. |
| `host build produced no install directory` / `installed no files` | The build script installed elsewhere or silently failed — rerun with `--keep-build-dir` and inspect `%USERPROFILE%\cvcpkg-winhost\jobs\<job>`. |
| Writes to `/mnt/c` fail as the builder user | `[automount]` uid/gid don't match the builder user — fix `/etc/wsl.conf`, then `wsl --terminate` and retry. |
| Builder dies when the SSH session closes | The distro idle-terminated — the anchor isn't running. |
