"""Run skia's tools/git-sync-deps one repository at a time, retrying each one.

usage: python3 sync_deps.py <skia-checkout>/tools/git-sync-deps

git-sync-deps starts one thread per DEPS entry (about 40 at once).  On the
fleet builder that ran skia, five of them failed with "fatal: remote transport
reported error" on every attempt, and the script only says "Thread failure
detected" -- not which repository failed.  Every pinned commit fetches fine one
at a time (checked 2026-10-15), so this loads the script as a module, swaps its
`multithread` for a serial loop with per-repository retries, and names any
repository that still fails.

Five DEPS entries are not synced at all.  The fleet builders star-01 and
rebota could not clone them from googlesource.com on any attempt
(2026-10-15/16), and this build never reads them: libavif and libjxl are
codecs behind skia_use_libavif / skia_use_libjxl_decode, vello sits behind
skia_enable_vello_shaders (all off by default, and build.sh's GN args leave
them so), oboe is Android-only audio, and unicodetools is Unicode's data
tooling.
"""

import importlib.machinery
import importlib.util
import os
import sys
import time

ATTEMPTS = 4
# third_party/externals/<name> entries this build does not use (see above).
UNUSED = {"libavif", "libjxl", "oboe", "unicodetools", "vello"}


def load(path):
    loader = importlib.machinery.SourceFileLoader("git_sync_deps", path)
    spec = importlib.util.spec_from_loader("git_sync_deps", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def serial(function, list_of_arg_lists):
    failed = []
    for args in list_of_arg_lists:
        repo, directory = args[1], args[3]
        if os.path.basename(os.path.normpath(directory)) in UNUSED:
            sys.stderr.write(f"skia: not syncing {directory} (unused by this build)\n")
            continue
        for attempt in range(1, ATTEMPTS + 1):
            try:
                function(*args)
                break
            except Exception as exc:  # subprocess.CalledProcessError and friends
                if attempt == ATTEMPTS:
                    failed.append(repo)
                    sys.stderr.write(
                        f"skia: {repo} -> {directory} failed {ATTEMPTS} times: {exc}\n"
                    )
                else:
                    delay = 10 * attempt
                    sys.stderr.write(
                        f"skia: {repo} failed (attempt {attempt}/{ATTEMPTS}): {exc}; "
                        f"retrying in {delay}s\n"
                    )
                    time.sleep(delay)
    if failed:
        raise SystemExit(f"skia: could not sync {len(failed)} dependencies: {', '.join(failed)}")


def main():
    module = load(sys.argv[1])
    module.multithread = serial
    return module.main(sys.argv[2:])


if __name__ == "__main__":
    sys.exit(main())
