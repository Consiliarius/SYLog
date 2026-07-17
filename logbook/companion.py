"""Companion apps this tool can start — currently just Moorwatch (§17).

This module starts another program and forgets it. It does not embed one, does
not talk to one, and does not report on one. That is deliberately the whole
module: §16.1 rejected showing the tide tool's readout inside this window and
prescribed the remedy in its own words — "It runs as its own tool alongside."
This is "alongside", spelled as a process.

**The line, for whoever extends this:** the moment anything is read back out of
the companion — a pipe, a status file, a "still running" lamp — §16.5 is broken
and this module has become the instrument §1.2 says the tool is not. Starting is
allowed. Listening is not.

No tkinter here, so the spawn is testable with no display and no real process.

Build order: §17 step 1.
Spec: §17.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

# A CONSTANT, not config. `python3 -m moorwatch --gui` is a fact about
# Moorwatch's CLI, and Moorwatch ships from the TSCTide repo alongside the tide
# tool this one already exports to — if the CLI changes, this line changes with
# it, and config cannot drift from something it does not hold. Config carries
# only the DIRECTORY, the part that genuinely varies per machine (§17.2).
#
# Rejected: the whole argv in config.json. It makes a logbook's config a
# run-anything surface, bought for nothing, and a typo in it is a dead button
# with no diagnosis — where a typo in a directory names itself in the error.
#
# `python3` and not sys.executable: the companion is a separate program with its
# own interpreter, and borrowing this one's would break the day either grows a
# virtualenv the other lacks. deployment.md already starts this tool the same way.
MOORWATCH_ARGV = ("python3", "-m", "moorwatch", "--gui")


class Companion:
    """A separate GUI app started from this one; at most one copy at a time."""

    def __init__(self, name: str, argv, directory, *, spawn=subprocess.Popen) -> None:
        self.name = name
        self.argv = list(argv)
        self.directory = Path(directory).expanduser()
        # The test seam: CI has no `python3 -m moorwatch` and must never look for
        # one. An instance attribute, not a module monkeypatch, so a test's stub
        # dies with its App instead of leaking into the next one.
        self.spawn = spawn
        self._proc = None

    def running(self) -> bool:
        """True while our copy is alive.

        ``poll()`` does double duty: it answers the question AND reaps the process
        once it has exited. A skipper opening and closing Moorwatch all afternoon
        would otherwise leave a queue of zombies parented to the log.
        """
        return self._proc is not None and self._proc.poll() is None

    def start(self) -> tuple[str, bool]:
        """Start it unless it is already up. Returns ``(message, ok)`` — the
        ``(text, ok)`` shape the backup status already uses.

        NEVER raises. A traceback out of a Tk callback goes to a console the
        netbook does not have, so the skipper would press the button and see
        nothing happen at all — the one outcome this whole design is against.
        """
        if self.running():
            return (f"{self.name} is already running — alt-tab to it.", True)
        if not self.directory.is_dir():
            # Named separately from the OSError below only because this message
            # can name the path as the thing at fault, which "No such file or
            # directory: 'python3'" cannot.
            return (f"{self.name} did not start: {self.directory} is not a directory",
                    False)
        try:
            self._proc = self.spawn(
                self.argv, cwd=str(self.directory),
                # Detached. SYLog is routinely started from a terminal and
                # Ctrl-C'd; without this, that Ctrl-C reaches the whole process
                # group and takes Moorwatch down with it — the exact opposite of
                # "its own tool alongside". Accepted and harmless on the Windows
                # dev box (verified), so no platform branch.
                start_new_session=True,
                # stdin only. stdout/stderr are INHERITED on purpose: under
                # autostart they land in the journal, where a failing Moorwatch
                # can be read with journalctl. Rejected DEVNULL — it makes a
                # crash-on-import silent, and silence is indistinguishable from
                # the window-opened-behind-us case.
                stdin=subprocess.DEVNULL,
            )
        except OSError as exc:
            # OSError, not FileNotFoundError: a missing cwd raises
            # NotADirectoryError on Windows and FileNotFoundError on Linux, and a
            # bad mode raises PermissionError. Measured, both spellings. To the
            # skipper they are one thing — it did not start, and here is why.
            self._proc = None
            return (f"{self.name} did not start: {exc}", False)
        return (f"{self.name} started in its own window — alt-tab to it.", True)
