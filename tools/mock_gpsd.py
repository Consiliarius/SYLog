"""Synthetic gpsd TPV source — a small TCP emitter for development.

Build order: step 0. NOT an afterthought. It lets development proceed on any
machine with no dongle and no boat, and — critically — lets the failure paths be
tested deliberately rather than by luck: fix loss, stale fix, 2D-only fix, and
gpsd dying mid-session (the paths most likely to be wrong and least likely to
occur by accident).

Chosen over gpsfake because gpsfake and gpsd are Linux-only, and development is
happening on Windows. The emitter speaks the same TPV JSON that logbook/gps.py
consumes, so the client cannot tell it from a real gpsd.

Protocol, kept just faithful enough for the client:
  - on connect, send a ``VERSION`` banner line (as real gpsd does);
  - ignore the client's ``?WATCH`` request and stream ``TPV`` objects, one JSON
    object per ``\\r\\n``-terminated line, at ``--rate`` Hz.

Scenarios (``--scenario``):
  nominal  3D fixes on a steady course; time and position advance each tick.
  2d       as nominal but mode 2 (no altitude). Must be accepted downstream.
  no-fix   mode 1, no lat/lon — gpsd reporting but with no fix.
  stale    one fix, then the SAME time and position resent forever — a latched
           receiver. The client must age this out (a frozen position is more
           dangerous than a blank one).
  drop     nominal, then the connection is closed after ``--drop-after`` s —
           gpsd dying mid-session. The server keeps listening so the client's
           reconnect/backoff can be exercised.

Spec: §12 step 0, §3.3.
"""

from __future__ import annotations

import argparse
import json
import math
import socket
import sys
import time
from datetime import datetime, timezone

MPS_PER_KN = 0.514444  # metres per second in one knot
METRES_PER_DEG_LAT = 111_320.0
SCENARIOS = ("nominal", "2d", "no-fix", "stale", "drop")

VERSION_BANNER = {
    "class": "VERSION",
    "release": "3.mock",
    "rev": "mock",
    "proto_major": 3,
    "proto_minor": 14,
}


def log(msg: str) -> None:
    print(f"[mock_gpsd] {msg}", file=sys.stderr, flush=True)


def iso_utc(ts: float) -> str:
    """gpsd-style ISO 8601 UTC with a trailing Z and millisecond precision."""
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def send(conn: socket.socket, obj: dict) -> None:
    conn.sendall((json.dumps(obj) + "\r\n").encode("utf-8"))


def serve_client(conn: socket.socket, args: argparse.Namespace) -> None:
    """Stream one client until it disconnects, the scenario ends, or Ctrl-C."""
    send(conn, VERSION_BANNER)
    interval = 1.0 / args.rate
    lat, lon = args.start_lat, args.start_lon
    started = time.time()
    frozen: dict | None = None
    tick = 0

    while True:
        now = time.time()

        if args.scenario == "drop" and (now - started) >= args.drop_after:
            log(f"scenario=drop: closing connection after {args.drop_after:g}s")
            return  # caller closes the socket -> EOF at the client

        if args.scenario == "no-fix":
            obj = {"class": "TPV", "device": "mock", "mode": 1, "time": iso_utc(now)}
        elif args.scenario == "stale":
            if frozen is None:  # latch the first fix and resend it verbatim
                frozen = {
                    "class": "TPV", "device": "mock", "mode": 3, "time": iso_utc(now),
                    "lat": round(lat, 6), "lon": round(lon, 6),
                    "track": round(args.track, 1),
                    "speed": round(args.speed * MPS_PER_KN, 3),
                }
            obj = frozen
        else:  # nominal or 2d
            mode = 2 if args.scenario == "2d" else 3
            obj = {
                "class": "TPV", "device": "mock", "mode": mode, "time": iso_utc(now),
                "lat": round(lat, 6), "lon": round(lon, 6),
                "track": round(args.track, 1),
                "speed": round(args.speed * MPS_PER_KN, 3),
            }
            if mode == 3:
                obj["alt"] = 0.0
            # advance the position along the course for the next tick
            step_m = args.speed * MPS_PER_KN * interval
            brg = math.radians(args.track)
            lat += step_m * math.cos(brg) / METRES_PER_DEG_LAT
            lon += step_m * math.sin(brg) / (METRES_PER_DEG_LAT * math.cos(math.radians(lat)))

        send(conn, obj)
        tick += 1
        if tick == 1 or tick % 5 == 0:
            log(f"sent {tick} TPV (mode={obj.get('mode')})")
        time.sleep(interval)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Synthetic gpsd TPV emitter for development.")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=2947)
    ap.add_argument("--rate", type=float, default=1.0, help="TPV updates per second")
    ap.add_argument("--scenario", choices=SCENARIOS, default="nominal")
    ap.add_argument("--drop-after", type=float, default=5.0, help="seconds before 'drop' closes the link")
    ap.add_argument("--start-lat", type=float, default=50.85, help="near Rye Harbour by default")
    ap.add_argument("--start-lon", type=float, default=0.575)
    ap.add_argument("--track", type=float, default=90.0, help="course over ground, degrees true")
    ap.add_argument("--speed", type=float, default=5.0, help="speed over ground, knots")
    args = ap.parse_args(argv)

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as srv:
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((args.host, args.port))
        srv.listen(1)
        log(f"listening on {args.host}:{args.port}  scenario={args.scenario}  rate={args.rate:g}Hz")
        try:
            while True:  # accept clients forever, so reconnects are testable
                conn, addr = srv.accept()
                log(f"client connected from {addr[0]}:{addr[1]}")
                try:
                    serve_client(conn, args)
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    log("client disconnected")
                finally:
                    conn.close()
        except KeyboardInterrupt:
            log("shutting down")
            return 0


if __name__ == "__main__":
    sys.exit(main())
