#!/usr/bin/env python3
"""
Streaming load test for /ws/stream.

Simulates N concurrent Android-SDK users. Each user opens a WebSocket,
pushes --frames-per-user JPEG frames at --fps, and then waits for the
server's stop_stream + process_result. Your real pipeline
(YOLO gate -> crop -> rotation -> /process -> VLM) runs unmodified.

    pip install websockets httpx

Example:
    python ws_loadtest.py \
      --url ws://10.244.32.4:4097/ws/stream \
      --image-dir /application/clean \
      --token "$JWT" \
      --users 64 --sessions 256 --frames-per-user 15 --fps 10
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import statistics
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

import websockets
from websockets.exceptions import ConnectionClosed

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff"}

# ---------------------------------------------------------------------------
# >>> PUT YOUR TOKEN HERE (or pass --token / export WS_TOKEN=...) <<<
# ---------------------------------------------------------------------------
DEFAULT_TOKEN = os.getenv(
    "WS_TOKEN",
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
    "eyJzdWIiOiJBdXRoZW50aWNhdGVkIHVzZXIiLCJpc3MiOiJ0cnVzdGVkLWlzc3VlciIsImF1"
    "ZCI6InZhbGlkLWF1ZGllbmNlIiwiZXhwIjoxODExNTkwODA2fQ."
    "LkCWBY1cK7hM904BHA43d-sBWq4OB_y6HraItuzp5tI",
)
DEFAULT_USER_ID = "NEORILTH4X1"
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------

@dataclass
class Session:
    idx: int
    user_id: str
    request_id: str

    ok: bool = False
    error: str = ""
    status: int = 0

    connect_s: Optional[float] = None      # handshake
    first_gate_s: Optional[float] = None   # first frame_result after first send
    gate_latencies: List[float] = field(default_factory=list)  # per-frame YOLO round trip
    stop_s: Optional[float] = None         # session start -> stop_stream received
    pipeline_s: Optional[float] = None     # stop_stream -> process_result (rot+VLM+db)
    total_s: Optional[float] = None        # connect -> final message

    frames_sent: int = 0
    frames_acked: int = 0
    frames_to_clean: Optional[int] = None  # how many frames until gate tripped
    got_stop: bool = False

    payload_kb: float = 0.0
    result: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Helpers (same shape as your HTTP script)
# ---------------------------------------------------------------------------

def collect_images(image_dir: str, limit: Optional[int]) -> List[str]:
    files = []
    for root, _dirs, names in os.walk(image_dir):
        for n in sorted(names):
            if os.path.splitext(n)[1].lower() in IMAGE_EXTS:
                files.append(os.path.join(root, n))
    if not files:
        sys.exit(f"No images found under {image_dir}")
    if limit:
        files = files[:limit]
    return files


def pct(values: List[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    if len(s) == 1:
        return s[0]
    return s[int(round((p / 100.0) * (len(s) - 1)))]


def fmt_mmm(values: List[Optional[float]], unit: str = "s", nd: int = 3) -> str:
    vals = [v for v in values if v is not None]
    if not vals:
        return "n/a"
    return (f"{min(vals):.{nd}f}{unit} / "
            f"{statistics.fmean(vals):.{nd}f}{unit} / "
            f"{max(vals):.{nd}f}{unit}")


def fmt_mmm_int(values: List[Optional[int]]) -> str:
    vals = [v for v in values if v is not None]
    if not vals:
        return "n/a"
    return f"{min(vals)} / {statistics.fmean(vals):.2f} / {max(vals)}"


def gpu_line() -> List[str]:
    try:
        out = subprocess.run(
            ["nvidia-smi",
             "--query-gpu=name,memory.used,memory.total,utilization.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        )
        if out.returncode != 0 or not out.stdout.strip():
            return ["  nvidia-smi unavailable"]
        lines = []
        for i, row in enumerate(out.stdout.strip().splitlines()):
            name, used, total, util = [c.strip() for c in row.split(",")]
            lines.append(f"  gpu{i} {name:<24} {used} / {total} MiB   util {util}%")
        return lines
    except Exception:
        return ["  nvidia-smi unavailable"]


def build_frame(seq: int, jpeg: bytes) -> bytes:
    """SDK binary frame: [4B big-endian size][4B big-endian seq][JPEG]."""
    return len(jpeg).to_bytes(4, "big") + seq.to_bytes(4, "big") + jpeg


# ---------------------------------------------------------------------------
# One simulated user
# ---------------------------------------------------------------------------

async def _reader(ws, sess: Session, t_start: float,
                  stop_event: asyncio.Event, sent_ts: Dict[int, float]) -> None:
    """Consume server messages until process_result / error / close."""
    try:
        async for raw in ws:
            now = time.perf_counter()
            if isinstance(raw, bytes):
                continue
            try:
                msg = json.loads(raw)
            except Exception:
                continue

            typ = msg.get("type", "")

            if typ == "frame_result":
                seq = msg.get("seq")
                sess.frames_acked += 1
                if seq in sent_ts:
                    lat = now - sent_ts[seq]
                    sess.gate_latencies.append(lat)
                    if sess.first_gate_s is None:
                        sess.first_gate_s = lat

            elif typ == "stop_stream":
                sess.got_stop = True
                sess.stop_s = now - t_start
                sess.frames_to_clean = sess.frames_sent
                stop_event.set()

            elif typ == "process_result":
                sess.status = int(msg.get("status", 0) or 0)
                sess.result = msg.get("result", {}) or {}
                sess.ok = sess.status == 200
                if not sess.ok:
                    sess.error = f"process HTTP {sess.status}"
                if sess.stop_s is not None:
                    sess.pipeline_s = (now - t_start) - sess.stop_s
                sess.total_s = now - t_start
                return

            elif typ == "error" or "error" in msg:
                sess.error = str(msg.get("message") or msg.get("error"))[:200]
                sess.total_s = now - t_start
                stop_event.set()
                return

    except ConnectionClosed as e:
        if not sess.ok and not sess.error:
            sess.error = f"closed before result (code={e.code})"
    except Exception as e:
        if not sess.error:
            sess.error = f"{type(e).__name__}: {e}"
    finally:
        stop_event.set()


async def one_session(idx: int, args, frames: List[bytes],
                      sem: asyncio.Semaphore) -> Session:

    user_id = args.user_id
    img_token = uuid.uuid4().hex[:12]
    request_id = f"{args.request_id_prefix}{img_token}"
    image_name = args.image_name_template.format(user=user_id, token=img_token)

    sess = Session(idx=idx, user_id=user_id, request_id=request_id)
    sess.payload_kb = sum(len(f) for f in frames) / 1024.0

    params = {
        "token":        args.token,
        "image_name":   image_name,
        "ean_code":     args.ean_code,
        "env_id":       args.env_id,
        "client_name":  args.client_name,
        "request_id":   request_id,
        "capture_type": args.capture_type,
        "is_recapture": str(args.is_recapture).lower(),
        "metadata_id":  args.metadata_id or "",
    }
    if None not in (args.x1, args.y1, args.x2, args.y2):
        # NOTE: the real SDK sends x1/y1/x2/y2, but stream_endpoint declares
        # roi_x1/roi_y1/roi_x2/roi_y2. Default --roi-param-style=sdk reproduces
        # production exactly (params ignored, ROI check skipped). Use
        # --roi-param-style=server to actually exercise the ROI branch.
        prefix = "roi_" if args.roi_param_style == "server" else ""
        params.update({f"{prefix}x1": args.x1, f"{prefix}y1": args.y1,
                       f"{prefix}x2": args.x2, f"{prefix}y2": args.y2})

    uri = f"{args.url}?{urlencode(params)}"
    interval = 1.0 / args.fps if args.fps > 0 else 0.0

    async with sem:
        t_start = time.perf_counter()
        try:
            async with websockets.connect(
                uri,
                max_size=None,
                open_timeout=args.connect_timeout,
                close_timeout=5,
                ping_interval=20,
            ) as ws:
                sess.connect_s = time.perf_counter() - t_start

                stop_event = asyncio.Event()
                sent_ts: Dict[int, float] = {}
                reader = asyncio.create_task(
                    _reader(ws, sess, t_start, stop_event, sent_ts)
                )

                for seq, jpeg in enumerate(frames, start=1):
                    if stop_event.is_set():
                        break
                    sent_ts[seq] = time.perf_counter()
                    try:
                        await ws.send(build_frame(seq, jpeg))
                    except ConnectionClosed:
                        break
                    sess.frames_sent += 1
                    if interval:
                        try:
                            await asyncio.wait_for(stop_event.wait(), timeout=interval)
                        except asyncio.TimeoutError:
                            pass

                # SDK behaviour: if the gate never tripped, say DONE
                if not stop_event.is_set():
                    try:
                        await ws.send("DONE")
                    except ConnectionClosed:
                        pass

                try:
                    await asyncio.wait_for(reader, timeout=args.result_timeout)
                except asyncio.TimeoutError:
                    reader.cancel()
                    sess.error = sess.error or "timeout waiting for process_result"
                    sess.total_s = time.perf_counter() - t_start

        except Exception as e:
            sess.error = f"{type(e).__name__}: {e}"
            sess.total_s = time.perf_counter() - t_start

    if sess.total_s is None:
        sess.total_s = time.perf_counter() - t_start
    return sess


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def report(sessions: List[Session], total_time: float, args) -> None:
    ok = [s for s in sessions if s.ok]
    bad = [s for s in sessions if not s.ok]
    n = len(ok)

    print()
    print(f"RESULTS  users={args.users}  sessions={len(sessions)}  "
          f"frames/user={args.frames_per_user}  fps={args.fps}  "
          f"mode={args.frame_mode}")
    print(f"endpoint={args.url}")
    print("-" * 70)

    if not ok:
        print("ALL SESSIONS FAILED")
        for s in bad[:10]:
            print(f"  #{s.idx}  sent={s.frames_sent} acked={s.frames_acked}  {s.error}")
        print()
        print("GPU")
        for line in gpu_line():
            print(line)
        return

    e2e = [s.total_s for s in ok if s.total_s is not None]

    # ---------------- TIME ----------------
    print("TIME")
    print(f"  total time             {total_time:.2f}s")
    print(f"  session throughput     {n / total_time:.3f} sess/s"
          f"  ({n / total_time * 60:.1f} sess/min)")
    achieved = sum(s.total_s for s in ok if s.total_s) / total_time
    print(f"  avg concurrency        {achieved:.1f} in flight"
          f"   (ceiling {args.users})")
    total_frames = sum(s.frames_sent for s in sessions)
    print(f"  frame throughput       {total_frames / total_time:.1f} frames/s"
          f"  ({total_frames} frames sent)")
    print(f"  NOTE: per-session duration is in END-TO-END below, "
          f"not wall/{n}.")
    if bad:
        print(f"  failures               {len(bad)} / {len(sessions)}")

    # ---------------- END TO END ----------------
    fastest = min(ok, key=lambda s: s.total_s)
    slowest = max(ok, key=lambda s: s.total_s)
    stdev = statistics.stdev(e2e) if len(e2e) > 1 else 0.0

    print()
    print("END-TO-END per session  (connect -> process_result)")
    print(f"  fastest                {min(e2e):.3f}s")
    print(f"  mean                   {statistics.fmean(e2e):.3f}s   (stdev {stdev:.2f}s)")
    print(f"  p25 / p50 / p75        "
          f"{pct(e2e,25):.3f}s / {pct(e2e,50):.3f}s / {pct(e2e,75):.3f}s")
    print(f"  p90 / p95 / p99        "
          f"{pct(e2e,90):.3f}s / {pct(e2e,95):.3f}s / {pct(e2e,99):.3f}s")
    print(f"  fastest session        {fastest.total_s:.3f}s  #{fastest.idx}")
    print(f"  slowest session        {slowest.total_s:.3f}s  #{slowest.idx}")

    # ---------------- WHERE THE TIME GOES ----------------
    print()
    print("WHERE THE TIME GOES (min / mean / max per session)")
    print(f"  ws connect             {fmt_mmm([s.connect_s for s in ok])}")
    print(f"  first frame gate       {fmt_mmm([s.first_gate_s for s in ok])}"
          f"   <- YOLO warm/queue")
    print(f"  time to stop_stream    {fmt_mmm([s.stop_s for s in ok])}"
          f"   <- gate tripped")
    print(f"  pipeline after stop    {fmt_mmm([s.pipeline_s for s in ok])}"
          f"   <- crop+rotation+/process+VLM")

    all_gate = [g for s in ok for g in s.gate_latencies]
    if all_gate:
        print()
        print("PER-FRAME YOLO GATE (round trip, all frames)")
        print(f"  min / mean / max       {fmt_mmm(all_gate)}")
        print(f"  p50 / p90 / p99        "
              f"{pct(all_gate,50):.3f}s / {pct(all_gate,90):.3f}s / {pct(all_gate,99):.3f}s")
        print(f"  gate throughput        {len(all_gate)/total_time:.1f} frames/s classified")

    stops = [s.stop_s for s in ok if s.stop_s is not None]
    pipes = [s.pipeline_s for s in ok if s.pipeline_s is not None]
    if stops and pipes:
        ts, tp = sum(stops), sum(pipes)
        tot = ts + tp
        print()
        print(f"  share                  streaming/gate {ts/tot*100:.0f}%"
              f"   post-process {tp/tot*100:.0f}%")

    # ---------------- FRAMES ----------------
    print()
    print("FRAMES / INPUT (min / mean / max)")
    print(f"  frames sent            {fmt_mmm_int([s.frames_sent for s in ok])}")
    print(f"  frames acked           {fmt_mmm_int([s.frames_acked for s in ok])}"
          f"   (< sent is normal: server stops reading after stop_stream)")
    print(f"  frames until clean     "
          f"{fmt_mmm_int([s.frames_to_clean for s in ok])}"
          f"   (of {args.frames_per_user})")
    print(f"  upload payload KB      {fmt_mmm([s.payload_kb for s in ok], unit='', nd=1)}")
    got_stop = sum(1 for s in sessions if s.got_stop)
    print(f"  hit stop_stream        {got_stop} / {len(sessions)}"
          f"  ({got_stop/len(sessions)*100:.0f}%)")

    # ---------------- GPU ----------------
    print()
    print("GPU")
    for line in gpu_line():
        print(line)

    # ---------------- FAILURES ----------------
    if bad:
        print()
        print("FAILURES")
        for s in bad[:15]:
            print(f"  #{s.idx:<5} sent={s.frames_sent:<3} acked={s.frames_acked:<3} "
                  f"stop={s.got_stop!s:<5} {s.error[:90]}")
        if len(bad) > 15:
            print(f"  ... and {len(bad)-15} more")
    print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def make_frame_set(pool: List[bytes], k: int, rng: random.Random,
                   idx: int = 0, mode: str = "same") -> List[bytes]:
    """
    Build the k frames one simulated user pushes.

    same      : session i sends pool[i] repeated k times. This is what real
                capture looks like — one product, k frames off one camera —
                so the gate trips at frame 3 like it does in production.
                2000 images -> 2000 distinct sessions.
    partition : session i gets pool[i*k:(i+1)*k], i.e. 15 DIFFERENT products.
                The gate rarely sees 3 consecutive clean, so sessions run long
                and some never trip. Useful for stress, not for latency.
    random    : random contiguous window, images reused across sessions.
    """
    if mode == "same":
        return [pool[idx % len(pool)]] * k
    if mode == "partition":
        start = (idx * k) % len(pool)
        return [pool[(start + i) % len(pool)] for i in range(k)]
    if len(pool) >= k:
        start = rng.randrange(len(pool))
        return [pool[(start + i) % len(pool)] for i in range(k)]
    return [pool[i % len(pool)] for i in range(k)]


async def run(args) -> None:
    if not args.token or args.token == "PASTE_YOUR_JWT_HERE":
        sys.exit("No token. Pass --token '<jwt>' or export WS_TOKEN=... "
                 "or edit DEFAULT_TOKEN at the top of this file.")

    paths = collect_images(args.image_dir, args.limit)
    pool = []
    for p in paths:
        with open(p, "rb") as f:
            pool.append(f.read())
    print(f"Loaded {len(pool)} images into memory "
          f"({sum(len(b) for b in pool)/1024/1024:.1f} MB)")
    print(f"users(concurrent)={args.users}  sessions(total)={args.sessions}  "
          f"frames/user={args.frames_per_user}  fps={args.fps}")

    rng = random.Random(args.seed)
    sem = asyncio.Semaphore(args.users)

    if args.frame_mode == "same":
        print(f"Frame mode: same  — each session sends 1 product x "
              f"{args.frames_per_user} frames (production-like). "
              f"{len(pool)} sessions possible without reuse.")
        if args.sessions > len(pool):
            print(f"  NOTE: --sessions {args.sessions} > {len(pool)} images, "
                  f"products will repeat")
    elif args.frame_mode == "partition":
        max_unique = len(pool) // args.frames_per_user
        print(f"Frame mode: partition — each session sends "
              f"{args.frames_per_user} DIFFERENT products. "
              f"{max_unique} sessions without reuse.")
        print("  WARNING: the clean gate needs 3 CONSECUTIVE clean frames of "
              "the same item.\n"
              "  Mixed products means it often never trips — expect long "
              "sessions and failures.\n"
              "  Use --frame-mode same for realistic latency numbers.")
        if args.sessions > max_unique:
            print(f"  NOTE: --sessions {args.sessions} > {max_unique}, "
                  f"images will wrap around and repeat")

    # ── Warmup: one lone session so CUDA graphs / caches are hot ──────────
    for i in range(args.warmup):
        w = await one_session(-(i + 1), args,
                              make_frame_set(pool, args.frames_per_user, rng,
                                             idx=0, mode=args.frame_mode), sem)
        print(f"  warmup {i+1}: ok={w.ok}  total={w.total_s:.3f}s  "
              f"stop={w.stop_s}  {w.error}")

    print("Running streaming load test...")

    t0 = time.perf_counter()
    tasks = []
    for i in range(args.sessions):
        frames = make_frame_set(pool, args.frames_per_user, rng,
                                idx=i, mode=args.frame_mode)
        tasks.append(asyncio.create_task(one_session(i, args, frames, sem)))
        if args.ramp:
            await asyncio.sleep(args.ramp)
    sessions = await asyncio.gather(*tasks)
    total_time = time.perf_counter() - t0

    report(sessions, total_time, args)

    if args.show_outputs:
        print("SAMPLE OUTPUTS")
        for s in [x for x in sessions if x.ok][: args.show_outputs]:
            print(f"  #{s.idx} -> {json.dumps(s.result)[:300]}")
        print()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="ws://127.0.0.1:4097/ws/stream")
    ap.add_argument("--image-dir", required=True)
    ap.add_argument("--token", default=DEFAULT_TOKEN,
                    help="JWT. Or set WS_TOKEN env var, or DEFAULT_TOKEN in this file.")

    # load shape
    ap.add_argument("--users", type=int, default=32,
                    help="concurrent websocket sessions in flight")
    ap.add_argument("--sessions", type=int, default=None,
                    help="total sessions to run (default = --users)")
    ap.add_argument("--frames-per-user", type=int, default=15)
    ap.add_argument("--fps", type=float, default=10.0,
                    help="frames per second per user; 0 = burst as fast as possible")
    ap.add_argument("--ramp", type=float, default=0.0,
                    help="seconds between launching each session (avoids thundering herd)")
    ap.add_argument("--warmup", type=int, default=1)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--frame-mode", choices=["same", "partition", "random"],
                    default="same",
                    help="same (default) = 1 product x N frames per session, "
                         "like real capture. partition = N different products "
                         "per session (stress only). random = random window.")
    ap.add_argument("--partition", action="store_true",
                    help="deprecated alias for --frame-mode partition")

    # SDK query params — defaults taken from a real capture
    ap.add_argument("--user-id", default=DEFAULT_USER_ID)
    ap.add_argument("--image-name-template",
                    default="S001_{user}_SDK-01_{token}.jpg",
                    help="{user} and {token} are substituted. Position matters: "
                         "the server reads parts[1] as user_id and parts[3] as img_token.")
    ap.add_argument("--request-id-prefix", default="lt-")
    ap.add_argument("--ean-code", default="8901030851005")
    ap.add_argument("--env-id", default="12688520260824155446")
    ap.add_argument("--client-name", default="shipsy")
    ap.add_argument("--capture-type", default="false")
    ap.add_argument("--is-recapture", default="false")
    ap.add_argument("--metadata-id", default="")
    ap.add_argument("--x1", type=float, default=27)
    ap.add_argument("--y1", type=float, default=286)
    ap.add_argument("--x2", type=float, default=933)
    ap.add_argument("--y2", type=float, default=994)
    ap.add_argument("--roi-param-style", choices=["sdk", "server"], default="sdk",
                    help="sdk = send x1/y1/x2/y2 like the real SDK (server ignores "
                         "them). server = send roi_x1/... to actually enable the "
                         "ROI centre check.")

    # misc
    ap.add_argument("--limit", type=int, default=None, help="cap images loaded")
    ap.add_argument("--connect-timeout", type=float, default=30.0)
    ap.add_argument("--result-timeout", type=float, default=300.0,
                    help="how long to wait for process_result after frames are sent")
    ap.add_argument("--show-outputs", type=int, default=0)

    args = ap.parse_args()
    if args.partition:
        args.frame_mode = "partition"
    if args.sessions is None:
        args.sessions = args.users

    asyncio.run(run(args))


if __name__ == "__main__":
    main()