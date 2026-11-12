<#
.SYNOPSIS
  Scan files with Microsoft Defender's current definitions and fail if any is detected.

.DESCRIPTION
  Microsoft Defender quarantined the cvcpkg 2.5.0, 2.5.12 and 2.5.13 Windows binaries as
  Trojan:Win32/Bearfoos.A!ml, a machine-learned static signature ("Concrete" detection) that came
  with definitions 1.463.178, .204 and .229 and fired when the exe was started. 2.5.13 had shipped
  clean and was flagged two days later. Later definitions dropped the detection again.

  Two workflows run this script:
    - cvcpkg-standalone.yml, on the Windows binary it just built, so a flagged build never reaches
      a release;
    - defender-watch.yml, daily, on the latest released binary, so a definitions update that
      flags a shipped release is noticed the same day.

  Before scanning it updates the definitions and proves the scanner works by scanning the EICAR
  test file. A scan that cannot detect EICAR proves nothing, so that is an error.

  -CI is for GitHub-hosted runners. Their image turns off real-time protection and excludes whole
  drives from scanning, and a custom scan of an excluded path reports "no threats" for anything.
  With -CI the script removes those exclusions first. Do not pass -CI on your own machine.

.EXAMPLE
  ./scripts/defender-scan.ps1 -CI -Path dist/cvcpkg.exe
#>
param(
    [Parameter(Mandatory = $true)][string[]]$Path,
    [switch]$CI
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$mp = Join-Path $env:ProgramFiles 'Windows Defender\MpCmdRun.exe'
if (-not (Test-Path $mp)) { throw "defender-scan: $mp not found; Microsoft Defender is not installed" }

if ($CI) {
    $pref = Get-MpPreference
    foreach ($kind in 'ExclusionPath', 'ExclusionExtension', 'ExclusionProcess') {
        $values = @($pref.$kind | Where-Object { $_ })
        if ($values.Count -gt 0) {
            Write-Host "defender-scan: removing $kind exclusions: $($values -join ', ')"
            $arg = @{ $kind = $values }
            Remove-MpPreference @arg
        }
    }
}

Write-Host 'defender-scan: updating definitions'
& $mp -SignatureUpdate | Out-Host
if ($LASTEXITCODE -ne 0) {
    # Scanning with older definitions is still worth doing; say so loudly.
    Write-Host "::warning::Defender definitions update failed (exit $LASTEXITCODE); scanning with the installed ones"
}
$status = Get-MpComputerStatus
Write-Host ("defender-scan: definitions {0} (age {1} d), engine {2}" -f
    $status.AntivirusSignatureVersion, $status.AntivirusSignatureAge, $status.AMEngineVersion)

function Invoke-DefenderScan([string]$File) {
    $out = & $mp -Scan -ScanType 3 -File $File -DisableRemediation 2>&1 | Out-String
    $clean = $out -match 'found no threats'
    return [pscustomobject]@{ Clean = $clean; Exit = $LASTEXITCODE; Output = $out.Trim() }
}

# Self-test with the EICAR test file. The string is split so this script does not itself match.
$selfDir = Join-Path ([IO.Path]::GetTempPath()) ("defender-selftest-" + [guid]::NewGuid())
New-Item -ItemType Directory -Path $selfDir | Out-Null
$eicar = Join-Path $selfDir 'eicar.com'
[IO.File]::WriteAllText($eicar, 'X5O!P%@AP[4\PZX54(P^)7CC)7}$' + 'EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*')
Start-Sleep -Seconds 2
if (-not (Test-Path $eicar)) {
    Write-Host 'defender-scan: self-test passed (real-time protection removed the EICAR file on write)'
} else {
    $self = Invoke-DefenderScan $eicar
    Remove-Item -Recurse -Force $selfDir -ErrorAction SilentlyContinue
    if ($self.Clean) {
        throw "defender-scan: the scanner did not detect the EICAR test file, so its verdicts mean nothing:`n$($self.Output)"
    }
    Write-Host 'defender-scan: self-test passed (EICAR detected)'
}

$failed = @()
foreach ($p in $Path) {
    $full = (Resolve-Path $p).Path
    $r = Invoke-DefenderScan $full
    if ($r.Clean) {
        Write-Host "defender-scan: clean: $full"
    } else {
        Write-Host "::error file=$p::Microsoft Defender flags $p"
        Write-Host $r.Output
        $failed += $p
    }
}
if ($failed.Count -gt 0) {
    $detections = Get-MpThreatDetection -ErrorAction SilentlyContinue | Sort-Object InitialDetectionTime |
        Select-Object -Last 5 | ForEach-Object { "$($_.ThreatID) $($_.Resources -join ';')" }
    if ($detections) { Write-Host "recent detections:`n$($detections -join "`n")" }
    throw "defender-scan: flagged: $($failed -join ', ')"
}
Write-Host "defender-scan: all $($Path.Count) file(s) clean"
