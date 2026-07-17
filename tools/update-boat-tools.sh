#!/usr/bin/env bash
# Update SYLog and Moorwatch, then re-sync the mooring settings from TSCTide.
#
# Lives at ~/Apps/update-boat-tools.sh — BESIDE the two checkouts (~/Apps/SYLog
# and ~/Apps/TSCTide), never inside one. Bash reads a script lazily as it runs,
# so a `git pull` that rewrote this file mid-execution could make the shell run
# garbage. Keep it out of anything it pulls.
#
# Needs wifi. Everything here is safe to run without it — it will say what it
# could not do and change nothing.

set -uo pipefail    # NOT -e: every step reports, and one failure must not
                    # silently skip the rest. A failed SYLog pull should still
                    # let the mooring re-sync happen.

SYLOG_DIR="${SYLOG_DIR:-$HOME/Apps/SYLog}"
TSCTIDE_DIR="${TSCTIDE_DIR:-$HOME/Apps/TSCTide}"
MOORING_ID="${MOORING_ID:-64}"
NET_TIMEOUT=60      # a boat network that is "there" but dead must not hang this

fail=0

say()  { printf '\n=== %s\n' "$1"; }
ok()   { printf '  ok %s\n' "$1"; }
warn() { printf '  !! %s\n' "$1"; fail=1; }   # something did NOT happen
note() { printf '  -- %s\n' "$1"; }           # worth seeing; not a failure

# The distinction is load-bearing. A script that reports "PROBLEMS" when nothing
# failed is one you stop reading, and then it cannot tell you the thing that
# matters. Only a step that did not do its job sets `fail`.

pull() {
    local name="$1" dir="$2"
    say "$name — $dir"

    if [ ! -d "$dir/.git" ]; then
        warn "not a git checkout; skipped"
        return
    fi

    # Local edits are reported, never stashed or discarded. Both repos keep
    # their config.json gitignored, so a dirty tree here means real work —
    # and a script run from a desktop icon must not be able to eat it.
    if [ -n "$(git -C "$dir" status --porcelain)" ]; then
        note "uncommitted local changes, left alone:"
        git -C "$dir" status --short | sed 's/^/     /'
    fi

    local before
    before="$(git -C "$dir" rev-parse --short HEAD)"

    # --ff-only: if the branch has diverged, STOP rather than create a merge
    # commit unattended on a boat. Nothing here is worth a surprise merge.
    if timeout "$NET_TIMEOUT" git -C "$dir" pull --ff-only 2>&1 | sed 's/^/     /'; then
        local after
        after="$(git -C "$dir" rev-parse --short HEAD)"
        if [ "$before" = "$after" ]; then
            ok "already current ($after)"
        else
            ok "updated $before -> $after"
            git -C "$dir" log --oneline "$before..$after" | sed 's/^/     /'
        fi
    else
        # 124 is timeout(1)'s signal that the network hung rather than refused.
        [ "${PIPESTATUS[0]}" = "124" ] \
            && warn "timed out after ${NET_TIMEOUT}s — no usable network?" \
            || warn "pull failed (see above) — the old version still works"
    fi
}

pull "SYLog"    "$SYLOG_DIR"
pull "Moorwatch (TSCTide)" "$TSCTIDE_DIR"

# AFTER the pull, deliberately: this runs the sync code we just fetched, not the
# copy that was on disk when the script started.
say "Mooring $MOORING_ID — re-syncing settings from TSCTide"
if [ -d "$TSCTIDE_DIR" ]; then
    # --mooring is passed explicitly though moorwatch would default it from its
    # own config.json: that is the "compel" — it does not depend on the config
    # already being right, which is the case where a re-sync is most wanted.
    if (cd "$TSCTIDE_DIR" && timeout "$NET_TIMEOUT" \
            python3 -m moorwatch --sync --mooring "$MOORING_ID" 2>&1 | sed 's/^/     /'); then
        ok "mooring $MOORING_ID settings re-synced"
    else
        warn "sync failed — Moorwatch keeps its last-known settings"
    fi
else
    warn "$TSCTIDE_DIR not found; nothing to sync"
fi

printf '\n'
if [ "$fail" -eq 0 ]; then
    printf '=== All done. Restart SYLog to pick up any new version.\n'
else
    printf '=== Finished WITH PROBLEMS (above). Nothing was discarded.\n'
fi

# A desktop launcher closes its terminal the instant the command returns, taking
# every message above with it. This is the whole point of running it, so wait.
printf '\nPress Enter to close. '
read -r _
