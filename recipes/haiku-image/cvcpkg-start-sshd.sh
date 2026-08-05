#!/bin/sh
# Installed to /boot/system/settings/ssh/cvcpkg-start-sshd.sh and started by
# the launch_daemon SYSTEM context (see the launch-sshd job file).
#
# Generates host keys if missing, then execs sshd in the foreground so the
# launch_daemon can supervise it as a service.
exec >> /boot/system/settings/ssh/cvcpkg-sshd.log 2>&1
echo "=== start-sshd $(date) ==="
ssh-keygen -A
echo "ssh-keygen -A exit=$?"
exec /bin/sshd -D -e
