#!/bin/sh
# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC
#
# Reclaim leaked cvcpkg build scratch trees WITHOUT deleting in-flight builds.
#
# This replaces the one-liner that used to be pasted into five places in
# .github/workflows/deploy-prod.yml:
#
#     find /tmp -maxdepth 1 -name 'cvcpkg-*' -type d -mmin +60 -exec rm -rf {} +
#
# That predicate is wrong, and not merely mistuned.  '-maxdepth 1 -type d
# -mmin' tests the TOP directory's OWN mtime, and a directory's mtime only
# advances when an entry is added or removed DIRECTLY inside it.  A build tree
# creates build/ and install/ in its first second and then writes tens of
# thousands of files several levels deeper, so the top directory's mtime
# freezes almost immediately and the tree looks stale after an hour no matter
# how hard it is working.  On 2026-08-04 the deploy-prod run at 10:45 UTC
# deleted /tmp/cvcpkg-haiku-image-b6wd0qrn 90 minutes into a build, orphaning
# six bfs_shell/build_haiku_image processes onto a deleted image file.
#
# Raising the threshold only moves the cliff.  llvm18 already declares
# timeout_seconds: 18000 (5 hours) in this tree, and haiku-image declares
# 14400 (4 hours); any build longer than the new number gets reaped
# mid-flight just the same.
#
# So this script asks four questions instead, cheapest first, and spares the
# tree the moment any of them says "alive":
#
#   1. Is the root directory's own mtime fresh?  cvcpkg's heartbeat watcher
#      (src/cvcpkg/heartbeat.py) utime()s the root once a minute for the life
#      of the build.  This is the ONLY signal that survives a cross-user
#      reaper: mkdtemp roots are 0700, so an unprivileged reaper cannot stat
#      anything INSIDE the tree, but /tmp is 1777 so it can always stat the
#      root.  One stat, no walk.
#   2. Is .cvcpkg-heartbeat fresh?  Same signal, explicit, and it also tells an
#      operator what was spared and why.
#   3. Does .cvcpkg-heartbeat name a process that is STILL ALIVE on this host?
#      Covers a build whose heartbeat thread wedged while the build itself
#      kept going.  Guarded against pid reuse by comparing the recorded comm
#      and boot id.
#   4. Was ANYTHING anywhere in the tree modified recently?  The fallback for
#      trees with no heartbeat: an older cvcpkg, or a foreign cvcpkg-* dir.
#      This is the full walk, so it runs last.
#
# A genuinely abandoned tree answers "no" to all four and is removed, which is
# the point -- builders do fill their disks, which is why disk-aware scheduling
# exists.  This script makes the reaper precise, not toothless.
#
# Portability: POSIX sh only.  It runs on Ubuntu (prod, linux builders, the
# CUDA box) and on FreeBSD/NetBSD builders, over ssh, as whatever user the
# workflow logs in as.  It uses only find/ps/sed/rm, avoids `stat` entirely
# (GNU -c vs BSD -f), and uses find's -mmin, which GNU, FreeBSD, NetBSD,
# OpenBSD and macOS all support.  It deliberately does NOT use lsof or fuser:
# neither is installed on the BSD builders, /proc is not mounted on FreeBSD,
# and reading another user's /proc/<pid>/cwd needs ptrace privileges on Linux.
#
# Usage:
#   reap-stale-build-dirs.sh [options] ROOT...
#
#   -a MINUTES   idle threshold; a tree with no sign of life for this long is
#                reaped (default 60)
#   -d DEPTH     candidates are the entries at exactly DEPTH below each ROOT
#                (default 1).  Use 2 for the fleet-supervisor layout, where
#                job dirs live in /var/lib/cvcpkg-builder/<server-slug>/.
#   -p PATTERN   candidate name glob (default 'cvcpkg-*')
#   -P MINUTES   how long a heartbeat's pid is still believed once the
#                heartbeat itself has gone stale (default 1440 = 24h); past
#                this a wedged-then-dead build is judged on mtime alone, so a
#                recycled pid cannot make a dead tree immortal
#   -n           dry run: report decisions, delete nothing
#   -q           only report reaped trees
#
# Exit status is 0 unless the arguments are unusable; a host with nothing to
# clean, an unreadable tree, or a racing deletion is not an error.

set -u

AGE_MIN=60
PID_TRUST_MIN=1440
DEPTH=1
PATTERN='cvcpkg-*'
DRY_RUN=0
QUIET=0

# Work roots and caches are conventionally named 'cvcpkg-builder' /
# 'cvcpkg-recipe-cache' and so match 'cvcpkg-*' themselves.  They are always
# older than the threshold, and deleting the work root leaves the builder
# unable to write its pidfile -- four builders went down that way the first
# time these steps reached a host they could log into.  Mirrors _NEVER_DELETE
# in src/cvcpkg/builder_gc.py.
NEVER_DELETE='cvcpkg-builder cvcpkg-recipe-cache'

HEARTBEAT_NAME='.cvcpkg-heartbeat'

usage() {
    # $0 is "sh" when the script is streamed over ssh (`sh -s -- ...`), so the
    # self-documenting path only works for a local invocation.
    if [ -r "$0" ]; then
        sed -n '/^# Usage:/,/^$/p' "$0" | sed 's/^# \{0,1\}//'
    else
        echo "usage: reap-stale-build-dirs.sh [-n] [-q] [-a MIN] [-P MIN] [-d DEPTH] [-p GLOB] ROOT..." >&2
    fi
    exit "${1:-0}"
}

while getopts 'a:d:p:P:nqh' opt; do
    case "$opt" in
        a) AGE_MIN=$OPTARG ;;
        P) PID_TRUST_MIN=$OPTARG ;;
        d) DEPTH=$OPTARG ;;
        p) PATTERN=$OPTARG ;;
        n) DRY_RUN=1 ;;
        q) QUIET=1 ;;
        h) usage 0 ;;
        *) usage 2 ;;
    esac
done
shift $((OPTIND - 1))

[ "$#" -gt 0 ] || usage 2

case "$AGE_MIN" in
    '' | *[!0-9]*) echo "reap: -a wants a whole number of minutes" >&2; exit 2 ;;
esac
case "$DEPTH" in
    '' | *[!0-9]*) echo "reap: -d wants a whole number" >&2; exit 2 ;;
esac
case "$PID_TRUST_MIN" in
    '' | *[!0-9]*) echo "reap: -P wants a whole number of minutes" >&2; exit 2 ;;
esac

HOSTNAME_NOW=$(hostname 2>/dev/null || echo '')
if [ -r /proc/sys/kernel/random/boot_id ]; then
    BOOT_NOW=$(cat /proc/sys/kernel/random/boot_id 2>/dev/null || echo '')
else
    BOOT_NOW=''
fi

say() { [ "$QUIET" -eq 1 ] || echo "$@"; }

# hb_field FILE KEY -- value of a key=value line, or empty.
hb_field() {
    sed -n "s/^$2=//p" "$1" 2>/dev/null | head -n 1
}

# fresh PATH -- true when PATH exists and was modified within AGE_MIN.
# -maxdepth 0 makes find test exactly this path and not descend.
fresh() {
    [ -e "$1" ] || return 1
    [ -n "$(find "$1" -maxdepth 0 -mmin -"$AGE_MIN" 2>/dev/null)" ]
}

# pid_alive PID -- true when the pid exists.  `ps -p` is POSIX and reports
# other users' processes, unlike `kill -0`, which cannot distinguish "exists
# but not mine" (EPERM) from "gone" (ESRCH) through the shell's exit status.
pid_alive() {
    ps -p "$1" >/dev/null 2>&1
}

# heartbeat_process_alive DIR -- true when DIR's heartbeat names a process that
# is still running HERE.  Requires the recorded host and boot id to match (a
# pid means nothing after a reboot or on another machine) and the recorded
# comm to still match (pid reuse).
heartbeat_process_alive() {
    hb="$1/$HEARTBEAT_NAME"
    [ -r "$hb" ] || return 1

    # A pid is only believed for so long.  Builders are long-lived and every
    # cvcpkg process is `python3`, so given enough weeks the pid space wraps
    # and a stranded heartbeat starts pointing at an unrelated live python --
    # which would make its tree immortal and refill the disk this step exists
    # to protect.  Past the window the tree is judged on mtime alone.
    fresh "$hb" || [ -n "$(find "$hb" -maxdepth 0 -mmin -"$PID_TRUST_MIN" 2>/dev/null)" ] || return 1

    hb_pid=$(hb_field "$hb" pid)
    case "$hb_pid" in
        '' | *[!0-9]*) return 1 ;;
    esac

    hb_host=$(hb_field "$hb" host)
    [ -z "$hb_host" ] || [ "$hb_host" = "$HOSTNAME_NOW" ] || return 1

    hb_boot=$(hb_field "$hb" boot)
    if [ -n "$hb_boot" ] && [ -n "$BOOT_NOW" ] && [ "$hb_boot" != "$BOOT_NOW" ]; then
        return 1  # host rebooted; that pid is somebody else now
    fi

    pid_alive "$hb_pid" || return 1

    hb_comm=$(hb_field "$hb" comm)
    if [ -n "$hb_comm" ]; then
        now_comm=$(ps -p "$hb_pid" -o comm= 2>/dev/null | sed 's:.*/::; s/^ *//; s/ *$//')
        [ -z "$now_comm" ] || [ "$now_comm" = "$hb_comm" ] || return 1
    fi
    return 0
}

# tree_recently_touched DIR -- true when anything anywhere under DIR was
# modified within AGE_MIN.  The expensive check, so it runs last.  `head -n 1`
# stops the walk at the first hit (find dies of SIGPIPE), so an active tree
# costs almost nothing and only a genuinely idle one is walked in full.
# Unreadable subtrees are skipped rather than treated as evidence of life;
# otherwise any other user's directory would be immortal and the disk would
# fill anyway.
tree_recently_touched() {
    [ -n "$(find "$1" -mmin -"$AGE_MIN" -print 2>/dev/null | head -n 1)" ]
}

# is_container DIR -- true when DIR is a work root holding other jobs rather
# than a job itself.  The fleet supervisor names each per-server work dir after
# the server, so cvcpkg.org's container is literally 'cvcpkg-org' and matches
# the glob; deleting it would take a live job with it.  Mirrors _candidates()
# in src/cvcpkg/builder_gc.py.
is_container() {
    [ -e "$1/cvcpkg-builder.pid" ] && return 0
    for kid in "$1"/cvcpkg-job-* "$1"/cvcpkg-prefix-* "$1"/cvcpkg-out-*; do
        [ -d "$kid" ] && return 0
    done
    return 1
}

reaped=0
spared=0

# Collect candidates into a file first.  Piping find into `while read` would
# put the loop in a subshell, where the counters below would be incremented and
# then thrown away -- and the summary is the only thing that tells an operator
# whether the reaper is doing anything.
CANDIDATES=$(mktemp "${TMPDIR:-/tmp}/cvcpkg-reap.XXXXXX") || exit 2
trap 'rm -f "$CANDIDATES"' EXIT HUP INT TERM

for root in "$@"; do
    [ -d "$root" ] || continue
    # -mindepth == -maxdepth == DEPTH: never let find test its own starting
    # point.  The work root matches 'cvcpkg-*' and is always older than the
    # threshold, so without this the sweep deletes the directory it exists to
    # clean out.
    find "$root" -mindepth "$DEPTH" -maxdepth "$DEPTH" -name "$PATTERN" -type d \
        -print 2>/dev/null >>"$CANDIDATES"
done

# Read the candidate list on fd 3, never fd 0.  This script is streamed to
# the BSD and CUDA builders over `ssh ... sh -s --`, so the shell is reading
# its own source from stdin; a `done <file` here would redirect the fd the
# shell is still parsing from.
while IFS= read -r dir <&3; do
    [ -d "$dir" ] || continue   # raced away between find and here
    base=${dir##*/}

    skip=0
    for keep in $NEVER_DELETE; do
        [ "$base" = "$keep" ] && skip=1 && break
    done
    if [ "$skip" -eq 1 ]; then
        say "keep  $dir (work root)"
        continue
    fi

    if is_container "$dir"; then
        say "keep  $dir (work root: holds job dirs)"
        continue
    fi

    if fresh "$dir"; then
        say "keep  $dir (root mtime fresh: build heartbeat)"
        spared=$((spared + 1))
        continue
    fi
    if fresh "$dir/$HEARTBEAT_NAME"; then
        say "keep  $dir (heartbeat fresh)"
        spared=$((spared + 1))
        continue
    fi
    if heartbeat_process_alive "$dir"; then
        say "keep  $dir (heartbeat pid $(hb_field "$dir/$HEARTBEAT_NAME" pid) still running)"
        spared=$((spared + 1))
        continue
    fi
    if tree_recently_touched "$dir"; then
        say "keep  $dir (modified within ${AGE_MIN}m)"
        spared=$((spared + 1))
        continue
    fi

    label=$(hb_field "$dir/$HEARTBEAT_NAME" label)
    [ -n "$label" ] && label=" [$label]"
    if [ "$DRY_RUN" -eq 1 ]; then
        echo "would reap $dir$label (idle >${AGE_MIN}m)"
    else
        echo "reap  $dir$label (idle >${AGE_MIN}m)"
        rm -rf "$dir" 2>/dev/null || true
    fi
    reaped=$((reaped + 1))
done 3<"$CANDIDATES"

say "reap: $reaped reclaimed, $spared spared (idle threshold ${AGE_MIN}m)"

# A host with nothing to clean, an unreadable tree, or a tree that raced away
# under us is not a failure; never let disk hygiene red a deploy.
exit 0
