# cvcpkg

Component package manager for **libcvc-deps** prebuilt dependency bundles.

`cvcpkg` resolves a set of component requirements against the libcvc-deps
bundle catalog, downloads the matching archives, verifies their integrity,
and materializes a single `CMAKE_PREFIX_PATH`-compatible install prefix.

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

## Requirements file

Create a `cvc-requirements.yaml` in your project:

```yaml
platform: auto
config: release
link: shared

libcvc-deps: "1.1.0"

components:
  - boost
  - hdf5
  - fftw3
  - tiff
```

Then: `cvcpkg install --from cvc-requirements.yaml --prefix ./deps`

## Development

```bash
cd tools/cvcpkg
pip install -e '.[progress]'
pytest
```

## License

MIT — see [LICENSE](../../LICENSE).
