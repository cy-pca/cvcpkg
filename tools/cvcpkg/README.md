# cvcpkg

Component package manager for **libcvc-deps** prebuilt dependency bundles.

`cvcpkg` resolves a set of component requirements against the libcvc-deps
bundle catalog, downloads the matching archives, verifies their integrity,
and materializes a single `CMAKE_PREFIX_PATH`-compatible install prefix.

**Downstream projects should adopt `cvcpkg` instead of manually downloading
and extracting the monolithic libcvc-deps archive.** Per-component bundles
are smaller, cacheable, and version-locked — you only pull what you need.

## Quick start

```bash
# Install from PyPI (once published):
pipx install cvcpkg

# Or install from source:
cd tools/cvcpkg && pip install -e '.[progress]'

# List available components:
cvcpkg list --available

# Install specific components into a prefix:
cvcpkg install --prefix ./deps boost hdf5 fftw3

# Install from a requirements file:
cvcpkg install --from cvc-requirements.yaml --prefix ./deps

# Verify an existing prefix:
cvcpkg verify --prefix ./deps
```

## Integrating your downstream project

### Step 1: Create `cvc-requirements.yaml`

Place this file in your project root (e.g. alongside `CMakeLists.txt`):

```yaml
# cvc-requirements.yaml — declare which libcvc-deps components you need.
#
# cvcpkg resolves these against the published catalog and installs
# exactly the matching per-component bundles for your platform.

platform: auto          # auto-detect, or: linux | macos | windows
arch: auto              # auto-detect, or: x86_64 | arm64
config: release         # release | debug
link: shared            # shared | static

# Pin the libcvc-deps release to consume bundles from:
libcvc-deps: ">=1.2.0"

# Components your project needs — only these are downloaded:
components:
  - boost
  - hdf5
  - fftw3
  - tiff
  - vtk
  - qt6
```

### Step 2: Install dependencies

```bash
# Resolve, download, verify, and install into ./deps:
cvcpkg install --from cvc-requirements.yaml --prefix ./deps

# Or specify overrides on the command line:
cvcpkg install --from cvc-requirements.yaml --prefix ./deps \
  --config debug --link static
```

### Step 3: Point CMake at the prefix

```bash
cmake -S . -B build -DCMAKE_PREFIX_PATH="$(pwd)/deps"
```

All `find_package()` calls (Boost, HDF5, FFTW3, VTK, Qt6, etc.) will
resolve from the cvcpkg-managed prefix.

### Step 4: Lock for CI reproducibility

After a successful install, cvcpkg writes a lockfile:

```bash
# Commit this for reproducible CI builds:
git add cvcpkg.lock.yaml
```

Re-running `cvcpkg install` with a lockfile present replays the exact
same downloads (same SHA-256 digests), regardless of catalog updates.

### Example: CMake preset integration

```json
{
  "configurePresets": [{
    "name": "default",
    "cacheVariables": {
      "CMAKE_PREFIX_PATH": "${sourceDir}/deps"
    }
  }]
}
```

### Example: CI workflow

```yaml
- name: Install libcvc-deps
  run: |
    pip install cvcpkg
    cvcpkg install --from cvc-requirements.yaml --prefix ./deps

- name: Configure
  run: cmake -S . -B build -DCMAKE_PREFIX_PATH=${{ github.workspace }}/deps
```

## Publishing a downstream package

If you maintain a library that other CVC projects depend on, you can
publish it to the cvcpkg server so consumers can pull it with
`cvcpkg install`.

### Step 1: Write a recipe

Create `recipes/<your-package>/recipe.yaml`:

```yaml
name: my-library
version: "2.1.0-cvc1"
upstream_version: "2.1.0"
cvc_revision: 1
description: "My library for CVC downstream consumers"

source:
  url: "https://github.com/org/my-library/archive/refs/tags/v2.1.0.tar.gz"
  sha256: "<sha256-of-tarball>"

build_matrix:
  - platform: linux
    arch: x86_64
  - platform: macos
    arch: arm64
  - platform: windows
    arch: x86_64

build:
  system: cmake
  args:
    - "-DCMAKE_BUILD_TYPE={{config}}"
    - "-DBUILD_SHARED_LIBS={{shared}}"
    - "-DCMAKE_INSTALL_PREFIX={{prefix}}"

package:
  files:
    - "lib/**"
    - "include/**"
    - "share/**/cmake/**"
    - "bin/**"

dependencies:
  - name: boost
    version: ">=1.83"
  - name: hdf5
    version: ">=1.10"

cmake_packages:
  - name: MyLibrary
    targets: ["MyLibrary::MyLibrary"]
```

### Step 2: Build the package

```bash
# Build using the recipe (fetches source, runs cmake, stages output):
cvcpkg build recipes/my-library --prefix ./stage \
  --config release --link shared

# Or if you already have a built install tree:
cvcpkg push ./stage --recipe recipes/my-library \
  --platform linux --arch x86_64 --config release --link shared
```

### Step 3: Publish to the server

```bash
# Get a publisher token from your server admin:
export CVCPKG_TOKEN="cvctok_..."

# Push to the cvcpkg server:
cvcpkg push ./stage --recipe recipes/my-library \
  --platform linux --arch x86_64 --config release --link shared \
  --server https://cvcpkg.example.org
```

### Server administration

```bash
# Install with server extras:
pip install cvcpkg[server]

# Start the server:
cvcpkg-server run --state-dir /var/lib/cvcpkg --host 0.0.0.0 --port 8080

# Create tokens:
cvcpkg-server token create --name ci-publisher --role publisher
cvcpkg-server token create --name dev-reader --role reader

# View audit log:
cvcpkg-server audit log --last 20
cvcpkg-server audit verify
```

## Development

```bash
cd tools/cvcpkg
pip install -e '.[progress,server]'
pytest
```

### Running with coverage

```bash
pytest --cov=cvcpkg --cov-branch --cov-report=html:htmlcov tests/
open htmlcov/index.html
```

Coverage reports are also generated as CI artifacts on every push/PR —
download them from the workflow run's Artifacts section.

## License

MIT — see [LICENSE](../../LICENSE).
