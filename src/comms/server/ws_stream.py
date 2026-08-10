from __future__ import annotations

import asyncio
import io
import os
import threading
import time
import traceback
from pathlib import Path
from typing import List, Optional, Tuple, Union

import httpx
import jwt
from dotenv import load_dotenv
from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from PIL import Image

load_dotenv("src/configs/.env")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

STREAM_MAX_DURATION_S: int = int(os.getenv("STREAM_MAX_DURATION_S", "30"))
CLEAN_FRAMES_TARGET:   int = 3   # how many clean frames before stop_stream

# YOLO class that counts as "clean"
CLEAN_CLASS_NAME = "real/clean"

# Internal /process and /recapture URLs — same host, same port this server is running on.
# Override via environment variables if needed.
PROCESS_SERVER_URL: str = os.getenv(
    "PROCESS_SERVER_URL",
    "http://localhost:4097/process",
)
RECAPTURE_SERVER_URL: str = os.getenv(
    "RECAPTURE_SERVER_URL",
    "http://localhost:4097/recapture",
)

# ---------------------------------------------------------------------------
# Frame-saving toggle
# ---------------------------------------------------------------------------
# Set  SAVE_FRAMES=true  in your .env (or environment) to persist every
# incoming raw JPEG to disk for debugging / review.
# When false (default) nothing is written to disk.
SAVE_FRAMES: bool = os.getenv("SAVE_FRAMES", "false").lower() == "true"

# Directory under which per-session frame folders are created:
#   <FRAMES_SAVE_DIR>/<ean_code>_<timestamp>/frame_<seq>.jpg
FRAMES_SAVE_DIR: str = os.getenv(
    "WS_FRAMES_SAVE_DIR",
    "/home/shared/saved_frames_src",
)

# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

stream_router = APIRouter()


# ---------------------------------------------------------------------------
# JWT helpers (self-contained — avoids circular import with api.py)
# ---------------------------------------------------------------------------

def _ws_validate_token(token: str, client_name: str) -> bool:
    """
    Validate a JWT token for the given client.
    Mirrors the logic in api.py without importing from it.
    """
    secret_key        = os.getenv("JWT_SECRET_KEY")
    algorithm         = os.getenv("JWT_ALGORITHM", "HS256")
    shipsy_secret_key = os.getenv("SHIPSY_SECRET_KEY")
    shipsy_algorithm  = os.getenv("SHIPSY_ALGORITHM", "HS256")

    if (client_name or "").strip().lower() == "shipsy":
        key, algo = shipsy_secret_key, shipsy_algorithm
    else:
        key, algo = secret_key, algorithm

    try:
        header = jwt.get_unverified_header(token)
        if header.get("alg") == "none":
            return False
        if "jku" in header or "jwk" in header:
            return False
        if "kid" in header and "../" in header.get("kid", ""):
            return False

        jwt.decode(
            token, key, algorithms=[algo],
            audience="valid-audience",
            issuer="trusted-issuer",
        )
        return True

    except Exception as exc:
        print(f"[WS auth] Token validation failed: {exc}")
        return False


# ---------------------------------------------------------------------------
# Frame parsing
# ---------------------------------------------------------------------------

def _parse_frame(data: bytes) -> Tuple[int, int, bytes]:
    """
    Parse SDK binary frame:
        [4B big-endian size][4B big-endian seq][JPEG bytes]

    Returns (size, seq, jpeg_bytes).
    Raises ValueError if the header is malformed.
    """
    if len(data) < 8:
        raise ValueError(f"Frame too short ({len(data)} bytes) — need ≥ 8 for header")
    size = int.from_bytes(data[0:4], "big")
    seq  = int.from_bytes(data[4:8], "big")
    jpeg = data[8:]
    return size, seq, jpeg


def _decode_jpeg(jpeg_bytes: bytes) -> Optional[Image.Image]:
    try:
        img = Image.open(io.BytesIO(jpeg_bytes))
        if img.mode != "RGB":
            img = img.convert("RGB")
        return img
    except Exception as exc:
        print(f"[WS] JPEG decode error: {exc}")
        return None


def _is_clean_detection(detections: list) -> bool:
    """Return True if the top detection (highest confidence) is 'real/clean'."""
    if not detections:
        return False
    return detections[0].get("class_name") == CLEAN_CLASS_NAME


# ---------------------------------------------------------------------------
# Frame saver (runs in a daemon thread — never blocks the WS event loop)
# ---------------------------------------------------------------------------

def _save_frame(jpeg_bytes: bytes, save_path: str) -> None:
    """
    Decode the JPEG, rotate 90° clockwise (to correct Android SDK orientation),
    then write to disk.
    Only called when SAVE_FRAMES=true.
    """
    try:
        os.makedirs(os.path.dirname(save_path), exist_ok=True)
        img = Image.open(io.BytesIO(jpeg_bytes))
        if img.mode != "RGB":
            img = img.convert("RGB")
        # rotate(-90) == 90° clockwise; expand=True keeps full dimensions
        # img_rotated = img.rotate(-90, expand=True)
        # img_rotated.save(save_path, format="JPEG", quality=95)
        img.save(save_path, format="JPEG", quality=60)
        print(f"[WS][Save] Written (rotated 90° CW) → {save_path}")
    except Exception as exc:
        print(f"[WS][Save] Failed to save {save_path}: {exc}")


# ---------------------------------------------------------------------------
# WebSocket endpoint
# ---------------------------------------------------------------------------

@stream_router.websocket("/ws/stream")
async def stream_endpoint(
    websocket:    WebSocket,
    token:        str = Query(..., description="JWT bearer token"),
    image_name:   str = Query(..., description="SDK image name, e.g. store_user_device_token.jpg"),
    ean_code:     str = Query(..., description="Product EAN / barcode"),
    env_id:       str = Query(..., description="Environment / shelf ID"),
    client_name:  str = Query("reliance", description="Client identifier"),
    request_id:   str = Query(..., description="SDK request ID for session tracking"),
    capture_type: str = Query("false", description="'true' for multicapture second shot"),
    is_recapture: Optional[Union[bool, str]] = Query(False, description="True if the image is a recapture"),
    metadata_id:  Optional[str]              = Query("", description="Old metadata ID for recapture"),
    roi_x1:       Optional[float] = Query(None, description="ROI top-left X"),
    roi_y1:       Optional[float] = Query(None, description="ROI top-left Y"),
    roi_x2:       Optional[float] = Query(None, description="ROI bottom-right X"),
    roi_y2:       Optional[float] = Query(None, description="ROI bottom-right Y"),
) -> None:
    """
    WebSocket endpoint for live frame streaming with real-time YOLO quality gate.

    Each incoming JPEG frame is classified by the YOLO model.  When 3 consecutive
    "real/clean" frames are detected the server sends a stop_stream signal to the
    SDK (which stops sending frames but keeps the socket open), then runs the full
    /process pipeline on the last clean frame and returns the metadata result.
    """
    await websocket.accept()
    session_start = time.monotonic()

    # ── Authentication ──────────────────────────────────────────────────────
    if not _ws_validate_token(token, client_name):
        await websocket.send_json({"error": "Unauthorized — invalid or expired token"})
        await websocket.close(code=1008)   # 1008 = Policy Violation
        return

    # ── Retrieve models from app state ──────────────────────────────────────────
    classifier    = websocket.app.state.classifier      # YOLO ImageClassifier (quality gate)

    # ── Buffers ──────────────────────────────────────────────────────────────
    seen_seqs:       set[int]              = set()
    save_threads:    List[threading.Thread]= []      # only populated if SAVE_FRAMES=True
    consecutive_clean: int                = 0        # resets to 0 on ANY non-clean frame
    last_clean_seq:  Optional[int]        = None     # seq of the most recent clean frame
    last_clean_img:  Optional[Image.Image]= None     # PIL image of the most recent clean frame
    last_clean_bbox: Optional[list]       = None     # Bbox of the real/clean detection
    stop_signal_sent = False

    # ── Frame save directory (only matters when SAVE_FRAMES=True) ─────────────
    ean_folder  = ean_code.strip() if ean_code.strip() else "unknown_ean"
    timestamp   = time.strftime("%Y%m%d_%H%M%S", time.gmtime())
    session_dir = str(Path(FRAMES_SAVE_DIR) / f"{ean_folder}_{timestamp}")

    is_recapture_bool = str(is_recapture).strip().lower() in ("true", "1", "yes")

    print(
        f"[WS /ws/stream] Session opened — "
        f"env_id={env_id}  client={client_name}  request_id={request_id}  "
        f"ean={ean_code}  is_recapture={is_recapture} (parsed={is_recapture_bool})  "
        f"metadata_id={metadata_id}  max_duration={STREAM_MAX_DURATION_S}s  "
        f"save_frames={SAVE_FRAMES}" + (f"  save_dir={session_dir}" if SAVE_FRAMES else "")
    )

    # ── Receive loop ─────────────────────────────────────────────────────────
    try:
        while True:
            elapsed           = time.monotonic() - session_start
            timeout_remaining = max(0.0, STREAM_MAX_DURATION_S - elapsed)

            if timeout_remaining == 0:
                print(
                    f"[WS] {STREAM_MAX_DURATION_S}s timeout reached — "
                    f"ending session (consecutive_clean={consecutive_clean})"
                )
                break

            try:
                message = await asyncio.wait_for(
                    websocket.receive(),
                    timeout=timeout_remaining,
                )
            except asyncio.TimeoutError:
                print("[WS] Receive timed out — ending session")
                break

            msg_type = message.get("type", "")

            # ── Disconnect notification ────────────────────────────────────
            if msg_type == "websocket.disconnect":
                print("[WS] Client disconnected gracefully")
                break

            if msg_type != "websocket.receive":
                continue

            # ── Text message — check for "DONE" ───────────────────────
            text = message.get("text")
            if text is not None:
                if text.strip().upper() == "DONE":
                    print(
                        f"[WS] DONE received — "
                        f"consecutive_clean={consecutive_clean} at stop"
                    )
                    break
                # Any other text is ignored
                continue

            # ── Binary frame ──────────────────────────────────────────────
            data = message.get("bytes")
            if not data:
                continue

            try:
                size, seq, jpeg = _parse_frame(data)
            except ValueError as exc:
                print(f"[WS] Malformed frame: {exc}")
                continue

            # Deduplicate by sequence number (SDK sends fire-and-forget)
            if seq in seen_seqs:
                continue
            seen_seqs.add(seq)

            img = _decode_jpeg(jpeg)
            if img is None:
                continue

            arrival_ts = time.monotonic() - session_start

            # ── Optional: save raw JPEG to disk in background thread ──────
            if SAVE_FRAMES:
                save_path = os.path.join(session_dir, f"frame_{seq:06d}.jpg")
                t = threading.Thread(
                    target=_save_frame,
                    args=(jpeg, save_path),
                    daemon=True,
                )
                t.start()
                save_threads.append(t)

            # ── YOLO classify (executor — does not block event loop) ───────
            loop = asyncio.get_event_loop()
            detections = await loop.run_in_executor(None, classifier.predict, img)

            is_clean = False
            current_bbox = None

            if detections and detections[0].get("class_name") == CLEAN_CLASS_NAME:
                current_bbox = detections[0].get("bbox")
                
                # If SDK provided an ROI, ensure the center of the patch is inside it
                if roi_x1 is not None and roi_y1 is not None and roi_x2 is not None and roi_y2 is not None and current_bbox:
                    px1, py1, px2, py2 = current_bbox
                    cx, cy = (px1 + px2) / 2, (py1 + py2) / 2
                    if roi_x1 <= cx <= roi_x2 and roi_y1 <= cy <= roi_y2:
                        is_clean = True
                else:
                    is_clean = True

            if is_clean:
                consecutive_clean += 1
                last_clean_seq = seq
                last_clean_img = img
                last_clean_bbox = current_bbox
            else:
                # Any non-clean frame breaks the streak — reset the counter
                consecutive_clean = 0

            print(
                f"[WS] Frame seq={seq}  ts={arrival_ts:.2f}s  "
                f"detections={len(detections)}  "
                f"top={detections[0]['class_name'] if detections else 'none'}  "
                f"clean={is_clean}  consecutive_clean={consecutive_clean}"
            )

            # ── Send per-frame result back to SDK immediately ─────────────
            frame_result = {
                "type":       "frame_result",
                "seq":        seq,
                "frame_ts":   round(arrival_ts, 3),
                "detections": detections,
            }
            try:
                await websocket.send_json(frame_result)
            except Exception as send_exc:
                print(f"[WS] Send error on frame seq={seq}: {send_exc}")
                break

            # ── Check if we have CLEAN_FRAMES_TARGET consecutive clean frames ──
            if consecutive_clean >= CLEAN_FRAMES_TARGET and not stop_signal_sent:
                print(
                    f"[WS] {CLEAN_FRAMES_TARGET} consecutive clean frames — "
                    f"last clean seq={last_clean_seq}  sending stop_stream"
                )
                try:
                    await websocket.send_json({
                        "type":    "stop_stream",
                        "message": f"{CLEAN_FRAMES_TARGET} consecutive clean frames — please stop sending frames",
                    })
                    stop_signal_sent = True
                except Exception as sig_exc:
                    print(f"[WS] Could not send stop_stream signal: {sig_exc}")
                # Break out of receive loop — WS stays open for process result
                break

    except WebSocketDisconnect:
        print("[WS] WebSocketDisconnect exception")
    except Exception as exc:
        print(f"[WS] Receive loop error: {exc}")
        traceback.print_exc()

    # ── Wait for any pending frame-save threads ───────────────────────────────
    if save_threads:
        print(f"[WS][Save] Waiting for {len(save_threads)} save thread(s)...")
        for t in save_threads:
            t.join(timeout=10)
        print(f"[WS][Save] All frames written to: {session_dir}")

    # ── Post-session pipeline ─────────────────────────────────────────────
    print(
        f"\n[WS] === Session receive loop ended — "
        f"{len(seen_seqs)} total frames, consecutive_clean={consecutive_clean} ==="
    )

    if consecutive_clean < CLEAN_FRAMES_TARGET or last_clean_img is None:
        print("[WS] No clean frames collected — notifying client")
        try:
            await websocket.send_json({
                "type":    "error",
                "message": "item is not clear for scaning retry"
            })
        except Exception as e:
            print(f"[WS] Could not send no-clean-frame error: {e}")
        try:
            await websocket.close()
        except Exception:
            pass
        return

    # ── Select and crop the LAST clean frame ──────────────────────────────
    print(f"[WS] Using last clean frame: seq={last_clean_seq} — cropping patch")
    
    try:
        # ── Step 1: Crop the label patch using bbox from classifier ───────
        if last_clean_bbox:
            x1, y1, x2, y2 = last_clean_bbox
            cropped_patch = last_clean_img.crop((int(x1), int(y1), int(x2), int(y2)))
        else:
            cropped_patch = last_clean_img

        # ── Step 2: encode PIL patch → JPEG bytes ─────────────────────────
        jpeg_buf = io.BytesIO()
        cropped_patch.save(jpeg_buf, format="JPEG", quality=95)
        jpeg_buf.seek(0)
        jpeg_bytes = jpeg_buf.read()
        print(f"[WS] Image encoded to JPEG: {len(jpeg_bytes)} bytes")

        is_recapture_bool = str(is_recapture).strip().lower() in ("true", "1", "yes")

        target_url = RECAPTURE_SERVER_URL if is_recapture_bool else PROCESS_SERVER_URL
        endpoint_label = "/recapture" if is_recapture_bool else "/process"

        # ── Step 2: POST multipart to /process or /recapture ─────────────
        # All query params from the WebSocket connection are forwarded as
        # multipart form fields, exactly as the Android SDK would send them.
        multipart_data = {
            "token":              (None, token),
            "image_name":         (None, image_name),
            "ean_code":           (None, ean_code),
            "env_id":             (None, env_id),
            "metadata_id":        (None, metadata_id if metadata_id else ""),
            "client_name":        (None, client_name),
            "request_id":         (None, request_id),
            "capture_type":       (None, capture_type),
            "skip_quality_check": (None, "true"),
            "session_start":      (None, str(session_start)),
            "image":              (image_name, jpeg_bytes, "image/jpeg"),
        }

        print(
            f"[WS] POSTing to {target_url} (is_recapture={is_recapture_bool}) — "
            f"image_name={image_name}  ean={ean_code}  env={env_id}  client={client_name}"
        )

        async with httpx.AsyncClient(timeout=60.0) as client_http:
            resp = await client_http.post(
                target_url,
                files=multipart_data,
            )

        print(f"[WS] {endpoint_label} responded HTTP {resp.status_code}")
        process_result = resp.json()

        # ── Step 3: forward the /process response back over the WebSocket ─
        response = {
            "type":       "process_result",
            "status":     resp.status_code,
            "result":     process_result,
            "request_id": request_id,
        }
        try:
            await websocket.send_json(response)
            print("[WS] Process result sent to SDK")
        except Exception as send_exc:
            print(f"[WS] Could not send process result (SDK already disconnected): {send_exc}")

    except httpx.TimeoutException:
        print("[WS] /process call timed out")
        try:
            await websocket.send_json({"error": "Processing timed out — please retry"})
        except Exception:
            pass

    except Exception as exc:
        print(f"[WS] Pipeline error: {exc}")
        traceback.print_exc()
        try:
            await websocket.send_json({"error": f"Processing failed: {str(exc)}"})
        except Exception:
            pass

    finally:
        try:
            await websocket.close()
        except Exception:
            pass
        print("[WS] Session closed")

