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
  configured `backup_dir` on session close and on demand. It never `cp`s a live
  database and never overwrites an existing backup.

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

## Reference platform

Acer Aspire One 522 — AMD C-50 (2 × 1.0 GHz, 9 W), 4 GB RAM, Debian, OpenCPN
with vector charts, USB GPS dongle via gpsd. If it fails, another aging netbook
or a Raspberry Pi substitutes with no change to the tool.
