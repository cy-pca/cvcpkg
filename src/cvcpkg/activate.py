"""Generate shell activation scripts for a cvcpkg prefix.

Modeled on Python's ``venv`` — running::

    source <prefix>/bin/activate           # bash / zsh
    source <prefix>/bin/activate.fish      # fish
    source <prefix>/bin/activate.csh       # csh / tcsh
    <prefix>\\Scripts\\Activate.ps1         # PowerShell
    <prefix>\\Scripts\\activate.bat         # cmd.exe

prepends the prefix's ``bin`` directory to ``PATH``, sets
``CMAKE_PREFIX_PATH``, ``PKG_CONFIG_PATH``, and the appropriate
platform-specific dynamic-library-loader variable
(``LD_LIBRARY_PATH`` on Linux/BSD, ``DYLD_LIBRARY_PATH`` on macOS),
and defines a ``cvcpkg_deactivate`` command that restores the
previous environment.

The install target layout follows the platform convention:
* POSIX prefixes → ``<prefix>/bin/activate*``
* Windows prefixes → ``<prefix>/Scripts/Activate.ps1`` and
  ``<prefix>/Scripts/activate.bat``

Scripts are self-contained (no runtime cvcpkg dependency) and can be
copied with the prefix.
"""

from __future__ import annotations

from pathlib import Path

# ── template payloads ──────────────────────────────────────────────
#
# Each template uses a small set of substitutions:
#   __CVCPKG_PREFIX__       absolute prefix path (POSIX form or Windows form)
#   __CVCPKG_PROMPT__       prompt tag shown in $PS1/$prompt
#   __CVCPKG_LIB_VAR__      LD_LIBRARY_PATH or DYLD_LIBRARY_PATH (POSIX only)
#
# We do not use Python format strings so that the templates can freely
# contain ``{``/``}`` shell constructs.

_BASH_TEMPLATE = r"""# cvcpkg activate — source this file, do not execute.
#
#     source __CVCPKG_PREFIX__/bin/activate
#
# Restore with:
#
#     cvcpkg_deactivate

if [ "${BASH_SOURCE-}" = "$0" ] 2>/dev/null; then
    echo "cvcpkg: activate must be sourced, not executed" 1>&2
    exit 33
fi

cvcpkg_deactivate() {
    if [ "${_CVCPKG_HAD_PATH-0}" = "1" ]; then
        PATH="${_CVCPKG_OLD_PATH-}"
        export PATH
    fi
    unset _CVCPKG_OLD_PATH _CVCPKG_HAD_PATH

    if [ "${_CVCPKG_HAD_CMAKE_PREFIX_PATH-0}" = "1" ]; then
        CMAKE_PREFIX_PATH="${_CVCPKG_OLD_CMAKE_PREFIX_PATH-}"
        export CMAKE_PREFIX_PATH
    else
        unset CMAKE_PREFIX_PATH 2>/dev/null || true
    fi
    unset _CVCPKG_OLD_CMAKE_PREFIX_PATH _CVCPKG_HAD_CMAKE_PREFIX_PATH

    if [ "${_CVCPKG_HAD_PKG_CONFIG_PATH-0}" = "1" ]; then
        PKG_CONFIG_PATH="${_CVCPKG_OLD_PKG_CONFIG_PATH-}"
        export PKG_CONFIG_PATH
    else
        unset PKG_CONFIG_PATH 2>/dev/null || true
    fi
    unset _CVCPKG_OLD_PKG_CONFIG_PATH _CVCPKG_HAD_PKG_CONFIG_PATH

    if [ "${_CVCPKG_HAD_LIB_PATH-0}" = "1" ]; then
        eval "__CVCPKG_LIB_VAR__=\"\${_CVCPKG_OLD_LIB_PATH-}\""
        export __CVCPKG_LIB_VAR__
    else
        unset __CVCPKG_LIB_VAR__ 2>/dev/null || true
    fi
    unset _CVCPKG_OLD_LIB_PATH _CVCPKG_HAD_LIB_PATH

    if [ "${_CVCPKG_HAD_PS1-0}" = "1" ]; then
        PS1="${_CVCPKG_OLD_PS1-}"
        export PS1
    fi
    unset _CVCPKG_OLD_PS1 _CVCPKG_HAD_PS1

    unset CVCPKG_ACTIVE_PREFIX
    if [ -n "${BASH-}${ZSH_VERSION-}" ]; then
        hash -r 2>/dev/null || true
    fi
    if [ ! "${1-}" = "nondestructive" ]; then
        unset -f cvcpkg_deactivate 2>/dev/null || true
    fi
}

cvcpkg_deactivate nondestructive

if [ -n "${PATH+set}" ]; then
    _CVCPKG_OLD_PATH="${PATH}"
    _CVCPKG_HAD_PATH=1
fi
if [ -n "${CMAKE_PREFIX_PATH+set}" ]; then
    _CVCPKG_OLD_CMAKE_PREFIX_PATH="${CMAKE_PREFIX_PATH}"
    _CVCPKG_HAD_CMAKE_PREFIX_PATH=1
fi
if [ -n "${PKG_CONFIG_PATH+set}" ]; then
    _CVCPKG_OLD_PKG_CONFIG_PATH="${PKG_CONFIG_PATH}"
    _CVCPKG_HAD_PKG_CONFIG_PATH=1
fi
eval "if [ -n \"\${__CVCPKG_LIB_VAR__+set}\" ]; then _CVCPKG_OLD_LIB_PATH=\"\${__CVCPKG_LIB_VAR__}\"; _CVCPKG_HAD_LIB_PATH=1; fi"
if [ -n "${PS1+set}" ]; then
    _CVCPKG_OLD_PS1="${PS1}"
    _CVCPKG_HAD_PS1=1
fi

CVCPKG_ACTIVE_PREFIX="__CVCPKG_PREFIX__"
export CVCPKG_ACTIVE_PREFIX

PATH="${CVCPKG_ACTIVE_PREFIX}/bin:${PATH}"
export PATH

if [ -n "${CMAKE_PREFIX_PATH-}" ]; then
    CMAKE_PREFIX_PATH="${CVCPKG_ACTIVE_PREFIX}:${CMAKE_PREFIX_PATH}"
else
    CMAKE_PREFIX_PATH="${CVCPKG_ACTIVE_PREFIX}"
fi
export CMAKE_PREFIX_PATH

for _cvcpkg_pcdir in \
    "${CVCPKG_ACTIVE_PREFIX}/lib/pkgconfig" \
    "${CVCPKG_ACTIVE_PREFIX}/lib64/pkgconfig" \
    "${CVCPKG_ACTIVE_PREFIX}/share/pkgconfig"; do
    if [ -d "${_cvcpkg_pcdir}" ]; then
        if [ -n "${PKG_CONFIG_PATH-}" ]; then
            case ":${PKG_CONFIG_PATH}:" in
                *":${_cvcpkg_pcdir}:"*) : ;;
                *) PKG_CONFIG_PATH="${_cvcpkg_pcdir}:${PKG_CONFIG_PATH}" ;;
            esac
        else
            PKG_CONFIG_PATH="${_cvcpkg_pcdir}"
        fi
    fi
done
unset _cvcpkg_pcdir
export PKG_CONFIG_PATH

_cvcpkg_libpath=""
for _cvcpkg_libdir in \
    "${CVCPKG_ACTIVE_PREFIX}/lib" \
    "${CVCPKG_ACTIVE_PREFIX}/lib64"; do
    if [ -d "${_cvcpkg_libdir}" ]; then
        if [ -n "${_cvcpkg_libpath}" ]; then
            _cvcpkg_libpath="${_cvcpkg_libpath}:${_cvcpkg_libdir}"
        else
            _cvcpkg_libpath="${_cvcpkg_libdir}"
        fi
    fi
done
if [ -n "${_cvcpkg_libpath}" ]; then
    eval "_cvcpkg_existing=\"\${__CVCPKG_LIB_VAR__-}\""
    if [ -n "${_cvcpkg_existing}" ]; then
        eval "__CVCPKG_LIB_VAR__=\"\${_cvcpkg_libpath}:\${_cvcpkg_existing}\""
    else
        eval "__CVCPKG_LIB_VAR__=\"\${_cvcpkg_libpath}\""
    fi
    export __CVCPKG_LIB_VAR__
fi
unset _cvcpkg_libdir _cvcpkg_libpath _cvcpkg_existing

if [ -z "${CVCPKG_ACTIVATE_NO_PROMPT-}" ]; then
    PS1="(__CVCPKG_PROMPT__) ${PS1-}"
    export PS1
fi

if [ -n "${BASH-}${ZSH_VERSION-}" ]; then
    hash -r 2>/dev/null || true
fi
"""


_FISH_TEMPLATE = r"""# cvcpkg activate — source with:  source __CVCPKG_PREFIX__/bin/activate.fish

function cvcpkg_deactivate --description "restore shell state before cvcpkg activation"
    if set -q _CVCPKG_OLD_PATH
        set -gx PATH $_CVCPKG_OLD_PATH
        set -e _CVCPKG_OLD_PATH
    end
    if set -q _CVCPKG_OLD_CMAKE_PREFIX_PATH
        set -gx CMAKE_PREFIX_PATH $_CVCPKG_OLD_CMAKE_PREFIX_PATH
        set -e _CVCPKG_OLD_CMAKE_PREFIX_PATH
    else
        set -e CMAKE_PREFIX_PATH
    end
    if set -q _CVCPKG_OLD_PKG_CONFIG_PATH
        set -gx PKG_CONFIG_PATH $_CVCPKG_OLD_PKG_CONFIG_PATH
        set -e _CVCPKG_OLD_PKG_CONFIG_PATH
    else
        set -e PKG_CONFIG_PATH
    end
    if set -q _CVCPKG_OLD_LIB_PATH
        set -gx __CVCPKG_LIB_VAR__ $_CVCPKG_OLD_LIB_PATH
        set -e _CVCPKG_OLD_LIB_PATH
    else
        set -e __CVCPKG_LIB_VAR__
    end
    if set -q _CVCPKG_OLD_FISH_PROMPT
        functions -e fish_prompt
        functions -c _cvcpkg_old_fish_prompt fish_prompt
        functions -e _cvcpkg_old_fish_prompt
        set -e _CVCPKG_OLD_FISH_PROMPT
    end
    set -e CVCPKG_ACTIVE_PREFIX
    if test "$argv[1]" != "nondestructive"
        functions -e cvcpkg_deactivate
    end
end

cvcpkg_deactivate nondestructive

set -gx _CVCPKG_OLD_PATH $PATH
set -q CMAKE_PREFIX_PATH; and set -gx _CVCPKG_OLD_CMAKE_PREFIX_PATH $CMAKE_PREFIX_PATH
set -q PKG_CONFIG_PATH;   and set -gx _CVCPKG_OLD_PKG_CONFIG_PATH $PKG_CONFIG_PATH
set -q __CVCPKG_LIB_VAR__; and set -gx _CVCPKG_OLD_LIB_PATH $__CVCPKG_LIB_VAR__

set -gx CVCPKG_ACTIVE_PREFIX "__CVCPKG_PREFIX__"
set -gx PATH "$CVCPKG_ACTIVE_PREFIX/bin" $PATH

if set -q CMAKE_PREFIX_PATH
    set -gx CMAKE_PREFIX_PATH "$CVCPKG_ACTIVE_PREFIX:$CMAKE_PREFIX_PATH"
else
    set -gx CMAKE_PREFIX_PATH "$CVCPKG_ACTIVE_PREFIX"
end

for _cvcpkg_pcdir in \
    "$CVCPKG_ACTIVE_PREFIX/lib/pkgconfig" \
    "$CVCPKG_ACTIVE_PREFIX/lib64/pkgconfig" \
    "$CVCPKG_ACTIVE_PREFIX/share/pkgconfig"
    if test -d "$_cvcpkg_pcdir"
        if set -q PKG_CONFIG_PATH
            if not string match -q "*:$_cvcpkg_pcdir:*" ":$PKG_CONFIG_PATH:"
                set -gx PKG_CONFIG_PATH "$_cvcpkg_pcdir:$PKG_CONFIG_PATH"
            end
        else
            set -gx PKG_CONFIG_PATH "$_cvcpkg_pcdir"
        end
    end
end

set -l _cvcpkg_libpath
for _cvcpkg_libdir in "$CVCPKG_ACTIVE_PREFIX/lib" "$CVCPKG_ACTIVE_PREFIX/lib64"
    if test -d "$_cvcpkg_libdir"
        if test -n "$_cvcpkg_libpath"
            set _cvcpkg_libpath "$_cvcpkg_libpath:$_cvcpkg_libdir"
        else
            set _cvcpkg_libpath "$_cvcpkg_libdir"
        end
    end
end
if test -n "$_cvcpkg_libpath"
    if set -q __CVCPKG_LIB_VAR__
        set -gx __CVCPKG_LIB_VAR__ "$_cvcpkg_libpath:$__CVCPKG_LIB_VAR__"
    else
        set -gx __CVCPKG_LIB_VAR__ "$_cvcpkg_libpath"
    end
end

if not set -q CVCPKG_ACTIVATE_NO_PROMPT
    if functions -q fish_prompt
        functions -c fish_prompt _cvcpkg_old_fish_prompt
        function fish_prompt --description "cvcpkg-activated prompt"
            printf '(%s) ' "__CVCPKG_PROMPT__"
            _cvcpkg_old_fish_prompt
        end
        set -gx _CVCPKG_OLD_FISH_PROMPT 1
    end
end
"""


_CSH_TEMPLATE = r"""# cvcpkg activate — source with:  source __CVCPKG_PREFIX__/bin/activate.csh
# Restore with:  cvcpkg_deactivate

alias cvcpkg_deactivate 'test $?_CVCPKG_OLD_PATH != 0 && setenv PATH "$_CVCPKG_OLD_PATH" && unsetenv _CVCPKG_OLD_PATH; test $?_CVCPKG_OLD_CMAKE_PREFIX_PATH != 0 && setenv CMAKE_PREFIX_PATH "$_CVCPKG_OLD_CMAKE_PREFIX_PATH" && unsetenv _CVCPKG_OLD_CMAKE_PREFIX_PATH; test $?_CVCPKG_OLD_PKG_CONFIG_PATH != 0 && setenv PKG_CONFIG_PATH "$_CVCPKG_OLD_PKG_CONFIG_PATH" && unsetenv _CVCPKG_OLD_PKG_CONFIG_PATH; test $?_CVCPKG_OLD_LIB_PATH != 0 && setenv __CVCPKG_LIB_VAR__ "$_CVCPKG_OLD_LIB_PATH" && unsetenv _CVCPKG_OLD_LIB_PATH; test $?_CVCPKG_OLD_PROMPT != 0 && set prompt="$_CVCPKG_OLD_PROMPT" && unset _CVCPKG_OLD_PROMPT; unsetenv CVCPKG_ACTIVE_PREFIX; rehash; test "\!:1" != "nondestructive" && unalias cvcpkg_deactivate'

cvcpkg_deactivate nondestructive

setenv _CVCPKG_OLD_PATH "$PATH"
if ( $?CMAKE_PREFIX_PATH ) setenv _CVCPKG_OLD_CMAKE_PREFIX_PATH "$CMAKE_PREFIX_PATH"
if ( $?PKG_CONFIG_PATH )   setenv _CVCPKG_OLD_PKG_CONFIG_PATH "$PKG_CONFIG_PATH"
if ( $?__CVCPKG_LIB_VAR__ ) setenv _CVCPKG_OLD_LIB_PATH "$__CVCPKG_LIB_VAR__"
if ( $?prompt )            set   _CVCPKG_OLD_PROMPT="$prompt"

setenv CVCPKG_ACTIVE_PREFIX "__CVCPKG_PREFIX__"
setenv PATH "$CVCPKG_ACTIVE_PREFIX/bin:$PATH"

if ( $?CMAKE_PREFIX_PATH ) then
    setenv CMAKE_PREFIX_PATH "${CVCPKG_ACTIVE_PREFIX}:${CMAKE_PREFIX_PATH}"
else
    setenv CMAKE_PREFIX_PATH "$CVCPKG_ACTIVE_PREFIX"
endif

foreach _cvcpkg_pcdir ( "$CVCPKG_ACTIVE_PREFIX/lib/pkgconfig" "$CVCPKG_ACTIVE_PREFIX/lib64/pkgconfig" "$CVCPKG_ACTIVE_PREFIX/share/pkgconfig" )
    if ( -d "$_cvcpkg_pcdir" ) then
        if ( $?PKG_CONFIG_PATH ) then
            setenv PKG_CONFIG_PATH "${_cvcpkg_pcdir}:${PKG_CONFIG_PATH}"
        else
            setenv PKG_CONFIG_PATH "$_cvcpkg_pcdir"
        endif
    endif
end
unset _cvcpkg_pcdir

if ( -d "$CVCPKG_ACTIVE_PREFIX/lib" ) then
    if ( $?__CVCPKG_LIB_VAR__ ) then
        setenv __CVCPKG_LIB_VAR__ "${CVCPKG_ACTIVE_PREFIX}/lib:${__CVCPKG_LIB_VAR__}"
    else
        setenv __CVCPKG_LIB_VAR__ "$CVCPKG_ACTIVE_PREFIX/lib"
    endif
endif
if ( -d "$CVCPKG_ACTIVE_PREFIX/lib64" ) then
    setenv __CVCPKG_LIB_VAR__ "${CVCPKG_ACTIVE_PREFIX}/lib64:${__CVCPKG_LIB_VAR__}"
endif

if ( ! $?CVCPKG_ACTIVATE_NO_PROMPT ) then
    if ( $?prompt ) then
        set prompt = "(__CVCPKG_PROMPT__) $prompt"
    endif
endif

rehash
"""


# PowerShell — Activate.ps1
_POWERSHELL_TEMPLATE = r"""<#
.SYNOPSIS
    Activate a cvcpkg prefix in the current PowerShell session.

.DESCRIPTION
    Prepends the prefix's bin/Scripts directory to $env:PATH and sets
    CMAKE_PREFIX_PATH so downstream tooling (CMake, pkg-config,
    dependent linkers) can find installed components.

    Sourcing:
        . '__CVCPKG_PREFIX__\Scripts\Activate.ps1'

    Restore:
        cvcpkg_deactivate
#>

if ($Script:_CVCPKG_ACTIVE_PREFIX) {
    # Already activated — treat re-activation as a full reset first.
    cvcpkg_deactivate
}

$Script:_CVCPKG_OLD_PATH               = $env:PATH
$Script:_CVCPKG_OLD_CMAKE_PREFIX_PATH  = $env:CMAKE_PREFIX_PATH
$Script:_CVCPKG_OLD_PKG_CONFIG_PATH    = $env:PKG_CONFIG_PATH
$Script:_CVCPKG_OLD_PROMPT_FUNCTION    = ${function:prompt}
$Script:_CVCPKG_ACTIVE_PREFIX          = '__CVCPKG_PREFIX__'

$env:CVCPKG_ACTIVE_PREFIX = $Script:_CVCPKG_ACTIVE_PREFIX

# Prepend both the POSIX-style bin/ (rarely used on Windows) and the
# Windows-style Scripts/ so we work for either install layout.
$binDirs = @()
foreach ($d in @('bin', 'Scripts')) {
    $p = Join-Path $Script:_CVCPKG_ACTIVE_PREFIX $d
    if (Test-Path $p) { $binDirs += $p }
}
if ($binDirs.Count -gt 0) {
    $env:PATH = ($binDirs -join [IO.Path]::PathSeparator) + [IO.Path]::PathSeparator + $env:PATH
}

if ($env:CMAKE_PREFIX_PATH) {
    $env:CMAKE_PREFIX_PATH = $Script:_CVCPKG_ACTIVE_PREFIX + [IO.Path]::PathSeparator + $env:CMAKE_PREFIX_PATH
} else {
    $env:CMAKE_PREFIX_PATH = $Script:_CVCPKG_ACTIVE_PREFIX
}

foreach ($pc in @(
    (Join-Path $Script:_CVCPKG_ACTIVE_PREFIX 'lib/pkgconfig'),
    (Join-Path $Script:_CVCPKG_ACTIVE_PREFIX 'lib64/pkgconfig'),
    (Join-Path $Script:_CVCPKG_ACTIVE_PREFIX 'share/pkgconfig')
)) {
    if (Test-Path $pc) {
        if ($env:PKG_CONFIG_PATH) {
            $env:PKG_CONFIG_PATH = $pc + [IO.Path]::PathSeparator + $env:PKG_CONFIG_PATH
        } else {
            $env:PKG_CONFIG_PATH = $pc
        }
    }
}

if (-not $env:CVCPKG_ACTIVATE_NO_PROMPT) {
    function global:prompt {
        Write-Host -NoNewline -ForegroundColor Green "(__CVCPKG_PROMPT__) "
        & $Script:_CVCPKG_OLD_PROMPT_FUNCTION
    }
}

function global:cvcpkg_deactivate {
    param([switch]$NonDestructive)

    if ($Script:_CVCPKG_OLD_PATH -ne $null) {
        $env:PATH = $Script:_CVCPKG_OLD_PATH
        $Script:_CVCPKG_OLD_PATH = $null
    }
    if ($Script:_CVCPKG_OLD_CMAKE_PREFIX_PATH -ne $null) {
        $env:CMAKE_PREFIX_PATH = $Script:_CVCPKG_OLD_CMAKE_PREFIX_PATH
        $Script:_CVCPKG_OLD_CMAKE_PREFIX_PATH = $null
    } else {
        Remove-Item Env:CMAKE_PREFIX_PATH -ErrorAction SilentlyContinue
    }
    if ($Script:_CVCPKG_OLD_PKG_CONFIG_PATH -ne $null) {
        $env:PKG_CONFIG_PATH = $Script:_CVCPKG_OLD_PKG_CONFIG_PATH
        $Script:_CVCPKG_OLD_PKG_CONFIG_PATH = $null
    } else {
        Remove-Item Env:PKG_CONFIG_PATH -ErrorAction SilentlyContinue
    }
    if ($Script:_CVCPKG_OLD_PROMPT_FUNCTION) {
        Set-Item -Path function:global:prompt -Value $Script:_CVCPKG_OLD_PROMPT_FUNCTION
        $Script:_CVCPKG_OLD_PROMPT_FUNCTION = $null
    }
    Remove-Item Env:CVCPKG_ACTIVE_PREFIX -ErrorAction SilentlyContinue
    $Script:_CVCPKG_ACTIVE_PREFIX = $null

    if (-not $NonDestructive) {
        Remove-Item function:global:cvcpkg_deactivate -ErrorAction SilentlyContinue
    }
}
"""


# cmd.exe — activate.bat
_CMD_TEMPLATE = r"""@echo off
rem cvcpkg activate — run:  __CVCPKG_PREFIX__\Scripts\activate.bat
rem Restore with:  cvcpkg_deactivate.bat  (installed alongside this file)

if defined _CVCPKG_OLD_PATH (
    call "%~dp0cvcpkg_deactivate.bat"
)

set "_CVCPKG_OLD_PATH=%PATH%"
set "_CVCPKG_OLD_CMAKE_PREFIX_PATH=%CMAKE_PREFIX_PATH%"
set "_CVCPKG_OLD_PKG_CONFIG_PATH=%PKG_CONFIG_PATH%"
set "_CVCPKG_OLD_PROMPT=%PROMPT%"

set "CVCPKG_ACTIVE_PREFIX=__CVCPKG_PREFIX__"

if exist "%CVCPKG_ACTIVE_PREFIX%\bin" (
    set "PATH=%CVCPKG_ACTIVE_PREFIX%\bin;%PATH%"
)
if exist "%CVCPKG_ACTIVE_PREFIX%\Scripts" (
    set "PATH=%CVCPKG_ACTIVE_PREFIX%\Scripts;%PATH%"
)

if defined CMAKE_PREFIX_PATH (
    set "CMAKE_PREFIX_PATH=%CVCPKG_ACTIVE_PREFIX%;%CMAKE_PREFIX_PATH%"
) else (
    set "CMAKE_PREFIX_PATH=%CVCPKG_ACTIVE_PREFIX%"
)

for %%D in ("%CVCPKG_ACTIVE_PREFIX%\lib\pkgconfig" "%CVCPKG_ACTIVE_PREFIX%\lib64\pkgconfig" "%CVCPKG_ACTIVE_PREFIX%\share\pkgconfig") do (
    if exist %%D (
        if defined PKG_CONFIG_PATH (
            set "PKG_CONFIG_PATH=%%~D;%PKG_CONFIG_PATH%"
        ) else (
            set "PKG_CONFIG_PATH=%%~D"
        )
    )
)

if not defined CVCPKG_ACTIVATE_NO_PROMPT (
    set "PROMPT=(__CVCPKG_PROMPT__) %PROMPT%"
)
"""


_CMD_DEACTIVATE_TEMPLATE = r"""@echo off
rem cvcpkg_deactivate — restore the shell state saved by activate.bat.

if defined _CVCPKG_OLD_PATH (
    set "PATH=%_CVCPKG_OLD_PATH%"
    set "_CVCPKG_OLD_PATH="
)
if defined _CVCPKG_OLD_CMAKE_PREFIX_PATH (
    set "CMAKE_PREFIX_PATH=%_CVCPKG_OLD_CMAKE_PREFIX_PATH%"
    set "_CVCPKG_OLD_CMAKE_PREFIX_PATH="
) else (
    set "CMAKE_PREFIX_PATH="
)
if defined _CVCPKG_OLD_PKG_CONFIG_PATH (
    set "PKG_CONFIG_PATH=%_CVCPKG_OLD_PKG_CONFIG_PATH%"
    set "_CVCPKG_OLD_PKG_CONFIG_PATH="
) else (
    set "PKG_CONFIG_PATH="
)
if defined _CVCPKG_OLD_PROMPT (
    set "PROMPT=%_CVCPKG_OLD_PROMPT%"
    set "_CVCPKG_OLD_PROMPT="
)
set "CVCPKG_ACTIVE_PREFIX="
"""


# ── platform-specific lib-loader variable ─────────────────────────


def _lib_var_for_platform(platform: str) -> str:
    """Return the dynamic-loader environment variable for *platform*."""
    if platform == "macos":
        return "DYLD_LIBRARY_PATH"
    # Linux + all BSDs + wasi use LD_LIBRARY_PATH.  Windows uses PATH
    # (handled directly in the templates) and doesn't need a separate var.
    return "LD_LIBRARY_PATH"


# ── public rendering / writing API ────────────────────────────────


def _substitute(template: str, *, prefix: str, prompt: str, lib_var: str) -> str:
    return (
        template.replace("__CVCPKG_PREFIX__", prefix)
        .replace("__CVCPKG_PROMPT__", prompt)
        .replace("__CVCPKG_LIB_VAR__", lib_var)
    )


def render_bash(prefix: Path, *, prompt: str, platform: str) -> str:
    return _substitute(
        _BASH_TEMPLATE,
        prefix=prefix.as_posix(),
        prompt=prompt,
        lib_var=_lib_var_for_platform(platform),
    )


def render_fish(prefix: Path, *, prompt: str, platform: str) -> str:
    return _substitute(
        _FISH_TEMPLATE,
        prefix=prefix.as_posix(),
        prompt=prompt,
        lib_var=_lib_var_for_platform(platform),
    )


def render_csh(prefix: Path, *, prompt: str, platform: str) -> str:
    return _substitute(
        _CSH_TEMPLATE,
        prefix=prefix.as_posix(),
        prompt=prompt,
        lib_var=_lib_var_for_platform(platform),
    )


def render_powershell(prefix: Path, *, prompt: str) -> str:
    # PowerShell always uses the Windows-form absolute path.
    return _substitute(
        _POWERSHELL_TEMPLATE,
        prefix=str(prefix).replace("/", "\\"),
        prompt=prompt,
        lib_var="",  # unused
    )


def render_cmd(prefix: Path, *, prompt: str) -> str:
    return _substitute(
        _CMD_TEMPLATE,
        prefix=str(prefix).replace("/", "\\"),
        prompt=prompt,
        lib_var="",  # unused
    )


def render_cmd_deactivate() -> str:
    return _CMD_DEACTIVATE_TEMPLATE


def _ensure_python_aliases(bin_dir: Path) -> list[Path]:
    """Materialize generic python/pip commands in *bin_dir* when missing.

    Version-specific python recipes ship only versioned binaries
    (``python3.13``, ``pip3.13``, ``python3.13-config``) so that several
    minor versions can be installed into one prefix side by side without
    fighting over the generic names. At install time we surface the
    conventional commands users expect on PATH — ``python3``, ``python``,
    ``pip3``, ``pip``, ``python3-config`` — as relative symlinks to the
    highest installed version, but ONLY when the name is not already provided
    (e.g. by the ``python3`` meta-package or the user). That keeps the build
    graph collision-free while making activated install trees ergonomic.

    Returns the list of alias paths created.
    """
    import re

    if not bin_dir.is_dir():
        return []

    ver_re = re.compile(r"^python(\d+)\.(\d+)$")  # python3.13
    cfg_re = re.compile(r"^python(\d+)\.(\d+)-config$")  # python3.13-config
    pip_re = re.compile(r"^pip(\d+)\.(\d+)$")  # pip3.13

    def _highest(rx: re.Pattern[str]) -> str | None:
        best_name, best_key = None, None
        for entry in bin_dir.iterdir():
            m = rx.match(entry.name)
            if not m:
                continue
            key = (int(m.group(1)), int(m.group(2)))
            if best_key is None or key > best_key:
                best_key, best_name = key, entry.name
        return best_name

    py = _highest(ver_re)
    cfg = _highest(cfg_re)
    pip = _highest(pip_re)

    plan: list[tuple[str, str]] = []
    if py:
        plan += [("python3", py), ("python", py)]
    if cfg:
        plan.append(("python3-config", cfg))
    if pip:
        plan += [("pip3", pip), ("pip", pip)]

    created: list[Path] = []
    for name, target in plan:
        dst = bin_dir / name
        if dst.exists() or dst.is_symlink():
            continue  # respect an existing meta-package / user-provided command
        try:
            dst.symlink_to(target)  # relative link, resolves within bin/
        except OSError:
            continue
        created.append(dst)
    return created


def write_activate_scripts(
    prefix: Path,
    *,
    platform: str | None = None,
    prompt: str | None = None,
) -> list[Path]:
    """Emit activate scripts into *prefix*.

    The set of scripts written is chosen by *platform* — Windows
    prefixes get PowerShell + cmd, all others get POSIX shells.  A
    prefix is often used cross-platform (e.g. built on Linux, used
    from a WSL bash), so POSIX prefixes always receive the full set
    of POSIX shells (bash/zsh, fish, csh).

    *prompt* defaults to the prefix's basename.  Returns the list of
    files written.
    """
    prefix = Path(prefix).resolve()
    if prompt is None:
        prompt = prefix.name or "cvcpkg"
    if platform is None:
        from cvcpkg.platform import detect_platform

        platform = detect_platform()

    written: list[Path] = []

    if platform == "windows":
        scripts_dir = prefix / "Scripts"
        scripts_dir.mkdir(parents=True, exist_ok=True)
        ps1 = scripts_dir / "Activate.ps1"
        ps1.write_text(render_powershell(prefix, prompt=prompt), encoding="utf-8")
        written.append(ps1)
        bat = scripts_dir / "activate.bat"
        bat.write_text(render_cmd(prefix, prompt=prompt), encoding="utf-8")
        written.append(bat)
        deactivate_bat = scripts_dir / "cvcpkg_deactivate.bat"
        deactivate_bat.write_text(render_cmd_deactivate(), encoding="utf-8")
        written.append(deactivate_bat)
    else:
        bin_dir = prefix / "bin"
        bin_dir.mkdir(parents=True, exist_ok=True)
        # Surface the conventional python3/python/pip commands for install
        # trees whose python was staged as versioned-only binaries (so multiple
        # minor versions can coexist). Create-if-missing; never clobber.
        _ensure_python_aliases(bin_dir)
        bash_path = bin_dir / "activate"
        bash_path.write_text(
            render_bash(prefix, prompt=prompt, platform=platform), encoding="utf-8"
        )
        written.append(bash_path)
        fish_path = bin_dir / "activate.fish"
        fish_path.write_text(
            render_fish(prefix, prompt=prompt, platform=platform), encoding="utf-8"
        )
        written.append(fish_path)
        csh_path = bin_dir / "activate.csh"
        csh_path.write_text(render_csh(prefix, prompt=prompt, platform=platform), encoding="utf-8")
        written.append(csh_path)
        # POSIX activate scripts are sourced, but chmod +r is enough;
        # they don't need +x.  We keep 0644 to match Python venv.

    return written
