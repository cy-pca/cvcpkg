# cvcpkg Builder on WSL2 (Debian)

Standalone provisioning guide for running a cvcpkg remote builder inside a
Debian WSL2 instance on a Windows host.

This produces a `linux/x86_64` builder (optionally with a `wasm` cross
target) that lives on a Windows box. It does **not** produce native
`windows/x86_64` artifacts — for those, run a native Windows builder instead.

The builder is the same three moving parts as every other Linux builder:

1. A CMake + Ninja + C/C++ toolchain.
2. `cvcpkg` on `PATH`, running `cvcpkg builder run …`.
3. Kept alive by **systemd** and reachable over **SSH** so your deploy
   automation can `git pull` + `pip install` + restart it.

WSL2 can do all three, but two things need extra plumbing that a normal VM
does not: **headless start across Windows reboots** and **LAN-reachable
SSH** (WSL2 sits behind a NAT with its own IP).

---

## 1. Install the distro

```powershell
wsl --install -d Debian
wsl --set-version Debian 2      # ensure WSL2 (real kernel), not WSL1
```

## 2. Enable systemd + configure the mount

Inside the Debian shell, create `/etc/wsl.conf`:

```ini
[boot]
systemd=true

[automount]
enabled=true
options="metadata,uid=1000,gid=1000,umask=022"   # Linux perms on Windows mounts

[network]
hostname=cvcpkg-wsl-01
```

Then from PowerShell:

```powershell
wsl --shutdown
```

Reopen the distro and verify: `systemctl is-system-running` should print
`running` (or `degraded`, which is fine). The `metadata` automount option
matters — without it every file under `/mnt/c` is `0777` root-owned, which
breaks anything that checks file modes.

> **`uid`/`gid` must match the builder user.** The `uid=1000,gid=1000` above
> makes the Windows mounts owned by uid 1000 — the default *first* WSL user.
> If the builder runs as a different account (e.g. a `tfx` service user
> created later, see §3) whose uid is **not** 1000, it won't own `/mnt/c` and
> writes to the Windows mount will fail. Check with `id -u tfx` and set
> `uid`/`gid` in `[automount]` to that user's ids (then `wsl --shutdown` and
> reopen). Simplest is to make the builder user the default WSL user so it
> gets uid 1000 and this just lines up.

## 3. Toolchain + cvcpkg

Prefer cvcpkg for anything it has a recipe for; use apt only for the
**bootstrap** — the handful of things cvcpkg itself needs in order to build
(you can't compile a C toolchain out of a package manager that needs one).

**Bootstrap (apt) — the minimum cvcpkg needs to run and build:**

```bash
sudo apt-get update
sudo apt-get install -y --no-install-recommends \
  build-essential \
  python3 python3-pip python3-venv pipx \
  git curl ca-certificates \
  pkg-config patchelf

pipx ensurepath
# [builder] is not optional for this host: this box's whole job is `cvcpkg
# builder run`, which talks to the server over HTTP (httpx).  A bare
# `pipx install cvcpkg` gets the core client (click + PyYAML) and the agent
# refuses to start.  [progress] just adds download progress bars.
pipx install 'cvcpkg[builder,progress]'
# or, for dev against a checkout:  cd libcvc-deps && pip install -e '.[builder,progress]'
```

| Bootstrap package | Why it can't come from cvcpkg |
|-------------------|-------------------------------|
| `build-essential` | The `gcc`/`g++`/`make` that cvcpkg *drives* to build everything else. |
| `python3*`, `pipx` | Run cvcpkg itself. |
| `git`, `curl`, `ca-certificates` | Fetch cvcpkg and recipe sources. |
| `pkg-config`, `patchelf` | Host build tools cvcpkg invokes during each recipe build. |

**Everything else comes from cvcpkg** — these all have recipes in
`libcvc-deps/recipes/`, so build them into a prefix rather than pulling the
apt equivalents:

```bash
# One prefix holds every cvcpkg-provided tool/lib for this builder.
export CVC_PREFIX="$HOME/cvcpkg-tools"

# Build tools + the SSH/crypto stack this builder needs, into that prefix.
# Prebuilt bundles come from the catalog; add --local to build from the
# local recipes/ instead (needed until openssh is published to the catalog).
cvcpkg install --prefix "$CVC_PREFIX" --local \
    cmake ninja \
    openssl zlib \
    openssh

# Put the prefix on PATH / CMAKE_PREFIX_PATH for this shell (§6 sets the same
# in the systemd units):
export PATH="$CVC_PREFIX/bin:$CVC_PREFIX/sbin:$PATH"
export CMAKE_PREFIX_PATH="$CVC_PREFIX${CMAKE_PREFIX_PATH:+:$CMAKE_PREFIX_PATH}"
```

> `cvcpkg install` names map to recipes: `cmake`, `ninja`, `openssl`, `zlib`,
> `openssh` (plus their deps — `nasm`, `perl`, `pkg-config` — resolve
> automatically). Validate any recipe you touch with
> `cvcpkg validate recipes/<name>`. If a recipe you need is missing, add it
> under `recipes/` rather than reaching for apt.

### OpenSSL and OpenSSH from cvcpkg

- **`openssl`** (recipe) builds `libssl`/`libcrypto` + headers into the
  prefix — this is what the **library we add later** links against, via
  `CMAKE_PREFIX_PATH` (no `libssl-dev` needed). It also drops the `openssl`
  CLI in `$CVC_PREFIX/bin`.
- **`openssh`** (recipe) provides both the client — `ssh`, `scp`, `sftp`,
  `ssh-keygen`, `ssh-agent`, `ssh-add`, `ssh-keyscan` (outbound git-over-SSH,
  remote fetches, host-key generation) — and the server — `sshd` (+
  `sshd-session`, `sshd-auth`, `sftp-server`), the daemon your deploy
  automation connects to (§4). Built against the `openssl` + `zlib` recipes.

> **Running the cvcpkg-built `sshd` as a service.** cvcpkg installs the
> binaries into a prefix; it does **not** do the system integration apt's
> `openssh-server` does. Do that once, at deploy time:
> ```bash
> sudo useradd -r -s /usr/sbin/nologin -d /var/empty sshd 2>/dev/null || true
> sudo install -d -m 0755 -o root -g root /var/empty          # privsep dir
> sudo install -d -m 0755 /etc/ssh
> sudo cp "$CVC_PREFIX/etc/ssh/sshd_config.sample" /etc/ssh/sshd_config
> # host keys (config below references these paths explicitly):
> sudo ssh-keygen -q -t ed25519 -N '' -f /etc/ssh/ssh_host_ed25519_key
> sudo ssh-keygen -q -t rsa -b 4096 -N '' -f /etc/ssh/ssh_host_rsa_key
> printf 'HostKey /etc/ssh/ssh_host_ed25519_key\nHostKey /etc/ssh/ssh_host_rsa_key\n' \
>   | sudo tee -a /etc/ssh/sshd_config >/dev/null
> ```
> Then run it from a systemd unit with an explicit config path (the recipe
> compiles `sysconfdir` into the prefix, so always pass `-f`):
> `ExecStart=$CVC_PREFIX/sbin/sshd -D -e -f /etc/ssh/sshd_config`.
> If you'd rather not run infra-critical sshd from a prefix, that one service
> is the reasonable place to fall back to apt's `openssh-server`; everything
> else stays on cvcpkg.

Version bar (from BUILDING.md): GCC ≥ 13, CMake ≥ 3.16 **and < 4.x** (Boost
1.86 CMake compat), Ninja ≥ 1.10, Python ≥ 3.10. cvcpkg's `cmake` recipe is
already pinned within this range, which is the other reason to prefer it over
whatever apt ships.

| Debian release | GCC | CMake | Notes |
|----------------|-----|-------|-------|
| 13 (trixie)    | 14  | 3.31  | Clears every bar out of the box. |
| 12 (bookworm)  | 12  | 3.25  | GCC 12 < 13 — pull `gcc-13` from bookworm-backports if a recipe needs it. |

Both releases are safely under the CMake 4.x ceiling.

**Build user.** The fleet convention is a `tfx` service user (the systemd unit
in §6 uses `User=tfx` and `/home/tfx/.local/bin/cvcpkg`). Set the distro's
default WSL user to `tfx` at first launch, or create it and run the toolchain
install / `pipx install` as that user so `cvcpkg` lands on its `PATH`. If you
keep a different username, change `User=` and the `ExecStart`/`PATH` in the
unit to match.

**Sanity check** before registering — with `eval "$(cvcpkg env)"` active, the
cvcpkg-provided tools should be the ones on `PATH`:

```bash
gcc --version && python3 --version && cvcpkg --version   # bootstrap (apt)
cmake --version && ninja --version                       # from cvcpkg prefix
command -v ssh sshd && ssh -V                             # from openssh recipe
openssl version                                          # cvcpkg openssl CLI
```

## 4. SSH server + client

WSL2 port 22 is **not** the host's port 22 — the distro is NAT'd with its own
IP. Pick one of three ways to make the deploy workflow able to reach sshd.
**Option C is usually the best fit for WSL**: the builder dials *out* to a
relay, so it works through NAT with no host-side port-forward and no
dependence on the ever-changing WSL IP.

**Option A — mirrored networking (simplest, Windows 11 22H2+).**
In `%UserProfile%\.wslconfig` on the Windows side:

```ini
[wsl2]
networkingMode=mirrored
```

`wsl --shutdown`, reopen. The distro now shares the host IP; `ssh tfx@<host>`
reaches sshd directly on port 22.

**Option B — host port-forward (older Windows).**
The WSL2 IP changes on every boot, so resolve it dynamically. Put this in the
boot task (step 6) so it re-runs each start:

```powershell
$ip = (wsl hostname -I).Trim().Split()[0]
netsh interface portproxy delete v4tov4 listenport=2222 listenaddress=0.0.0.0 2>$null
netsh interface portproxy add v4tov4 listenport=2222 listenaddress=0.0.0.0 connectport=22 connectaddress=$ip
New-NetFirewallRule -DisplayName "WSL sshd" -Direction Inbound -LocalPort 2222 -Protocol TCP -Action Allow -ErrorAction SilentlyContinue
```

Register the builder in the deploy target list on port 2222.

**Option C — reverse SSH tunnel to a LAN relay (recommended for WSL).**
The builder polls a reachable SSH host on the remote LAN (the *relay*) and,
once it can connect, holds open a reverse tunnel (`ssh -R`) that publishes its
local sshd on a port of the relay. Users/CI on the relay's LAN then reach the
builder by connecting to that port on the relay — no inbound path into WSL is
needed, and the outbound dial survives NAT and WSL IP churn.

```
   remote LAN user ──▶ relay.lan:2222 ──[reverse tunnel]──▶ WSL builder localhost:22
```

*Relay-side prerequisite:* to let LAN peers (not just the relay itself) use the
forwarded port, the relay's `/etc/ssh/sshd_config` must allow a non-loopback
bind — set `GatewayPorts clientspecified` (or `yes`) and reload sshd.
Otherwise the `-R` bind is loopback-only and reachable just from the relay.

*Config lives in one env file* so the same script and unit work unchanged
across every builder — only this file differs per host. Create
`/etc/cvcpkg/tunnel.env` (root-owned, `chmod 600`):

```ini
# /etc/cvcpkg/tunnel.env — reverse-tunnel config for this builder
CVCPKG_RELAY_HOST=relay.lan       # reachable SSH host on the remote LAN
CVCPKG_RELAY_USER=tfx
CVCPKG_RELAY_PORT=22              # the relay's sshd port
CVCPKG_REMOTE_BIND=0.0.0.0:2222  # where users connect ON the relay
CVCPKG_LOCAL_SSHD=localhost:22   # this WSL instance's sshd
CVCPKG_POLL_SECS=5
```

*Shortest form — `autossh`* (`sudo apt-get install -y autossh`), which does the
poll-and-reconnect for you. For a quick manual test, source the env file first:

```bash
set -a; . /etc/cvcpkg/tunnel.env; set +a
autossh -M 0 -f -NT \
  -o ServerAliveInterval=30 -o ServerAliveCountMax=3 \
  -o ExitOnForwardFailure=yes -o StrictHostKeyChecking=accept-new \
  -p "$CVCPKG_RELAY_PORT" \
  -R "$CVCPKG_REMOTE_BIND:$CVCPKG_LOCAL_SSHD" \
  "$CVCPKG_RELAY_USER@$CVCPKG_RELAY_HOST"
```

*Dependency-free equivalent* — a short poller that waits for the relay, then
holds the tunnel and reconnects if it drops. Save as
`/home/tfx/bin/cvcpkg-tunnel.py`:

```python
#!/usr/bin/env python3
"""Poll a LAN relay's sshd; while reachable, hold a reverse tunnel that
publishes this WSL builder's local sshd on the relay for remote-LAN users.

Config comes from the environment (see /etc/cvcpkg/tunnel.env); the defaults
below only apply when a var is unset, so the same script ships to every host.
"""
import os, socket, subprocess, time

RELAY_HOST = os.environ.get("CVCPKG_RELAY_HOST", "relay.lan")
RELAY_USER = os.environ.get("CVCPKG_RELAY_USER", "tfx")
RELAY_PORT = int(os.environ.get("CVCPKG_RELAY_PORT", "22"))
REMOTE_BIND = os.environ.get("CVCPKG_REMOTE_BIND", "0.0.0.0:2222")
LOCAL_SSHD = os.environ.get("CVCPKG_LOCAL_SSHD", "localhost:22")
POLL_SECS = int(os.environ.get("CVCPKG_POLL_SECS", "5"))

def reachable(host, port, timeout=3):
    try:
        with socket.create_connection((host, port), timeout):
            return True
    except OSError:
        return False

while True:
    if not reachable(RELAY_HOST, RELAY_PORT):
        time.sleep(POLL_SECS)
        continue
    # -N: no shell, -T: no tty; keepalives tear down dead tunnels;
    # ExitOnForwardFailure so a stale bind can't leave us "up" but useless.
    subprocess.call([
        "ssh", "-NT",
        "-o", "ServerAliveInterval=30", "-o", "ServerAliveCountMax=3",
        "-o", "ExitOnForwardFailure=yes", "-o", "StrictHostKeyChecking=accept-new",
        "-R", f"{REMOTE_BIND}:{LOCAL_SSHD}",
        "-p", str(RELAY_PORT), f"{RELAY_USER}@{RELAY_HOST}",
    ])
    time.sleep(POLL_SECS)   # ssh returned => tunnel dropped; re-poll
```

Either way, the builder needs **key-based** auth to the relay (so the loop is
non-interactive): `ssh-keygen -t ed25519`, then append the pubkey to the
relay's `~tfx/.ssh/authorized_keys`. Users then reach the builder with:

```bash
ssh -p 2222 tfx@relay.lan     # lands on the WSL builder's sshd
```

Run the tunnel under systemd so it starts with the distro and restarts on
drop (companion to the builder unit in §6):

```ini
# /etc/systemd/system/cvcpkg-tunnel.service
[Unit]
Description=cvcpkg WSL reverse SSH tunnel to LAN relay
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=tfx
EnvironmentFile=/etc/cvcpkg/tunnel.env
ExecStart=/usr/bin/python3 /home/tfx/bin/cvcpkg-tunnel.py
# or, with autossh (systemd expands ${VAR} from EnvironmentFile):
# ExecStart=/usr/bin/autossh -M 0 -NT \
#   -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -o ExitOnForwardFailure=yes \
#   -p ${CVCPKG_RELAY_PORT} \
#   -R ${CVCPKG_REMOTE_BIND}:${CVCPKG_LOCAL_SSHD} \
#   ${CVCPKG_RELAY_USER}@${CVCPKG_RELAY_HOST}
Restart=always
RestartSec=15

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now cvcpkg-tunnel
```

Inside the distro, generate host keys, enable sshd, install the deploy key:

```bash
sudo ssh-keygen -A
sudo systemctl enable --now ssh
mkdir -p ~/.ssh && chmod 700 ~/.ssh
# append the deploy workflow's public key to ~/.ssh/authorized_keys
chmod 600 ~/.ssh/authorized_keys
```

## 5. Mount a Windows directory

WSL auto-mounts fixed drives under `/mnt` already, so the Windows user's home
directory (`C:\Users\<you>`) is reachable at `/mnt/c/Users/<you>` — with Linux
perms thanks to the `metadata` option. Rather than hardcode the username,
resolve the running Windows user's profile path dynamically. The `wslu`
package provides `wslvar`/`wslpath` helpers for exactly this:

```bash
sudo apt-get install -y wslu
WINHOME="$(wslpath "$(wslvar USERPROFILE)")"   # => /mnt/c/Users/<you>
echo "$WINHOME"

# Bind it to a stable path for the builder to read/write:
sudo mkdir -p /srv/winhome
sudo mount --bind "$WINHOME" /srv/winhome
```

Without `wslu`, ask Windows for the path directly (strip the trailing CR):

```bash
WINHOME="$(wslpath "$(cmd.exe /C 'echo %USERPROFILE%' 2>/dev/null | tr -d '\r')")"
```

To mount just a subdirectory of the user profile (e.g. a `cvcpkg` folder in
the Windows home), point at it explicitly:

```bash
sudo mount --bind "$WINHOME/cvcpkg" /srv/winhome
# or an explicit drvfs mount of a Windows path:
# sudo mount -t drvfs 'C:\Users\<you>\cvcpkg' /srv/winhome -o metadata,uid=1000,gid=1000
```

To make the mount survive a distro restart, add it to `/etc/fstab` (systemd
honors fstab under WSL). fstab can't run command substitution, so use the
resolved literal path — the Windows username is stable per host:

```
C:\Users\<you>  /srv/winhome  drvfs  metadata,uid=1000,gid=1000,umask=022  0  0
```

> ⚠️ **Do not put `--work-dir` on the Windows mount.** `/mnt/c` is 9P/DrvFs:
> slow, and it does not honor Unix permissions/symlinks the way builds expect
> — CMake/Ninja and `patchelf` will crawl or misbehave. Keep `--work-dir` on
> native ext4 (`/tmp/cvcpkg-builder`) and use the Windows mount only to hand
> finished artifacts back to Windows.

## 6. Persistence (two layers)

**Inside the distro** — a normal systemd unit, identical to any Linux builder:

```ini
# /etc/systemd/system/cvcpkg-builder.service
[Unit]
Description=cvcpkg builder agent
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=tfx
Environment=PATH=/home/tfx/.local/bin:/usr/local/bin:/usr/bin:/bin
Environment=CVCPKG_SERVER_URL=https://cvcpkg.org
ExecStart=/home/tfx/.local/bin/cvcpkg builder run \
    --server ${CVCPKG_SERVER_URL} \
    --token cvctok_<builder-token> \
    --name cvcpkg-wsl-01 \
    --max-jobs 4 \
    --work-dir /tmp/cvcpkg-builder \
    --cross-platform wasm
Restart=always
RestartSec=30

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now cvcpkg-builder
journalctl -u cvcpkg-builder -f
```

(Drop `--cross-platform wasm` if this builder should not take wasm jobs.)

**On the Windows side** — WSL2 does not boot with Windows, and it shuts the
distro down when the last process exits. A Scheduled Task at boot starts WSL
(which brings up systemd → sshd → `cvcpkg-builder`; systemd keeps them alive):

```powershell
schtasks /create /tn "wsl-cvcpkg-boot" /sc onstart /ru SYSTEM /rl highest ^
  /tr "wsl -d Debian -u root -e /bin/true"
```

Running as `SYSTEM` (`/RU SYSTEM`) lets the task start without an interactive
login. If you used port-forward Option B, fold the
`netsh portproxy` refresh into a small script and point the task at that
instead of `/bin/true`.

## 7. Register + verify

Use a **publisher**-role token for the builder (create one with
`cvcpkg-server token create --role publisher`). Then confirm the builder
appears in the fleet:

```bash
curl -H "Authorization: Bearer $TOKEN" "$CVCPKG_SERVER_URL/v1/builders" | python3 -m json.tool
cvcpkg builds monitor --server "$CVCPKG_SERVER_URL" --token $TOKEN
```

If you run deploy automation that manages builders over SSH, add this
builder's SSH target (host + port + user) to its builder list so it gets
pulled/reinstalled/restarted with the rest.

---

## Cleanup & reclaiming disk

WSL2 stores the distro in a virtual disk (`ext4.vhdx`). Two distinct
problems: **junk inside the distro**, and the **vhdx never shrinking on its
own** even after you delete files.

### Inside the distro — free space

```bash
# cvcpkg build scratch + cache (safe; rebuilt on demand)
rm -rf /tmp/cvcpkg-builder/*
rm -rf ~/.cache/cvcpkg

# apt caches
sudo apt-get clean
sudo apt-get autoremove --purge -y

# old journald logs (keep last 2 days)
sudo journalctl --vacuum-time=2d

# see what is using space
sudo du -h -d1 / 2>/dev/null | sort -h | tail -20
```

The cvcpkg cache under `~/.cache/cvcpkg` is the usual culprit on a busy
builder; clear it periodically if the instance runs tight on disk.

### Shrink the vhdx (reclaim space back to Windows)

Deleting files inside the distro frees space **for the distro** but does not
give it back to the host — the `.vhdx` only grows. To compact it, shut WSL
down and run `Optimize-VHD` (Hyper-V) or `diskpart`:

```powershell
wsl --shutdown

# Find the vhdx path (per-distro, under the package dir):
#   %LOCALAPPDATA%\Packages\TheDebianProject...\LocalState\ext4.vhdx

# Option A — Hyper-V module (Windows Pro/Enterprise):
Optimize-VHD -Path "$env:LOCALAPPDATA\Packages\<DebianPkg>\LocalState\ext4.vhdx" -Mode Full

# Option B — diskpart (any Windows edition):
diskpart
#   select vdisk file="C:\Users\<you>\AppData\Local\Packages\<DebianPkg>\LocalState\ext4.vhdx"
#   attach vdisk readonly
#   compact vdisk
#   detach vdisk
#   exit
```

On recent WSL you can instead enable automatic reclaim in `%UserProfile%\.wslconfig`:

```ini
[experimental]
autoMemoryReclaim=gradual
sparseVhd=true            # new distros; keeps the vhdx sparse so it self-shrinks
```

`sparseVhd` only applies to newly created disks. To convert an existing one:
`wsl --manage Debian --set-sparse true` (after `wsl --shutdown`).

### Nuke and re-provision

If the instance is beyond saving, unregister it — this deletes the vhdx and
all its state, fully reclaiming the disk — then rebuild from step 1:

```powershell
wsl --shutdown
wsl --unregister Debian     # DESTRUCTIVE: wipes the distro and its vhdx
wsl --install -d Debian
```

For a repeatable rebuild, export a clean baseline once and re-import instead
of re-running the whole setup:

```powershell
wsl --export Debian D:\wsl\cvcpkg-debian-baseline.tar
# later:
wsl --import cvcpkg-wsl D:\wsl\cvcpkg-wsl D:\wsl\cvcpkg-debian-baseline.tar
```

---

## Quick reference

| Task | Command |
|------|---------|
| Builder logs | `journalctl -u cvcpkg-builder -f` |
| Restart builder | `sudo systemctl restart cvcpkg-builder` |
| Restart sshd | `sudo systemctl restart ssh` |
| WSL IP (Option B) | `wsl hostname -I` |
| Shut WSL down | `wsl --shutdown` (from Windows) |
| Free build cache | `rm -rf ~/.cache/cvcpkg /tmp/cvcpkg-builder/*` |
| Compact vhdx | `Optimize-VHD -Path <ext4.vhdx> -Mode Full` (after `wsl --shutdown`) |
| Destroy distro | `wsl --unregister Debian` |
