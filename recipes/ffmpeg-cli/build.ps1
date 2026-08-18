# recipes/ffmpeg-cli/build.ps1 — build the ffmpeg/ffprobe BINARIES on Windows
# via MSYS2/MinGW64, H.264 only.
#
# Sibling of recipes/ffmpeg, which builds --disable-programs (libraries for
# other recipes to link). This one exists for consumers that shell out to an
# `ffmpeg` executable.
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-windows.ps1"

$bash       = Get-CvcGitBash
$msysPrefix = ConvertTo-CvcMsysPath $env:CVC_INSTALL_DIR
$msysSource = ConvertTo-CvcMsysPath $env:CVC_SOURCE_DIR
$msysBuild  = ConvertTo-CvcMsysPath $env:CVC_BUILD_DIR
$msysDeps   = if ($env:CVC_DEPS_PREFIX) { ConvertTo-CvcMsysPath $env:CVC_DEPS_PREFIX } else { '' }
$jobs       = if ($env:CVC_JOBS) { [int]$env:CVC_JOBS } else { 4 }
if ($jobs -le 0) { $jobs = 4 }

New-Item -ItemType Directory -Force -Path $env:CVC_BUILD_DIR | Out-Null

$env:MSYSTEM          = 'MINGW64'
$env:MSYS_NO_PATHCONV = '1'
$env:CHERE_INVOKING   = '1'

# Clear MSVC env so MinGW-w64 gcc is selected by FFmpeg's configure.
'CC','CXX','LD','AR','NM','RANLIB','CFLAGS','CXXFLAGS','LDFLAGS' |
    ForEach-Object { Remove-Item "Env:$_" -ErrorAction SilentlyContinue }

# EXPORT, not a command-prefix assignment: `VAR=x cd dir && ./configure` scopes
# the assignment to `cd` alone (a regular builtin), so configure would run
# without the prefix on PATH and fail to find nasm/x264. Same bug the x264
# recipe carried.
# Host tools (the compiler, nasm) are staged into CVC_BUILD_PREFIX, NOT the
# runtime prefix — putting a toolchain in the runtime prefix shadows MSVC for
# every later CMake build against it. Search the build prefix first so our
# pinned gcc wins over any ambient C:\msys64\mingw64\bin.
$toolRoots = @($env:CVC_BUILD_PREFIX, $env:CVC_DEPS_PREFIX) | Where-Object { $_ }

# PATH is consumed by the SHELL, so it wants MSYS form and ':' separators.
$msysToolBins = ($toolRoots | ForEach-Object { (ConvertTo-CvcMsysPath $_) + '/bin' }) -join ':'

# PKG_CONFIG_PATH is consumed by pkg-config.exe, a NATIVE Windows binary, so it
# wants drive-letter paths and ';' separators. Handing it the MSYS form made it
# silently find nothing, and ffmpeg's configure reported
#   ERROR: x264 not found using pkg-config
# with x264.pc sitting in the build prefix -- indistinguishable from x264 not
# being built. Only --with-deps hit it, because a plain build happened to find
# x264 already merged into the deps prefix. numpy-cp312 gets this right; this
# did not.
$winPcPaths = ($toolRoots | ForEach-Object { ($_ -replace '\\', '/') + '/lib/pkgconfig' }) -join ';'

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
# A drive-letter path with forward slashes is understood by BOTH bash and the
# native compiler, so point TMPDIR at one inside the build dir.
# Headers and libs can live in EITHER prefix: link deps (zlib, x264) land in
# the deps prefix normally but in the BUILD prefix under --with-deps, so search
# both rather than assuming. MSYS form here — these are consumed by the shell
# on their way into configure's own compiler invocations.
$extraCflags = ($toolRoots | ForEach-Object { "-I$(ConvertTo-CvcMsysPath $_)/include" }) -join ' '
$extraLdflags = ($toolRoots | ForEach-Object {
    $m = ConvertTo-CvcMsysPath $_; "-L$m/lib -L$m/bin"
}) -join ' '

$winTmp = ($env:CVC_BUILD_DIR -replace '\\', '/') + '/cfgtmp'
New-Item -ItemType Directory -Force -Path (Join-Path $env:CVC_BUILD_DIR 'cfgtmp') | Out-Null
$depsFlag = "export TMPDIR='$winTmp'; " + $depsFlag

# zlib's GNU-named aliases (libz.a / libz.dll.a) are produced by the zlib
# recipe itself now. This recipe used to synthesise an import library here with
# gendef+dlltool, which worked but put the fix in the wrong place -- every
# future MinGW consumer would have rediscovered the same "zlib requested but
# not found" against a zlib that was plainly installed. See recipes/zlib.

# --cross-prefix only when the cross-named binutils exist. cvcpkg's own
# mingw-w64-gcc is a NATIVE toolchain: cross-named compilers, plain-named
# binutils (strings.exe, ar.exe). Passing it unconditionally makes configure
# hunt x86_64-w64-mingw32-strings and die without naming the missing tool.
$crossProbe = Join-Path $env:CVC_DEPS_PREFIX 'bin\x86_64-w64-mingw32-strings.exe'
$crossFlag  = if ($env:CVC_DEPS_PREFIX -and (Test-Path $crossProbe)) {
    "--cross-prefix=x86_64-w64-mingw32- \`n    "
} else { '' }

# Honour CVC_LINK in both directions, like every other recipe.
#
# static (the default here) is what makes the shipped ffmpeg.exe SELF-CONTAINED:
# it absorbs libx264 and the GCC runtime, so the binary imports no MinGW DLL and
# nothing has to be hand-copied into the prefix. cvcpkg packages no MinGW
# runtime, so a shared build would leave ffmpeg.exe depending on libgcc/
# libwinpthread that no package provides — a hermeticity hole.
#
# -static-libgcc/-static-libstdc++ are belt and braces for the shared case:
# even then, do not make the GCC runtime someone else's problem.
#
# But deliberately NOT a fully static `-static` link. `-static` makes ld prefer
# libz.a over libz.dll.a, and cvcpkg's zlib is MSVC-built with no
# MinGW-linkable static archive (renaming zlibstatic.lib does not work — its
# objects reference MSVC's /GS symbols; see recipes/zlib). The search therefore
# fell straight through to C:\msys64\mingw64\lib\libz.a and silently linked an
# AMBIENT zlib.
#
# That artifact looked BETTER than the correct one — no zlib1.dll import at all
# — and the only evidence was the version string baked into the binary: 1.3.2,
# where cvcpkg's recipe is 1.3.1. Worth remembering as a detection method.
#
# Without -static, ld uses our libz.dll.a and zlib comes from the prefix's own
# zlib1.dll. libx264 is still absorbed statically (there is a real libx264.a)
# and the GCC runtime stays in, so the single added runtime dependency is a
# cvcpkg-built DLL sitting beside the exe.
# -static-libgcc covers libgcc, but NOT libwinpthread, which x264 pulls in, so
# the exe imports libwinpthread-1.dll. That used to be fatal — nothing packaged
# the MinGW runtime, so ffmpeg.exe could not start from a clean prefix.
#
# The attempted fix here was to bracket just that library, `-Wl,-Bstatic
# -lwinpthread -Wl,-Bdynamic`, absorbing pthreads while leaving zlib dynamic.
# It does not work: --extra-ldexeflags are emitted BEFORE the object files and
# libraries on the link line, so by the time ld resolves the real -lwinpthread
# the -Bdynamic is long since back in effect. The import survives.
#
# mingw-w64-runtime removes the need. It packages the eight redistributable
# GCC/threading DLLs from the SAME pinned WinLibs artifact as mingw-w64-gcc, so
# the runtime always matches the compiler that produced the code, and it is
# declared in this recipe's depends.runtime. Link plainly and let the package
# supply the DLLs:
#   static:  libx264, libgcc
#   dynamic: zlib1.dll + libwinpthread-1.dll — all cvcpkg-built, beside the exe
$gccRuntime = "-static-libgcc"
$linkFlags = if ($env:CVC_LINK -eq 'shared') {
    "--enable-shared --disable-static --extra-ldexeflags='$gccRuntime'"
} else {
    "--disable-shared --enable-static --extra-ldexeflags='$gccRuntime'"
}

# Everything off, then H.264 encode + the demuxers a PNG/image sequence needs
# back on. Note there is no --enable-programs: the binaries are on by default
# and only --disable-programs exists (which is exactly what the sibling
# `ffmpeg` library recipe passes).
# IN-TREE build, deliberately. Out of tree, configure records SRC_PATH as the
# MSYS path it was invoked by (/c/Users/...) and bakes it into the Makefile;
# our gcc is a NATIVE Windows binary that cannot resolve /c/..., so every
# object failed with
#   cc1.exe: fatal error: /c/.../src/libavformat/avformat.c: No such file
# Building in the source tree makes SRC_PATH '.', so no absolute path is ever
# handed to the compiler and the translation problem disappears. This is also
# why the sibling x264 recipe never hit it.
$configureCmd = @"
$depsFlag cd '$msysSource' && \
  ./configure \
    --prefix='$msysPrefix' \
    --target-os=mingw32 \
    --arch=x86_64 \
    $crossFlag--enable-gpl \
    --enable-version3 \
    --extra-cflags='$extraCflags' \
    --extra-ldflags='$extraLdflags' \
    $linkFlags \
    --disable-doc \
    --disable-debug \
    --disable-everything \
    --disable-network \
    --disable-autodetect \
    --enable-zlib \
    --enable-w32threads \
    --enable-libx264 \
    --enable-encoder=libx264 \
    --enable-decoder=h264 \
    --enable-parser=h264 \
    --enable-encoder=png \
    --enable-encoder=mjpeg \
    --enable-decoder=png \
    --enable-decoder=mjpeg \
    --enable-decoder=rawvideo \
    --enable-demuxer=image2 \
    --enable-demuxer=image2pipe \
    --enable-demuxer=rawvideo \
    --enable-demuxer=mov \
    --enable-muxer=mp4 \
    --enable-muxer=mov \
    --enable-muxer=image2 \
    --enable-muxer=rawvideo \
    --enable-protocol=file \
    --enable-protocol=pipe \
    --enable-filter=scale \
    --enable-filter=format \
    --enable-filter=fps \
    --enable-swscale \
  && make -j $jobs \
  && make install
"@

Write-Host "cvcpkg: bash -lc <ffmpeg-cli configure + make>"
& $bash -lc $configureCmd
if ($LASTEXITCODE -ne 0) {
    # In-tree build, so ffbuild/ lives under the SOURCE dir, not the build dir.
    $cfgLog = Join-Path $env:CVC_SOURCE_DIR 'ffbuild\config.log'
    if (Test-Path $cfgLog) {
        Write-Host '--- config.log (last 80 lines) ---'
        Get-Content $cfgLog -Tail 80 | Write-Host
    }
    throw 'ffmpeg-cli build failed'
}

# The whole point of this package: an executable that actually exists and can
# encode H.264 into MP4. Prove both rather than trusting configure.
$ff = Join-Path $env:CVC_INSTALL_DIR 'bin\ffmpeg.exe'
if (-not (Test-Path $ff)) { throw "ffmpeg-cli: no ffmpeg.exe produced at $ff" }

# These checks RUN the binary, and it links zlib1.dll from the dependency
# prefix. At this point we are still in the per-recipe install dir — nothing
# has been merged into the prefix yet — so the DLL is not beside the exe and
# Windows fails the load. The process then produces no output at all, and the
# checks below misreport it as "no libx264 encoder" when the encoder is present
# and the binary simply never started.
$runPath = ($toolRoots | ForEach-Object { Join-Path $_ 'bin' } | Where-Object { Test-Path $_ }) -join ';'
if ($runPath) { $env:PATH = "$runPath;$env:PATH" }
& $ff -hide_banner -version > $null 2>&1
if ($LASTEXITCODE -ne 0) {
    throw ("ffmpeg-cli: built ffmpeg.exe cannot start (exit $LASTEXITCODE). " +
           "A 0xC0000135 here means a runtime DLL is missing from the dependency prefix.")
}
$enc = & $ff -hide_banner -encoders 2>&1 | Select-String -Pattern 'libx264'
if (-not $enc) { throw 'ffmpeg-cli: built ffmpeg.exe has no libx264 encoder' }
$mux = & $ff -hide_banner -muxers 2>&1 | Select-String -Pattern '\bmp4\b'
if (-not $mux) { throw 'ffmpeg-cli: built ffmpeg.exe has no mp4 muxer' }
# --enable-libx264 gives an ENCODER only. Without the native h264 decoder the
# tool cannot read back a file it just wrote — pulling a poster frame out of
# its own mp4 fails "Decoding requested, but no decoder found for: h264".
$dec = & $ff -hide_banner -decoders 2>&1 | Select-String -Pattern '\bh264\b'
if (-not $dec) { throw 'ffmpeg-cli: built ffmpeg.exe cannot decode h264 (cannot read its own output)' }
Write-Host "ffmpeg-cli: $((& $ff -version 2>&1 | Select-Object -First 1)) — libx264 + mp4 OK"

Invoke-CvcRewriteInstallPaths
