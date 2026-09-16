# recipes/matplotlib-cp311/build.ps1 — Windows counterpart of build.sh: install
# the pinned prebuilt matplotlib win_amd64 wheel (source.type python_wheel).
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\..\_common\python-wheel.ps1"
Invoke-CvcPipInstallWheel
Invoke-CvcPythonCheck @'
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
fig = plt.figure()
fig.add_subplot(111).plot([0, 1], [0, 1])
fig.canvas.draw()
print('matplotlib', matplotlib.__version__, 'Agg render OK')
'@
