# Rust crates as cvcpkg packages

A Rust recipe builds offline from crates that are cvcpkg packages, one package per
crate **at its exact version**. Nothing in its build reaches crates.io or GitHub:
each crate is downloaded once, by whichever builder publishes its noarch package,
and every recipe that pins the same version shares that package.

## The pieces

| Recipe | What it is | Ships |
|---|---|---|
| `crate-<name>--<version>` | One crate from crates.io, generated | `share/cargo/crates/<name>-<version>/` |
| `crate-git-<name>--<commit8>` | A git dependency, written by hand | `share/cargo/crates/<name>-<version>-git-<commit8>/` |
| `<consumer>-crates` | A crate set: every crate one Cargo.lock pins, generated | `share/cargo/sets/<set>/{Cargo.lock,cargo-config.toml.in}` |

- **Crate packages.** The source is the `.crate` file from
  `https://static.crates.io/crates/<name>/<name>-<version>.crate`. Its sha256 is
  the `checksum` Cargo.lock records, which is the sha256 of that file, so cvcpkg
  checks the download against the same hash cargo would. The build script copies
  the crate into `share/cargo/crates/<name>-<version>/` and writes the
  `.cargo-checksum.json` a cargo directory source needs:
  `{"files":{},"package":"<checksum>"}`. cargo compares `package` with
  Cargo.lock; there are no per-file hashes, because the bundle's own sha256 covers
  the files.
- **Names.** `crate-` + the crate name + `--` + the version, lowercased, with `_`
  and `.` written as `-`, and `+build` metadata dropped (crates.io treats `-` and
  `_` as the same name, and cargo ignores build metadata, so neither folding can
  merge two crates). The `--` keeps a name that ends in digits apart from the
  version: `crate-windows-sys--0-48-0`. `upstream_version` is the version without
  build metadata, since cvcpkg uses `+cvc.N`.
- **Git dependencies** are not on crates.io. Builders cannot fetch `type: git`
  sources, so a hand-written recipe takes the GitHub archive of the pinned commit
  (and fetches any submodule's archive in its build script, pinned by sha256). Its
  `.cargo-checksum.json` has `"package": null`, as `cargo vendor` writes for git
  sources. Example: `recipes/crate-git-xcb-imdkit--215ce4b0`.
- **Crate sets.** `<consumer>-crates` has every crate of the consumer's Cargo.lock
  in `depends.runtime`, and ships the lockfile plus `cargo-config.toml.in`:

  ```toml
  [source.crates-io]
  replace-with = "cvcpkg-crates"

  [source."git+https://github.com/wez/xcb-imdkit-rs.git?rev=215ce4b..."]  # one per git source
  git = "https://github.com/wez/xcb-imdkit-rs.git"
  rev = "215ce4b..."
  replace-with = "cvcpkg-crates"

  [source.cvcpkg-crates]
  directory = "@CRATES_DIR@"
  ```

## A consumer recipe

1. `depends.build` lists the set (`- name: wezterm-crates`) and `rust`. The set's
   runtime dependencies, the crates, are installed into the build prefix with it.
2. The build script finds `share/cargo/sets/<set>/cargo-config.toml.in` under
   `CVC_BUILD_PREFIX` (or `CVC_DEPS_PREFIX`), replaces `@CRATES_DIR@` with
   `<that prefix>/share/cargo/crates` (forward slashes on Windows), and writes it
   to `$CARGO_HOME/config.toml`, with `CARGO_HOME` in the build directory.
3. It runs `cargo build --release --locked --offline`. A crate missing from the
   directory is an error, never a download.

`recipes/wezterm` is the first consumer (`build.sh`, `build.ps1`).

## Generating crate packages

```sh
python tools/make_crate_packages.py --lockfile path/to/Cargo.lock \
    --set <consumer>-crates --set-version <consumer version> --consumer <consumer> \
    [--crate-sources path/to/vendor]
```

- Use the Cargo.lock the build will use, **after** the consumer's patches: a patch
  that changes the lockfile changes the crate set.
- A crate that already has a recipe is reused. If that recipe's sha256 disagrees
  with the lockfile, the tool stops.
- A git dependency without a hand-written `crate-git-*` recipe stops the tool, which
  names the recipe to write.
- License and description come from each crate's Cargo.toml. With
  `--crate-sources` (for example a `cargo vendor --versioned-dirs` output) the tool
  reads them locally; otherwise it asks the crates.io API, one request a second.

## Not yet converted

cryptography, bcrypt, pydantic-core, nh3, orjson, maturin and slint still let cargo
fetch crates.io at build time. Each becomes a consumer the same way: generate its
set from its Cargo.lock (most crates it shares with others are then reused), then
point its build at the set.
