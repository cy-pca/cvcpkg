"""Run skia's tools/git-sync-deps one repository at a time, retrying each one.

usage: python3 sync_deps.py <skia-checkout>/tools/git-sync-deps

git-sync-deps starts one thread per DEPS entry (about 40 at once).  On the
fleet builder that ran skia, five of them failed with "fatal: remote transport
reported error" on every attempt, and the script only says "Thread failure
detected" -- not which repository failed.  Every pinned commit fetches fine one
at a time (checked 2026-10-15), so this loads the script as a module, swaps its
`multithread` for a serial loop with per-repository retries, and names any
repository that still fails.
"""

import importlib.machinery
import importlib.util
import sys
import time

ATTEMPTS = 4


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
