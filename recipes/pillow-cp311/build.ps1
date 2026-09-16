# recipes/pillow-cp311/build.ps1 — Windows counterpart of build.sh: install the
# pinned prebuilt Pillow win_amd64 wheel (source.type python_wheel). The check
# asserts the PNG + JPEG codecs are present.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\..\_common\python-wheel.ps1"
Invoke-CvcPipInstallWheel
Invoke-CvcPythonCheck @'
from PIL import Image, features
assert features.check('zlib'), 'Pillow: no zlib/PNG codec'
assert features.check('jpg'), 'Pillow: no JPEG codec'
print('Pillow codecs OK (zlib+jpg)')
'@
