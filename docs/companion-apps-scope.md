# Vessel Logbook Tool — §17 Companion apps: starting Moorwatch

**Status:** §17 is **built**. **Done:** `logbook/companion.py` (the `Companion`
spawn, one copy, detached); `tools.moorwatch_dir` in config and in the Settings
editor; the `Moorwatch ↗` button in the launch grid's sixth cell; the `⌂` on the
status bar, making the launcher reachable from any view; and the round-trip
tests that pin the invariant the whole feature rests on.
**Remaining:** nothing in §17.
**Date:** 17 July 2026

Extends `logbook-scope.md`. Numbered §17 so it slots in without renumbering the
canonical doc; its decisions fold into §13 when adopted. Deliberately **not** a
§16.6: §16 is closed and dated, and its subject is a column, a form field, a
renderer clause and an export file — *data*. This is process spawning and view
navigation — *architecture*. The addenda split by subject, not by which other
program is mentioned.

---

## 17.1 Purpose — and why this does not overturn §16.5

TSCTide has a spin-off, **Moorwatch**, which shows in a small Tk window whether
there is enough water on the mooring to arrive or depart safely. It is wanted at
exactly the moment this tool is already open — at the mooring, deciding whether
to go — and reaching it meant leaving the app for a terminal.

**Read §16.5's row precisely**, because the next reader arrives at §17 holding it:

> The tide tool's countdown is **not** shown in this app; the traffic runs outward.

It forbids **showing the readout**. A predicted depth is an inference (§4.1) and
an always-on readout of one is an instrument (§1.2). And §16.1 does not merely
forbid — it **prescribes**, in its own words:

> It runs as its own tool alongside.

A button that starts a separate process **displays nothing, infers nothing,
imports nothing and stores nothing.** It is §16.1's own sentence, implemented.
The traffic still runs outward: SYLog → Moorwatch is a *process start*, not a
data import. **§16.5 is upheld, not overturned.**

**The line, stated so it cannot be eroded later:**

> **SYLog starts Moorwatch and forgets it. It never displays Moorwatch's state —
> not its countdown, not its depth, not even whether it is running.**

That is why the button is a plain button and not a `▶`/`■` toggle like Engine and
Auto-log: a running/stopped readout of another tool inside this window is the
instrument §1.2 says this is not. **The line is not "no countdown". The line is
no readout of the other tool at all** — and it is a code-review test, not a vibe.
`companion.py`'s header states it, and
`test_the_button_never_reports_moorwatch_state` enforces it.

## 17.2 The command is a constant; only the directory is configured

`companion.MOORWATCH_ARGV = ("python3", "-m", "moorwatch", "--gui")` lives in
code. Only `tools.moorwatch_dir` is configuration.

- The invocation is a fact about **Moorwatch's CLI**, not a preference of this
  boat. Moorwatch ships from the TSCTide repo this tool already exports to, so if
  the CLI changes, that constant changes with it — and config cannot drift from
  something it does not hold. This is §16.2's "one source of truth" argument,
  applied to a command instead of a datum.
- **Rejected: the whole argv in config.json.** It makes a logbook's config a
  run-anything surface — a real scope widening bought for nothing today — and a
  typo in an argv text box is a dead button with no diagnosis, where a typo in a
  directory names itself in the error message.
- **`python3`, not `sys.executable`.** The companion is a separate program with
  its own interpreter; borrowing this one's would break the day either grows a
  virtualenv the other lacks. `deployment.md` already starts this tool the same
  way. If a venv is ever needed, the answer is an additive override key (the
  `export.html` pattern), not opening that door now.

## 17.3 Config — `tools.moorwatch_dir`, and why not `paths.*`

Optional, absent by default, **not** in `_REQUIRED`. `config.example.json` ships
it **blank** — unlike `paths.database`, which ships a real default because the
tool cannot start without it. Optional keys ship absent-shaped (`ssr`,
`callsign`, `mmsi` all ship `""`), so a fresh install — including every Windows
dev box, which copies the example on first run — gets **no button**, not a broken
one.

**Under `tools`, not `paths`.** `paths` is the two locations the tool cannot
start without, and the Settings editor leaves the section out to keep invariant
11 — database never inside the backup directory — away from a text box
(settings.py:30-33). This key carries neither property.

**So it *is* in the Settings editor**, under a new `Tools` section, and that is
not a contradiction of the rule above: what is excluded is `paths.*`, for a
reason that does not reach here. The netbook is a Debian box at a chart table,
where hand-editing JSON is exactly what §15.5 exists to avoid. Blanking the field
removes the button. It takes effect on restart, per the editor's one standing
rule.

| State | Button | Why |
|---|---|---|
| unset / blank | **absent** | Nothing to offer. The `_vessel_card`-returns-None and ⚙-omitted precedent (§15.2), doubled |
| set, but the directory or `python3` is missing | **present, fails onto `_notice`** | Configuration decides *presence*; runtime decides the *message* |

**No `Path.exists()` in the view builder, deliberately.** A stat there would make
an unmounted disk look like an unset setting, and the skipper would hunt the
config file instead of the mount.

## 17.4 Starting it — one copy, detached, never a traceback

`Companion.start()` returns `(message, ok)` — the `(text, ok)` shape the backup
status already uses — and **never raises**. A traceback out of a Tk callback goes
to a console the netbook does not have, so the skipper would press the button and
see nothing happen at all: the one outcome this whole design is against.

- **One copy.** `poll()` does double duty — it answers "is it running?" *and*
  reaps the process once it has exited. Without it, a skipper opening and closing
  Moorwatch all afternoon leaves a queue of zombies parented to the log, and a
  second press stacks a second window behind a fullscreen SYLog.
- **`start_new_session=True`.** SYLog is routinely started from a terminal and
  Ctrl-C'd; without this, that Ctrl-C reaches the whole process group and takes
  Moorwatch with it — the exact opposite of "its own tool alongside". Accepted
  and harmless on the Windows dev box, so no platform branch. **Invisible until
  it fails, and it fails only on the boat**, which is why it has its own test.
- **Failures are caught as `OSError`, not `FileNotFoundError`.** Measured, both
  spellings: a missing `cwd` raises `NotADirectoryError` on Windows and
  `FileNotFoundError` on Linux, and a bad mode raises `PermissionError`. Catching
  `FileNotFoundError` alone leaks a traceback on *the case most likely to happen*
  — TSCTide not installed at the configured path. Found in review, before it
  shipped; pinned by a parametrised test over all three.
- **stdout/stderr are INHERITED, not `DEVNULL`.** Under autostart they reach the
  journal, where a failing Moorwatch can be read with `journalctl`. `DEVNULL`
  would make a crash-on-import silent, and **silence is indistinguishable from
  the window-having-opened-behind-us case** — the one confusion this feature must
  not create.

**The launch button takes the launch grid's sixth cell** — the one §14.9 sized
the 2×3 grid for and never filled, its five entry points having been one short.
Start Session (col 0) and Engine (col 2) keep their positions, so nothing moves
under the thumb. Measured at **594 of the 800 floor** with the button in: 206 px
spare. Unlike every other surface in this tool, this one had room waiting.

Labelled **`Moorwatch ↗`**, not `Moorwatch ▶`: `▶`/`■` are load-bearing here as
*timer running/stopped* (Engine, Auto-log), and an arrow would promise a toggle
that stops Moorwatch. `↗` says "opens outside this window".

**Both new codepoints render on the netbook — `↗` (U+2197) and `⌂` (U+2302),
confirmed 17 July 2026 on the real machine.** The fallback, had either tofued,
was plain text; it was not needed.

*Worth keeping for the next glyph, because the dev box cannot answer this
question:* it has no DejaVu Sans installed (`_preferred_font_family` picks Segoe
UI here, DejaVu or Noto there), so a coverage measurement taken on Windows
silently falls back to another font and proves nothing — it reports a width for a
glyph the netbook may not have. The `⚙` (U+2699) already shipping was evidence
that *a* glyph works in the bar, not that *this* one would. **Only the netbook can
settle it, so a new glyph ships as unverified until someone looks at it.**

The result goes on
**`_notice`**, never `_banner`, which `refresh()` rewrites every 250 ms tick
(§6.5, the engine rule). This is the first *good* news to land on `_notice`; the
line was always specified as "the result of the last button press" — only its
examples were warnings.

## 17.5 The round trip — the session was never in the view

Reaching the launcher mid-session is the other half of the request, and **it
needed no state migration at all.** Not because the trip is careful, but because
of where the session already lives:

- Every session fact is in **SQLite** — `autolog_active` is a column, entries are
  written on the spot, and `SessionView.refresh_controls()` re-derives every
  control from the database rather than a variable (invariant 3).
- **Every timer is gated on `open_session()` — a query, not the view.** This is
  worth stating precisely, because it is easy to get wrong from a skim: the
  `isinstance(current, SessionView)` checks in `_autolog_tick` and
  `_drain_and_refresh` only decide *which view to redraw*. They do not gate the
  work. Auto-log, distance sampling and auto-backup all keep running with the
  launcher on screen.
- The one in-memory item, `App.accumulator`, hangs off **`App`**, not the frame
  (§5.5).
- `LaunchView` already relabelled its button **Resume Session**, and
  `_start_session` already resumed. The return leg needed no new code.

**This is invariant 3 paying out somewhere it was not designed for** — the same
way §8's "readable forever without config.json" paid out for the HTML export
(§14.10.1). It also means the invariant is now *load-bearing for a feature*, not
merely a principle, and it was previously **unguarded**: nothing stopped a future
refactor from gating the timers on the view and silently stopping auto-logging
mid-passage while the skipper looked at the launcher.
`test_autolog_keeps_writing_while_the_launcher_shows` is that guard, and it is
the test in §17 worth keeping above all the others.

**The launcher is reached by a `⌂` on the status bar, not a button in the session
toolbar.** The §15.5 ⚙ argument, exactly: a control that must be pressable
mid-session cannot live in the session's own toolbars. Unlike the ⚙, it does not
return to the caller — the launcher **is** the destination, and the way back is
the Resume Session button already on it.

**One caveat, named rather than discovered:** the ⌂ is global chrome, so it is
pressable from a half-filled form, where it discards the entry. Not a *new*
hazard — every form already has a `Cancel` that calls `show_launch` — but it is
now reachable by mis-tap. Accepted; the bar's virtue is that it never changes,
and stateful chrome would be a new idea here.

## 17.6 What this spends at the 800 px floor — and a correction to §16.3

The honest section. Measured with a method calibrated against **§16.3's own
figure**: it reproduces the preset row at **791 px exactly**, so these numbers
are directly comparable.

| Surface | Used | Spare at the 800 floor |
|---|---|---|
| Launch grid row 0, **with Moorwatch** | 594 | **206** — the button is free |
| SessionView row 2 (presets) | 791 | 9 (unchanged; §16.3's figure, reproduced) |
| SessionView row 1 (toolbar) | 729 | **71** — *not the ~95 §16.3 claims* |

**§16.3 is corrected: row 1 has 71 px spare, not ~95.** The error mattered — it
is what made a `‹ Launcher` button look feasible. Measured, nothing labelled
fits: `‹ Launcher` needs **128 px**, `‹ Launch` 111, `‹ Menu` 99. Only a bare
glyph does (`‹` 45, `⌂` 55).

**A bare `‹` in row 1 was rejected on the docs' own terms.** It is "another
squeeze", it leaves the row one feature from breaking — and it is a lie: this is
not Back. The session stays open; the action is "show me the launcher *without*
ending this". `‹` cannot say that, and a skipper reading it as Back may go
looking for End Session.

**The `⌂` on the status bar costs 36 px**, and the bar is the one surface here
with room for it:

| Status bar | Without ⌂ | With ⌂ | Floor |
|---|---|---|---|
| §15.7's "today" case (baseline note `none`, no warnings) | 690 | **726** | 784 ✓ |
| §15.7's worst case (documented note + clock warning + failed backup) | **1069** | **1105** | 784 ✗ |

*An earlier draft of this section claimed the ⌂ cost ~54 px and "spent §15.7's
last headroom". Both were wrong, and wrong the same way §16.3's ~95 px was — from
a reconstruction that was never calibrated.* Measured against a real `App`: the
today case keeps 58 px of headroom, so **the ⌂ does not bite**.

**The worst case is a different matter, and it is not §17's to fix.** It measures
**1069 px before this change** — already past the 784 floor, and already past the
netbook's real 1024 (§14.10.2). §15.7 estimated ~940 and judged it "does not bite
today"; the direction was right and the figure was optimistic. §17 adds 36 px to a
number that was already over. That does not make it acceptable — it makes it
**pre-existing**, and §15.7 is amended with the measured figure so the next person
meets it in the doc rather than on the water. The squeeze victim remains the one
§15.7 named: `_engine_label`, packed `expand=True`, carrying the provenance §7
says the hours must never appear without.

**Also rejected:** a control on `bar0`, the vessel strip (778/800 with every
field set, and it vanishes when no vessel is configured); `End Session` → `End`
for ~60 px (muscle memory, on the one destructive control that most deserves a
full label); moving `Details` to `bar0` for ~96 px (a genuine re-think, but scope
creep, and it moves a control already learned on the netbook).

## 17.7 The window that opens behind — answered with words, not stacking

Moorwatch's window is small, and SYLog may be **fullscreen** for the
alt-tab-with-OpenCPN workflow (§2.1). The companion can open behind it and read
as nothing having happened.

**Launching therefore leaves fullscreen, and says so.** Undoing a setting the
skipper chose is only acceptable if it is not done silently: the notice reads
*"SYLog left fullscreen so it is visible — F11 restores."*

**On any successful press, not only the one that spawns.** A press with Moorwatch
*already running* is the skipper saying "I cannot see it" — and a fullscreen
SYLog is what is causing that. Refusing to move there would answer the complaint
by restating it. (Caught in review: the first cut dropped fullscreen only on the
spawning press, which handled every case except the one that most needed it.)

**A failed press does not drop fullscreen** — the setting is the price of showing
the companion, and if it never started, it was paid for nothing.

The messages carry the rest, and cost nothing:

1. The first press says *"started in its own window — alt-tab to it"* — setting
   the expectation *before* the confusion. Not "started" alone, which implies the
   skipper is about to see something.
2. The second press cannot double-spawn, and **repeats the remedy** rather than
   merely refusing.
3. A crash-on-start is not silent, because stderr is inherited (§17.4).

**Rejected:** `-topmost` juggling; lowering or withdrawing SYLog (it would hide
the log to advertise something else); `wmctrl`/`xdotool` (not stdlib — §2.1). And
**the raise-itself fix belongs to Moorwatch**: a GUI app that opens behind the
focused window is a bug in *that* app. §16.4 set this precedent explicitly —
*"TSCTide learned to read CSV rather than this tool learning to write XLSX … The
dependency rule held; the other program moved."* Same shape: SYLog says what it
did; Moorwatch shows up.

## 17.8 Decision log additions (fold into §13)

| Decision | Rationale |
|---|---|
| Moorwatch is **started as a separate process**, never embedded | §16.1 rejected the readout and prescribed the remedy: "It runs as its own tool alongside". A process start is that sentence, implemented — nothing displayed, inferred, imported or stored. §16.5 is upheld, not overturned |
| The button **never reports Moorwatch's state**, not even running/stopped | A state readout of another tool inside this window is the instrument §1.2 says this is not. Hence a plain `↗`, not a `▶`/`■` toggle. The line is not "no countdown" — it is no readout at all |
| The command is a **code constant**; config carries only the directory | The CLI is a fact about Moorwatch, which ships from the repo this tool already exports to, so the two change together. Whole-argv in config makes a logbook's config a run-anything surface, and a typo in it is a dead button with no diagnosis |
| `tools.moorwatch_dir`, optional, **not** in `_REQUIRED`, and **in** the Settings editor | What §15.5 excludes is `paths.*`, to keep invariant 11 out of a text box. This key carries neither property, and the netbook is a Debian box at a chart table |
| The example ships it **blank**, unlike `paths.database` | Optional keys ship absent-shaped. A fresh install — including the Windows dev box — gets no button rather than a broken one |
| Unset → the button is **absent**; configured-but-broken → it **stays** and fails onto `_notice` | Configuration decides presence; runtime decides the message. A `Path.exists()` in the view builder would make an unmounted disk look like an unset setting |
| At most one copy; `poll()` both answers and reaps | A second press would otherwise stack a window behind a fullscreen log, and a closed copy would leave a zombie parented to it |
| Detached with `start_new_session=True` | SYLog is routinely Ctrl-C'd from a terminal; without it, that Ctrl-C takes Moorwatch too — the opposite of "its own tool". Invisible until it fails, and it fails only on the boat |
| Failures caught as `OSError`, not `FileNotFoundError` | Measured: a missing `cwd` raises `NotADirectoryError` on Windows and `FileNotFoundError` on Linux. The likeliest failure — TSCTide not installed — would have leaked a traceback to a console the netbook does not have |
| stdout/stderr **inherited**, not `DEVNULL` | Under autostart they reach the journal. `DEVNULL` makes a crash-on-import silent, and silence is indistinguishable from the window-opened-behind case |
| The result goes on `_notice`, never `_banner` | `refresh()` rewrites `_banner` every 250 ms tick; §6.5's engine rule, applied. The first good news to land there |
| Launching **leaves fullscreen, and says so** — on *any* successful press, including one with it already running; a *failed* press does not | The companion opens behind a fullscreen window and reads as nothing happening. Undoing a chosen setting silently would look like a second bug; the notice names F11. A press with it already up is "I cannot see it", which is the case that most needs the window moved. A press that failed bought nothing with the setting |
| Raising its own window is **Moorwatch's** job | §16.4's precedent: the other program moved. `wmctrl`/`xdotool` are not stdlib (§2.1), and `-topmost` juggling is a bug farm |
| The launcher is reachable from any view via `⌂` on the status bar | The §15.5 ⚙ argument exactly. Measured, the session's toolbars have 71 px and 9 px spare, and §16.3 forbids another squeeze. A bare `‹` fits but misnames the action: this is not Back, the session stays open |
| The session survives the round trip **because it was never in the view** | Every timer is gated on `open_session()` — a query, not the view; the `isinstance` checks only pick what to redraw. Invariant 3, reaching the timers, and now load-bearing rather than merely principled |
| §16.3's "~95 px spare on row 1" is **corrected to 71 px** | Re-measured by a method that reproduces §16.3's own 791 px exactly. The error is what made a labelled `‹ Launcher` look feasible, and it is not |
| §15.7's "~940 px" worst case is **corrected to 1069 px** — already past the netbook's real 1024, before §17 | Measured against a real `App`. The ⌂ costs 36 px, and the today case (690 → 726 of 784) keeps its headroom, so §17 does not bite. The worst case was over before this change and is §15.7's to fix, not §17's — but an estimate that says "does not bite" must not stay unmeasured once something is built on top of it |
