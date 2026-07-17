# Deployment notes

Target: a Debian-based, 64-bit Linux machine — amd64 today (an Acer Aspire One
netbook), arm64 (Raspberry Pi) a possible future substitute. These notes cover
the system-level setup the tool relies on but deliberately does **not** perform
itself.

## System packages

- **`python3-tk`** — Tkinter is a *separate* Debian package and is **not**
  installed by a netinstall. Without it the tool fails at startup with an
  unhelpful import error. Install it explicitly.
- **`gpsd`** (+ `gpsd-clients`) — holds the USB GPS dongle and serves clients on
  `localhost:2947`. OpenCPN is already one client; the logbook connects as
  another, so **no reconfiguration of a working chart-plotter connection is
  needed**. Confirm gpsd is serving with `cgps` or `cgps -s`.
- **`chrony`** — disciplines the system clock from GPS (see below).

## Clock — GPS time is authoritative

The tool prefers GPS time and falls back to system time, recording which it
used. It does **not** set the system clock itself.

- Configure **`chrony` + the gpsd SHM refclock driver** so the *system*
  disciplines its clock from GPS. This is system configuration, not application
  code.
- On each fix the tool computes `gps_time − system_time` and **warns once** if
  the offset exceeds `clock_offset_warn_sec`. It never silently rewrites stored
  timestamps.
- **A Raspberry Pi has no RTC.** On boot it restores an approximate time that
  may be days stale, and chrony cannot correct it until the first fix. **Fit a
  DS3231 (or similar) RTC module for any arm64 deployment.**

## Storage and backup

- The working database lives **outside** any synced folder (e.g.
  `~/logbook/logbook.db`). Cloud-sync clients copy files mid-write and can write
  a stale version back over live data — a well-known way to destroy a SQLite
  database. The backup directory (e.g. `~/OneDrive/logbook/`) receives **copies
  only**.
- The tool writes consistent, timestamped, integrity-checked snapshots to the
  configured `backup_dir` on session close and automatically on an interval
  (`backup.interval_min`, default 30) while a session is open. It never `cp`s a
  live database and never overwrites an existing backup.

## Moorwatch, the companion tide tool (§17)

The launcher shows a **Moorwatch ↗** button when `tools.moorwatch_dir` points at
an install; blank (the shipped default) means no button. Set it in **Settings →
Tools**, or in `config.json`:

```json
"tools": { "moorwatch_dir": "~/Apps/TSCTide" }
```

It is run as `python3 -m moorwatch --gui` from that directory — the command is a
constant in `logbook/companion.py`, not config, because it is a fact about
Moorwatch's CLI (§17.2). So the directory must be the one `python3 -m moorwatch`
resolves from, and Moorwatch's own dependencies must be installed for the system
`python3` this tool already uses.

- **It is started, not embedded.** SYLog spawns it detached and never talks to
  it. If it fails to start, the launcher's notice line says why.
- **Diagnosing a failure:** stderr is inherited deliberately, so under `systemd`
  autostart a crash-on-import is in the journal — `journalctl --user -u <unit>`.
  A button that appears to do nothing is more likely the window having opened
  behind SYLog; press it again and it will say "already running".
- The tool leaves fullscreen on a successful launch so the small window is
  visible; **F11** puts it back.

### Updating both tools, and re-syncing the mooring

`tools/update-boat-tools.sh` pulls SYLog and TSCTide, then re-syncs the mooring
settings from TSCTide. Run it when there is wifi; it is safe to run without.

**Copy it out of the repo before using it** — `~/Apps` is the suggested home:

```bash
mkdir -p ~/Apps && cp ~/SYLog/tools/update-boat-tools.sh ~/Apps/
chmod +x ~/Apps/update-boat-tools.sh
```

**It must not be run from inside either checkout.** Bash reads a script lazily as
it executes, so a `git pull` that rewrote the file mid-run could make the shell
execute garbage. The repo copy is the master; the `~/Apps` copy is the one that
runs. (So after an update that changes the script, copy it again — the script
says nothing about this, because a script that reports on its own staleness is a
script that has outgrown being one.)

Paths and mooring default to `~/SYLog`, `~/Apps/TSCTide` and mooring **64**, and
each is overridable:

```bash
SYLOG_DIR=~/src/SYLog MOORING_ID=12 ~/Apps/update-boat-tools.sh
```

What it does **not** do, deliberately: it never stashes, discards or merges. A
diverged branch stops with a message rather than making a merge commit
unattended, and local edits are reported and left alone. A failed pull leaves the
working version in place and says so.

A desktop launcher, if wanted — `~/.local/share/applications/update-boat-tools.desktop`:

```ini
[Desktop Entry]
Type=Application
Name=Update SYLog + Moorwatch
Comment=Pull both tools and re-sync the mooring settings (needs wifi)
Exec=x-terminal-emulator -e ~/Apps/update-boat-tools.sh
Terminal=false
Categories=Utility;
```

`Exec` runs it in a terminal on purpose: the output *is* the point, and the
script waits for Enter at the end so a launcher cannot close the window before
it is read.

**Git auth — check this first if the TSCTide pull fails with `Permission
denied (publickey)`.** The clone step above uses a **deploy key**, and GitHub
scopes a deploy key to *one repository*: the same key cannot be added to TSCTide
as well. If both repos are pulled on this machine, either give TSCTide its own
deploy key with a `~/.ssh/config` host alias, or use a single account-level SSH
key (**GitHub → Settings → SSH keys**) which reaches every repo the account can
see. A public repo cloned over HTTPS needs no key at all.

## rclone (optional off-machine copy)

The tool does **not** invoke rclone. A `systemd` timer or a NetworkManager hook
runs `rclone copy` when a network appears — this preserves the tool's "no
network dependency" property.

- Use **`rclone copy`, one-way, never `bisync`** — the cloud must never write a
  stale file back over local data.
- **Headless OAuth:** a netbook has no convenient browser flow. Run
  `rclone authorize` on another machine and paste the token across.
- **Refresh-token expiry:** Microsoft OAuth refresh tokens are *believed* to
  expire after roughly 90 days of inactivity — **this is not confirmed and
  should be verified rather than taken on trust.** A boat laid up over winter may
  return to a remote that no longer authenticates. This is **not** a data-loss
  risk (local backups remain valid) but it will look like one at the worst
  moment.

## Test deployment, step by step

> **Gate:** the reference netbook had a thermal fault (since corrected) and, before
> that, unexplained segfaults. A sustained OpenCPN soak test was to confirm they
> had stopped. **Confirm that soak test came back clean before trusting a run
> here** — otherwise a crash is ambiguous between a code bug and bad hardware.

**1. System packages.** `python3-tk` is *not* installed by a netinstall.

```bash
sudo apt install python3 python3-tk gpsd gpsd-clients git chrony
python3 --version          # 3.9 or newer (zoneinfo)
```

**2. Get the code.** The repository is private, so the clone needs
authentication. **`gh` is not in Debian's default packages** — the simplest route
on a deployment machine is an SSH key, which also makes `git pull` work for later
updates.

```bash
ssh-keygen -t ed25519 -C "netbook-sylog"    # accept the default path
cat ~/.ssh/id_ed25519.pub                   # copy this one line
```

Add that public key to GitHub from a browser on any machine:
**repo → Settings → Deploy keys → Add deploy key**. Read-only is enough to clone
and pull, and a deploy key is scoped to this one repository — which is what you
want on a boat machine. Then:

```bash
ssh -T git@github.com        # expect: "Hi ...! You've successfully authenticated"
git clone git@github.com:Consiliarius/SYLog.git
cd SYLog
```

If port 22 is blocked on the network, add to `~/.ssh/config`:

```
Host github.com
  HostName ssh.github.com
  Port 443
  User git
```

If you would rather use the GitHub CLI, it needs GitHub's own apt repository
(<https://github.com/cli/cli/blob/trunk/docs/install_linux.md>) and then
`gh auth login` with the device-code flow. It is not needed for any of this.

**3. Run the tests first.** They need nothing installed (stdlib `unittest`) and
prove the build is sound on this machine before any real data exists:

```bash
python3 -m unittest discover -s tests -t .            # expect: OK
python3 -m logbook --check                            # builds the window, exits
```

**4. Configure.** `config.json` is gitignored and machine-specific:

```bash
cp config.example.json config.json
$EDITOR config.json        # vessel name, sails, engine_hours_baseline, paths
```

**The working database must NOT live inside the backup directory** — the tool
refuses to start if it does (sync clients corrupt live SQLite databases). Keep
`paths.database` at something like `~/logbook/logbook.db` and `paths.backup_dir`
somewhere else entirely.

**5. Confirm gpsd is serving a fix.**

```bash
cgps -s                    # or: gpspipe -w -n 5
```

**6. Dry-run against the mock first** — gpsd already holds port 2947, so give the
mock its own port. This exercises the failure paths that real hardware will not
reproduce on demand:

```bash
python3 tools/mock_gpsd.py --scenario nominal --port 3947 &
python3 -m logbook --db ~/logbook/test.db --port 3947
# also try: --scenario no-fix | stale | 2d | drop
```

**7. Then run against the real gpsd** (defaults to `localhost:2947`). Use a
throwaway database for the test so the real logbook stays clean:

```bash
python3 -m logbook --db ~/logbook/test.db
```

**F11** toggles fullscreen, for alt-tabbing with a fullscreen OpenCPN.

**8. What to exercise.** Start a session (or Skip) · arm Auto-log · Depart ·
press Engine ▶ and ■ · record an Observation, Sail, Radio, Multi… · Arrive ·
End Session. Closing the session writes the four CSVs **and** a verified,
timestamped `.db` snapshot into `backup_dir`, and reports the outcome on the
launch screen. A snapshot is **also** written automatically every
`backup.interval_min` while a session is open — the status bar shows `backup
HH:MM` after each (or a red `backup FAILED HH:MM`); to see it quickly without
waiting, set `backup.interval_min` low (e.g. `1`) for the test. Check they are
there:

```bash
ls -l "$(python3 - <<'PY'
import json,os;print(os.path.expanduser(json.load(open("config.json"))["paths"]["backup_dir"]))
PY
)"
```

**9. When it is real.** Drop `--db`, so the tool uses `paths.database` from
config, and set up chrony + the gpsd SHM refclock (below) so the system clock is
disciplined from GPS.

## Reference platform

Acer Aspire One 522 — AMD C-50 (2 × 1.0 GHz, 9 W), 4 GB RAM, Debian, OpenCPN
with vector charts, USB GPS dongle via gpsd. If it fails, another aging netbook
or a Raspberry Pi substitutes with no change to the tool.
