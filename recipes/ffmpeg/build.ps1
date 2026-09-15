# recipes/ffmpeg/build.ps1 — build FFmpeg on Windows via MSYS2/MinGW64.
#
# Produces native Windows DLLs (avcodec-*.dll etc.) using the MinGW-w64
# GCC toolchain inside MSYS2, PLUS the ffmpeg/ffprobe executables built
# against that same full codec set: Opus, MP3, Vorbis, VP8/VP9, AV1
# (dav1d), WebP, JPEG, PNG, OpenSSL, freetype, fribidi, bzip2, lzma,
# x264, x265.
#
# Nothing is passed to restrict components, so every muxer, demuxer,
# filter and protocol FFmpeg can build is present.
#
# There used to be a sibling `ffmpeg-cli` recipe making the opposite trade
# (--disable-everything for a three-package closure). It was removed once this
# recipe started shipping programs: the two then owned the same bin/ffmpeg.exe
# and had to be declared conflicting, and keeping a second FFmpeg build in sync
# earned less than it cost. Consumers that only need the H.264 binary now take
# this package's superset.
#
# All codec/library dependencies must be pre-built and available in
# CVC_DEPS_PREFIX (declared as depends.build in recipe.yaml).
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-windows.ps1"

$bash       = Get-CvcGitBash
$msysSource = ConvertTo-CvcMsysPath $env:CVC_SOURCE_DIR
# Drive-letter form, NOT the MSYS /c/... form: these become -I/-L flags for a
# NATIVE Windows gcc, which cannot resolve /c/... and — because gcc silently
# ignores search paths that do not exist — fails by finding nothing rather than
# by erroring. The now-removed ffmpeg-cli recipe passed the MSYS form and got
# away with it only because pkg-config (fed native paths) supplied the real
# flags; the zlib import library, reached through -L alone, had no such cover.
$winDeps    = if ($env:CVC_DEPS_PREFIX) { ($env:CVC_DEPS_PREFIX -replace '\\', '/') } else { '' }
$jobs       = if ($env:CVC_JOBS) { [int]$env:CVC_JOBS } else { 4 }
if ($jobs -le 0) { $jobs = 4 }

# Drive-letter form with forward slashes, NOT the MSYS /c/... form. Two
# reasons, both learned the hard way elsewhere in this tree:
#   * `make install` runs ranlib from the toolchain, a NATIVE Windows binary
#     that cannot resolve /c/... (see recipes/x264) — breaks static only.
#   * configure bakes --prefix verbatim into the installed .pc files. Handed
#     the MSYS form, Invoke-CvcRewriteInstallPaths used to match nothing and
#     silently no-op, shipping .pc files that point at the (deleted) build
#     sandbox — which is exactly what the old ffmpeg-cli recipe did to the
#     dbg-deps prefix. The helper recognises all three forms now, but this is
#     still the form to pass.
$winPrefix = ($env:CVC_INSTALL_DIR -replace '\\', '/')

New-Item -ItemType Directory -Force -Path $env:CVC_BUILD_DIR | Out-Null

$env:MSYSTEM          = 'MINGW64'
$env:MSYS_NO_PATHCONV = '1'
$env:CHERE_INVOKING   = '1'

# Clear MSVC env so MinGW-w64 gcc is selected by FFmpeg's configure.
'CC','CXX','LD','AR','NM','RANLIB','CFLAGS','CXXFLAGS','LDFLAGS' |
    ForEach-Object { Remove-Item "Env:$_" -ErrorAction SilentlyContinue }

# Host tools (the compiler, nasm) are staged into CVC_BUILD_PREFIX, NOT the
# runtime prefix — putting a toolchain in the runtime prefix shadows MSVC for
# every later CMake build against it. Search the build prefix first so our
# pinned gcc wins over any ambient C:\msys64\mingw64\bin.
$toolRoots = @($env:CVC_BUILD_PREFIX, $env:CVC_DEPS_PREFIX) | Where-Object { $_ }

# PATH is consumed by the SHELL, so it wants MSYS form and ':' separators.
$msysToolBins = ($toolRoots | ForEach-Object { (ConvertTo-CvcMsysPath $_) + '/bin' }) -join ':'

# PKG_CONFIG_PATH is consumed by pkg-config.exe, a NATIVE Windows binary, so it
# wants drive-letter paths and ';' separators. Handing it the MSYS form makes it
# silently find nothing and configure then reports each codec as simply "not
# found using pkg-config" — indistinguishable from the codec not being built.
$winPcPaths = ($toolRoots | ForEach-Object { ($_ -replace '\\', '/') + '/lib/pkgconfig' }) -join ';'

# EXPORT, not a command-prefix assignment: `VAR=x cd dir && ./configure` scopes
# the assignment to `cd` alone (a regular builtin), so configure would run
# without the prefix on PATH and never find nasm or the codecs.
$depsFlag = if ($msysToolBins) {
    "export PKG_CONFIG_PATH='$winPcPaths'; export PATH='${msysToolBins}:'`$PATH; "
} else { '' }

# FFmpeg's configure compiles probe files under $TMPDIR (default /tmp) and
# passes the path straight to the compiler. Our gcc is a NATIVE Windows binary
# from the prefix, not an MSYS one, so it cannot resolve Git Bash's `/tmp` and
# every probe dies with
#   cc1.exe: fatal error: /tmp/ffconf.XXXX/test.c: No such file or directory
#   C compiler test failed.
# which reads as a broken toolchain rather than a path-translation mismatch.
$winTmp = ($env:CVC_BUILD_DIR -replace '\\', '/') + '/cfgtmp'
New-Item -ItemType Directory -Force -Path (Join-Path $env:CVC_BUILD_DIR 'cfgtmp') | Out-Null
$depsFlag = "export TMPDIR='$winTmp'; " + $depsFlag

# zlib's GNU-named import alias (lib/libz.dll.a) is produced by the zlib
# recipe itself (rev 3+), which also asserts it exists in shared builds. This
# recipe used to synthesise one here with gendef+dlltool, which worked but put
# the fix in the wrong place — and its cleanup pass deleted "too small"
# libz.dll.a files from the SHARED deps prefix, i.e. someone else's shipped
# artifact. See recipes/zlib/build.ps1 for why only the import lib can be
# aliased and why there is deliberately no MinGW libz.a.

# --cross-prefix only when the cross-named binutils exist. cvcpkg's own
# mingw-w64-gcc is a NATIVE toolchain: cross-named compilers, plain-named
# binutils (strings.exe, ar.exe). Passing it unconditionally makes configure
# hunt x86_64-w64-mingw32-strings and die "endian test failed" without ever
# naming the missing tool.
$crossFlag = ''
foreach ($r in $toolRoots) {
    if (Test-Path (Join-Path $r 'bin\x86_64-w64-mingw32-strings.exe')) {
        $crossFlag = "--cross-prefix=x86_64-w64-mingw32- \`n    "
        break
    }
}

# cvcpkg DOES package the redistributable GCC/threading DLLs now
# (recipes/mingw-w64-runtime, added for the old ffmpeg-cli), but this recipe
# deliberately does not depend on them: it ships dozens of binaries, so the
# GCC runtime is linked statically instead and nothing we ship may import
# libgcc_s_seh-1.dll / libstdc++-6.dll / libwinpthread-1.dll (asserted below).
# -static-libstdc++ is not belt-and-braces here: x265 is C++, so linking it
# pulls libstdc++ into both the shared libavcodec and the executables.
$extraPathFlags = if ($winDeps) {
    "--extra-cflags='-I$winDeps/include' \`n    --extra-ldflags='-L$winDeps/lib -L$winDeps/bin' \`n    "
} else { '' }

$runtimeFlags = "-static-libgcc -static-libstdc++"
$linkFlags = if ($env:CVC_LINK -eq 'static') {
    "--enable-static --disable-shared --pkg-config-flags=--static " +
    "--extra-ldexeflags='-static $runtimeFlags'"
} else {
    "--disable-static --enable-shared " +
    "--extra-ldexeflags='$runtimeFlags' --extra-ldsoflags='$runtimeFlags'"
}

# IN-TREE build, deliberately. Out of tree, configure records SRC_PATH as the
# MSYS path it was invoked by (/c/Users/...) and bakes it into the Makefile;
# our gcc is a NATIVE Windows binary that cannot resolve /c/..., so every
# object fails with
#   cc1.exe: fatal error: /c/.../src/libavformat/avformat.c: No such file
# Building in the source tree makes SRC_PATH '.', so no absolute path is ever
# handed to the compiler.
#
# Note there is no --enable-programs: the binaries are ON by default and only
# --disable-programs exists. Not passing it is what ships ffmpeg/ffprobe.
$configureCmd = @"
$depsFlag cd '$msysSource' && \
  ./configure \
    --prefix='$winPrefix' \
    --target-os=mingw32 \
    --arch=x86_64 \
    $crossFlag--enable-gpl \
    --enable-pic \
    --enable-version3 \
    $extraPathFlags$linkFlags \
    --disable-doc \
    --disable-debug \
    --enable-libx264 \
    --enable-libx265 \
    --enable-libopus \
    --enable-libmp3lame \
    --enable-libvorbis \
    --enable-libvpx \
    --enable-libdav1d \
    --enable-libwebp \
    --enable-libfreetype \
    --enable-libfribidi \
    --enable-openssl \
    --enable-zlib \
    --enable-bzlib \
    --enable-lzma \
    --enable-w32threads \
    --enable-dxva2 \
    --enable-d3d11va \
  && make -j $jobs \
  && make install
"@

Write-Host "cvcpkg: bash -lc <ffmpeg configure + make>"
& $bash -lc $configureCmd
if ($LASTEXITCODE -ne 0) {
    # In-tree build, so ffbuild/ lives under the SOURCE dir, not the build dir.
    $cfgLog = Join-Path $env:CVC_SOURCE_DIR 'ffbuild\config.log'
    if (Test-Path $cfgLog) {
        Write-Host '--- config.log (last 80 lines) ---'
        Get-Content $cfgLog -Tail 80 | Write-Host
    }
    throw 'FFmpeg build failed'
}

# MinGW writes import libraries as libavcodec.dll.a; package.files (and any
# MSVC consumer's find_library) wants avcodec.lib. Copy rather than rename so
# both names resolve. This is what Invoke-CvcMsysAutotoolsBuild does for the
# recipes that go through it — this one drives configure directly, so it has to
# do the same by hand or ship a package whose declared lib/*.lib never exist.
$installLib = Join-Path $env:CVC_INSTALL_DIR 'lib'
if (Test-Path $installLib) {
    $patterns = if ($env:CVC_LINK -eq 'static') { @('lib*.a') } else { @('lib*.dll.a') }
    foreach ($pat in $patterns) {
        foreach ($f in Get-ChildItem -Path $installLib -File -Filter $pat -ErrorAction SilentlyContinue) {
            if ($pat -eq 'lib*.a' -and $f.Name -like '*.dll.a') { continue }
            $suffixLen = if ($f.Name -like '*.dll.a') { 6 } else { 2 }
            $stem = $f.Name.Substring(3, $f.Name.Length - 3 - $suffixLen)
            $dest = Join-Path $installLib ($stem + '.lib')
            if (-not (Test-Path $dest)) { Copy-Item -Force $f.FullName $dest }
        }
    }
}

# Prove the codec set rather than trusting configure — a missing external
# codec does not fail the build, it just silently drops the encoder.
$ff = Join-Path $env:CVC_INSTALL_DIR 'bin\ffmpeg.exe'
if (-not (Test-Path $ff)) { throw "ffmpeg: no ffmpeg.exe produced at $ff" }
if (-not (Test-Path (Join-Path $env:CVC_INSTALL_DIR 'bin\ffprobe.exe'))) {
    throw 'ffmpeg: no ffprobe.exe produced'
}

$encoders = @(& $ff -hide_banner -encoders 2>&1)
foreach ($want in @('libx264', 'libx265', 'libopus', 'libmp3lame', 'libvorbis', 'libvpx', 'libwebp')) {
    if (-not ($encoders | Select-String -SimpleMatch $want)) {
        throw "ffmpeg: built ffmpeg.exe is missing the $want encoder"
    }
}
$decoders = @(& $ff -hide_banner -decoders 2>&1)
foreach ($want in @('libdav1d', 'h264', 'hevc', 'vp9')) {
    if (-not ($decoders | Select-String -Pattern "\b$want\b")) {
        throw "ffmpeg: built ffmpeg.exe is missing the $want decoder"
    }
}

# Match on the NAME column only — a substring test would let `scale2ref` or
# `zscale` satisfy a check for `scale`. These are the filters the documented
# capture pipelines use (see deliverables/*/CAPTURE-NOTES.md).
$filterNames = @(& $ff -hide_banner -filters 2>&1 |
    ForEach-Object { if ($_ -match '^\s*[A-Z\.]{3}\s+(\S+)\s+\S*->\S*') { $matches[1] } })
foreach ($want in @('scale', 'format', 'fps', 'hstack', 'vstack', 'overlay', 'pad', 'crop', 'setpts')) {
    if ($filterNames -notcontains $want) { throw "ffmpeg: built ffmpeg.exe is missing the $want filter" }
}

# The MinGW runtime must not have leaked into anything we ship. This recipe
# links the runtime statically rather than depending on mingw-w64-runtime, so
# an import here is an install that breaks on a machine without MSYS2 — and
# passes every test on this one.
# Check EVERY shipped binary, not just ffmpeg.exe: in a shared build the
# executables are thin and the runtime would leak in via avcodec-*.dll instead.
$objdump = @($toolRoots | ForEach-Object { Join-Path $_ 'bin\objdump.exe' } | Where-Object { Test-Path $_ }) |
    Select-Object -First 1
if ($objdump) {
    $shipped = @(Get-ChildItem -Path (Join-Path $env:CVC_INSTALL_DIR 'bin') -File `
        -Include '*.exe', '*.dll' -Recurse -ErrorAction SilentlyContinue)
    foreach ($bin in $shipped) {
        $imports = & $objdump -p $bin.FullName 2>$null | Select-String -Pattern 'DLL Name:\s*(\S+)' |
            ForEach-Object { $_.Matches[0].Groups[1].Value }
        $bad = @($imports | Where-Object { $_ -match '(?i)^(libgcc|libstdc\+\+|libwinpthread)' })
        if ($bad) {
            throw "ffmpeg: $($bin.Name) imports unpackaged MinGW runtime: $($bad -join ', ')"
        }
    }
    Write-Host "ffmpeg: $($shipped.Count) shipped binaries carry no MinGW runtime imports"
}

# In a static link, `-static` makes ld prefer libz.a — and the only libz.a a
# MinGW link can find is MSYS2's ambient one (cvcpkg's zlib ships the import
# alias only and deletes any libz.a; see recipes/zlib). An absorbed ambient
# zlib is invisible on the import table; the one witness is the version string
# zlib bakes into deflate.c. Compare it against the zlib.h actually in the
# prefix. (Learned on the old ffmpeg-cli: the wrong binary looked BETTER — no
# zlib1.dll import at all — and only its baked 1.3.2 vs the recipe's 1.3.1
# gave it away.)
if ($env:CVC_LINK -eq 'static') {
    $zh = $toolRoots | ForEach-Object { Join-Path $_ 'include\zlib.h' } |
        Where-Object { Test-Path $_ } | Select-Object -First 1
    if ($zh) {
        $wantMatch = Select-String -Path $zh -Pattern '#define\s+ZLIB_VERSION\s+"([^"]+)"' |
            Select-Object -First 1
        $bakedMatch = Select-String -Path $ff -Pattern 'deflate\s+([0-9][0-9.]*)\s+Copyright' |
            Select-Object -First 1
        if ($wantMatch -and $bakedMatch) {
            $want  = $wantMatch.Matches[0].Groups[1].Value
            $baked = $bakedMatch.Matches[0].Groups[1].Value
            if ($baked -ne $want) {
                throw ("ffmpeg: statically absorbed zlib $baked but the prefix zlib is $want " +
                       '— ld fell through to an ambient libz.a')
            }
            Write-Host "ffmpeg: static zlib is $baked (matches prefix zlib.h)"
        }
    }
}

Write-Host "ffmpeg: $((& $ff -version 2>&1 | Select-Object -First 1)) — full codec set + programs OK"

Invoke-CvcRewriteInstallPaths
