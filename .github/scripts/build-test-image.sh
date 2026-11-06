#!/usr/bin/env bash
# Build one docker-compose.test.yml service image on the isolated `cvcpkg-ci`
# buildx builder, and judge success by whether the image landed in the daemon,
# not by the exit code of `docker compose build`.  Both failure modes are then
# covered:
#
#   * Building test+playwright+backend together exported three OCI tarballs
#     from the container-driver builder into the daemon store at once. The
#     export storm stalled -- Playwright's "sending tarball" ran for 2-4
#     minutes -- and compose returned a non-zero exit even though every image
#     reported "Built" and was imported ("importing to docker ... DONE"). That
#     spurious non-zero was read as fatal and failed the whole job on green
#     master and PRs #44/#45. One service per call caps peak export IO; the
#     presence check ignores the bogus exit code.
#
#   * A genuine failure (residual lock contention, ENOSPC on the shared
#     ramdisk, a broken layer) leaves no image, so `docker image inspect`
#     fails, we retry, and then fail for real with a clear message -- no
#     error-string grep to keep in sync with docker.
#
# Usage: build-test-image.sh SERVICE   (the image is cvcpkg-test-SERVICE)
set -uo pipefail

svc="$1"
img="cvcpkg-test-$svc"
for attempt in 1 2 3; do
  docker compose -f docker-compose.test.yml build --no-cache \
    --builder=cvcpkg-ci "$svc" 2>&1 | tee /tmp/build.log || true
  if docker image inspect "$img" >/dev/null 2>&1; then
    exit 0
  fi
  echo "::warning::$img absent after build attempt $attempt/3; retrying"
  sleep $((attempt * 20))
done
echo "::error::$img failed to build after 3 attempts"
exit 1
