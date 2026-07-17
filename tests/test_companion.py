"""Tests for logbook/companion.py — starting the companion tide tool (§17).

Pure: no Tk, no display, and NO REAL PROCESS. Every spawn goes through the
injected seam, so CI never looks for `python3 -m moorwatch` and these pass on the
Windows dev box, where neither it nor ~/Apps/TSCTide exists.

Build order: §17 step 1.
Run: ``python -m unittest discover -s tests -t .``
"""

import subprocess
import tempfile
import unittest
from pathlib import Path

from logbook import companion


class _FakeProc:
    """Stands in for Popen. Alive until a returncode is set on it."""

    def __init__(self, returncode=None):
        self.returncode = returncode

    def poll(self):
        return self.returncode


class _Spy:
    """Records calls and hands back a proc — or raises what Popen would."""

    def __init__(self, proc=None, raises=None):
        self.calls = []
        self.proc = proc or _FakeProc()
        self.raises = raises

    def __call__(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        if self.raises is not None:
            raise self.raises
        return self.proc


class CompanionTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)

    def _companion(self, spy, directory=None):
        return companion.Companion("Moorwatch", companion.MOORWATCH_ARGV,
                                   directory or self.dir, spawn=spy)

    def test_press_runs_the_constant_command_in_the_configured_directory(self):
        spy = _Spy()
        text, ok = self._companion(spy).start()
        self.assertTrue(ok, text)
        (argv, kwargs), = spy.calls
        self.assertEqual(argv, list(companion.MOORWATCH_ARGV))
        self.assertEqual(kwargs["cwd"], str(self.dir))

    def test_spawn_is_detached_so_ctrl_c_on_the_log_does_not_kill_it(self):
        # Invisible until it fails, and it fails only on the boat: SYLog is
        # started from a terminal, and without this a Ctrl-C there reaches the
        # whole process group and takes Moorwatch with it.
        spy = _Spy()
        self._companion(spy).start()
        self.assertIs(spy.calls[0][1]["start_new_session"], True)

    def test_stderr_is_inherited_so_a_crash_reaches_the_journal(self):
        # DEVNULL here would make a crash-on-import indistinguishable from the
        # window having opened behind us.
        spy = _Spy()
        self._companion(spy).start()
        kwargs = spy.calls[0][1]
        self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)
        self.assertNotIn("stdout", kwargs)
        self.assertNotIn("stderr", kwargs)

    def test_the_directory_is_expanded(self):
        spy = _Spy()
        c = companion.Companion("Moorwatch", companion.MOORWATCH_ARGV,
                                "~/Apps/TSCTide", spawn=spy)
        self.assertNotIn("~", str(c.directory))
        self.assertEqual(c.directory, Path.home() / "Apps/TSCTide")

    def test_second_press_does_not_start_a_second_copy(self):
        spy = _Spy(_FakeProc(returncode=None))          # still running
        c = self._companion(spy)
        c.start()
        text, ok = c.start()
        self.assertEqual(len(spy.calls), 1)
        self.assertTrue(ok)
        self.assertIn("already running", text)

    def test_a_dead_copy_is_replaced_on_the_next_press(self):
        proc = _FakeProc(returncode=None)
        spy = _Spy(proc)
        c = self._companion(spy)
        c.start()
        proc.returncode = 0                              # skipper closed it
        self.assertFalse(c.running())                    # poll() reaps it
        c.start()
        self.assertEqual(len(spy.calls), 2)

    def test_running_is_false_before_anything_is_started(self):
        self.assertFalse(self._companion(_Spy()).running())

    def test_a_missing_directory_is_a_message_not_an_exception(self):
        c = self._companion(_Spy(), directory=self.dir / "nope")
        text, ok = c.start()
        self.assertFalse(ok)
        self.assertIn("nope", text)
        self.assertIn("not a directory", text)

    def test_a_missing_directory_does_not_reach_the_spawn(self):
        spy = _Spy()
        self._companion(spy, directory=self.dir / "nope").start()
        self.assertEqual(spy.calls, [])

    def test_oserror_from_the_spawn_is_a_message_not_a_traceback(self):
        # Parametrised because this is the bug the design review actually found:
        # a missing cwd raises NotADirectoryError on Windows and
        # FileNotFoundError on Linux, and a bad mode raises PermissionError.
        # Catching FileNotFoundError alone leaks a traceback out of a Tk callback,
        # onto a console the netbook does not have.
        for exc in (FileNotFoundError(2, "No such file or directory: 'python3'"),
                    NotADirectoryError(20, "Not a directory"),
                    PermissionError(13, "Permission denied")):
            with self.subTest(exc=type(exc).__name__):
                c = self._companion(_Spy(raises=exc))
                text, ok = c.start()
                self.assertFalse(ok)
                self.assertIn("did not start", text)

    def test_a_failed_start_leaves_no_handle_so_the_next_press_retries(self):
        c = self._companion(_Spy(raises=FileNotFoundError(2, "nope")))
        c.start()
        self.assertFalse(c.running())
        spy = _Spy()
        c.spawn = spy
        text, ok = c.start()
        self.assertTrue(ok, text)
        self.assertEqual(len(spy.calls), 1)

    def test_the_command_is_moorwatchs_documented_invocation(self):
        # Pinned because it is a constant and not config: if Moorwatch's CLI
        # changes, this line and MOORWATCH_ARGV change in the same commit (§17.2).
        self.assertEqual(companion.MOORWATCH_ARGV,
                         ("python3", "-m", "moorwatch", "--gui"))


if __name__ == "__main__":
    unittest.main()
