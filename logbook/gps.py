"""gpsd client — raw JSON over the TCP socket (localhost:2947), no wrapper.

The ``python3-gps`` wrapper is thin and historically fragile; the protocol is
~40 lines to speak directly. The socket read runs on a daemon thread that
pushes messages to a ``queue.Queue``; the Tk main loop drains that queue on an
``after()`` tick.

MUST NOT touch any Tk widget from the reader thread — Tkinter is not
thread-safe (invariant 2). This module therefore knows nothing about Tk; it only
puts ``("tpv", Fix)`` and ``("status", str)`` messages onto the queue it is
given.

TPV handling:
  - ``speed`` is metres/second; convert to knots (× 1.94384).
  - ``mode`` 0/1 = no fix  → auto-log is suppressed, not faked (decided by the
    consumer via ``classify``).
  - a fix older than ~10 s is treated as no fix (a frozen position is more
    dangerous than a blank one).
  - ``mode`` 2 (2D) is accepted; only altitude is invalid, which is irrelevant
    here.

Build order: step 1 — headless, prints classified TPV, verifies the data path
before any GUI exists. Run: ``python -m logbook.gps`` (``--host/--port`` point it
at the mock on localhost or a live gpsd on the LAN).
Spec: §3.3, §3.4.
"""

from __future__ import annotations

import argparse
import json
import queue
import socket
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone

MPS_TO_KN = 1.94384  # knots per metre-per-second
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 2947
DEFAULT_STALE_SEC = 10.0
_WATCH = b'?WATCH={"enable":true,"json":true};\r\n'


@dataclass(frozen=True)
class Fix:
    """One decoded TPV. Fields absent from gpsd are ``None`` — never fabricated."""

    time: datetime | None       # fix timestamp as reported by gpsd, UTC
    mode: int                   # 0/1 = no fix, 2 = 2D, 3 = 3D
    lat: float | None           # decimal degrees, N positive
    lon: float | None           # decimal degrees, E positive
    sog_kn: float | None        # speed over ground, knots (converted from m/s)
    cog_deg: float | None       # course over ground, degrees true (NOT heading)

    @property
    def has_position(self) -> bool:
        return self.mode >= 2 and self.lat is not None and self.lon is not None

    def age_seconds(self, now_utc: datetime) -> float | None:
        if self.time is None:
            return None
        return (now_utc - self.time).total_seconds()


def parse_gps_time(value: str | None) -> datetime | None:
    """Parse a gpsd ISO 8601 timestamp (trailing Z) to an aware UTC datetime."""
    if not value:
        return None
    try:
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def parse_tpv(obj: dict) -> Fix:
    """Build a Fix from a gpsd TPV dict, converting speed m/s → knots."""
    speed = obj.get("speed")
    return Fix(
        time=parse_gps_time(obj.get("time")),
        mode=int(obj.get("mode", 0)),
        lat=obj.get("lat"),
        lon=obj.get("lon"),
        sog_kn=(speed * MPS_TO_KN) if isinstance(speed, (int, float)) else None,
        cog_deg=obj.get("track"),
    )


def classify(fix: Fix, now_utc: datetime, stale_sec: float) -> str:
    """The §3.3 decision: is this fix usable, and if not, why not?

    'FIX' / '2D' are usable; 'NO FIX' and 'STALE' must suppress auto-logging.
    """
    if not fix.has_position:
        return "NO FIX"
    age = fix.age_seconds(now_utc)
    if age is not None and age > stale_sec:
        return "STALE"
    return "2D" if fix.mode == 2 else "FIX"


class GpsdReader:
    """Reads gpsd on a daemon thread, posting messages to an out-queue.

    Messages: ``("status", str)`` for connection events, ``("tpv", Fix)`` for
    each decoded fix. Reconnects with exponential backoff; a malformed line is
    reported and skipped rather than crashing the reader.
    """

    def __init__(self, out: "queue.Queue", host: str = DEFAULT_HOST,
                 port: int = DEFAULT_PORT, *, connect_timeout: float = 5.0,
                 max_backoff: float = 30.0) -> None:
        self._out = out
        self._host = host
        self._port = port
        self._connect_timeout = connect_timeout
        self._max_backoff = max_backoff
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="gpsd-reader", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def join(self, timeout: float | None = None) -> None:
        self._thread.join(timeout)

    # -- internals (reader thread only) --------------------------------------

    def _status(self, msg: str) -> None:
        self._out.put(("status", msg))

    def _run(self) -> None:
        backoff = 1.0
        while not self._stop.is_set():
            try:
                self._status(f"connecting to {self._host}:{self._port}")
                with socket.create_connection(
                    (self._host, self._port), timeout=self._connect_timeout
                ) as sock:
                    sock.sendall(_WATCH)
                    self._status("connected")
                    backoff = 1.0
                    self._read_loop(sock)  # returns on EOF or stop
            except OSError as exc:
                self._status(f"disconnected: {exc}")
            else:
                self._status("disconnected: stream ended")
            if self._stop.is_set():
                break
            self._status(f"reconnecting in {backoff:.0f}s")
            self._interruptible_sleep(backoff)
            backoff = min(backoff * 2.0, self._max_backoff)

    def _read_loop(self, sock: socket.socket) -> None:
        sock.settimeout(1.0)  # so we can poll the stop flag between reads
        buf = bytearray()
        while not self._stop.is_set():
            try:
                chunk = sock.recv(4096)
            except socket.timeout:
                continue
            if not chunk:
                return  # EOF — gpsd closed the connection
            buf.extend(chunk)
            while b"\n" in buf:
                line, _, rest = buf.partition(b"\n")
                buf = bytearray(rest)
                self._handle_line(bytes(line))

    def _handle_line(self, raw: bytes) -> None:
        text = raw.strip()
        if not text:
            return
        try:
            obj = json.loads(text)
        except json.JSONDecodeError:
            self._status(f"ignored malformed line: {text[:60]!r}")
            return
        if obj.get("class") == "TPV":
            self._out.put(("tpv", parse_tpv(obj)))

    def _interruptible_sleep(self, seconds: float) -> None:
        deadline = time.monotonic() + seconds
        while not self._stop.is_set():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            time.sleep(min(0.2, remaining))


def _format(fix: Fix, state: str, age: float | None) -> str:
    tstr = fix.time.strftime("%H:%M:%S") if fix.time else "--:--:--"
    if fix.has_position:
        pos = f"{fix.lat:8.4f} {fix.lon:9.4f}"
        motion = (f"SOG {fix.sog_kn:4.1f}kn  COG {fix.cog_deg:5.1f}°"
                  if fix.sog_kn is not None and fix.cog_deg is not None else "")
    else:
        pos = "   --   " + "    --   "
        motion = ""
    agestr = f"age {age:5.1f}s" if age is not None else "age    -- "
    return f"{tstr}  mode {fix.mode}  {state:6}  {pos}  {agestr}  {motion}".rstrip()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Headless gpsd client — decode and classify TPV.")
    ap.add_argument("--host", default=DEFAULT_HOST)
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--stale-sec", type=float, default=DEFAULT_STALE_SEC,
                    help="a fix older than this is treated as no fix")
    ap.add_argument("--duration", type=float, default=0.0,
                    help="stop after N seconds (0 = run until Ctrl-C)")
    args = ap.parse_args(argv)

    q: "queue.Queue" = queue.Queue()
    reader = GpsdReader(q, args.host, args.port)
    reader.start()
    print(f"# gpsd client -> {args.host}:{args.port}  (stale threshold {args.stale_sec:g}s)  "
          f"Ctrl-C to stop", flush=True)

    deadline = time.monotonic() + args.duration if args.duration > 0 else None
    try:
        while deadline is None or time.monotonic() < deadline:
            try:
                kind, payload = q.get(timeout=0.25)
            except queue.Empty:
                continue
            if kind == "status":
                print(f"[status] {payload}", flush=True)
            else:
                now = datetime.now(timezone.utc)
                state = classify(payload, now, args.stale_sec)
                print(_format(payload, state, payload.age_seconds(now)), flush=True)
    except KeyboardInterrupt:
        print("\n# stopping", flush=True)
    finally:
        reader.stop()
        reader.join(timeout=2.0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
