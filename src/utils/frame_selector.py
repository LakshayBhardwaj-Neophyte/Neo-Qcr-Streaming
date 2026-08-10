"""
frame_selector.py
=================
Server-side frame quality pipeline.

Two-stage design:
  Stage 1 — decimate_to_1fps()
      Receives ALL buffered frames (each tagged with arrival timestamp in
      seconds from session-start).  Divides the timeline into 1-second
      integer buckets and keeps the SHARPEST frame in each bucket.
      Result: exactly 1 frame per second → e.g. 10 frames for a 10 s session.

  Stage 2 — select_best_frames()
      From the decimated list, scores every frame by Laplacian variance
      (higher = sharper / more in-focus) and returns the top-N.
      Fallback: if fewer than N frames survived decimation, returns all of
      them rather than erroring — the LLM can work with 1 or 2 patches.

Usage (called from ws_stream.py):
    decimated   = decimate_to_1fps(frame_buffer)        # frame_buffer: List[(ts, PIL.Image)]
    best_frames = select_best_frames(decimated, n=3)    # List[PIL.Image], len <= 3
"""

from __future__ import annotations

import cv2
import numpy as np
from PIL import Image
from typing import List, Tuple


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _laplacian_variance(pil_image: Image.Image) -> float:
    """
    Compute Laplacian variance of the image — the standard no-reference
    blur metric.  Higher value means sharper / better-focused image.
    """
    # Convert to greyscale numpy array (cheap, no copy if already RGB)
    arr  = np.array(pil_image.convert("L"))
    lap  = cv2.Laplacian(arr, cv2.CV_64F)
    return float(lap.var())


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def decimate_to_1fps(
    frames: List[Tuple[float, Image.Image]],
) -> List[Image.Image]:
    """
    Reduce a high-frequency frame buffer to 1 frame per second.

    Args:
        frames: List of (timestamp_seconds_from_session_start, PIL.Image)
                Frames may arrive at any rate (SDK sends ~2 fps / 500 ms).

    Returns:
        Ordered list of PIL.Images, one per occupied second bucket.
        E.g. 10 s session @ 2 fps → 20 raw frames → 10 decimated frames.

    Algorithm:
        1. Floor each timestamp to an integer second → bucket key.
        2. Within each bucket keep only the frame with the highest
           Laplacian variance (sharpest candidate for that second).
        3. Return frames in ascending bucket order.
    """
    if not frames:
        return []

    # bucket_key → (sharpness_score, PIL.Image)
    buckets: dict[int, Tuple[float, Image.Image]] = {}

    for ts, img in frames:
        bucket = int(ts)          # floor to integer second
        score  = _laplacian_variance(img)

        if bucket not in buckets or score > buckets[bucket][0]:
            buckets[bucket] = (score, img)

    # Return images sorted chronologically
    ordered = sorted(buckets.items())   # [(bucket_key, (score, img)), ...]
    result  = [img for _, (_, img) in ordered]

    print(
        f"[FrameSelector] decimate_to_1fps: "
        f"{len(frames)} raw frames → {len(result)} decimated (1 fps)"
    )
    return result


def select_best_frames(
    decimated_frames: List[Image.Image],
    n: int = 3,
) -> List[Image.Image]:
    """
    Pick the top-N sharpest frames from a 1-fps-decimated list.

    Args:
        decimated_frames: Output of decimate_to_1fps().
        n:                How many frames to select (default 3).

    Returns:
        Up to N PIL.Images sorted sharpest-first.
        FALLBACK: if len(decimated_frames) < N, returns all available frames
        (still sorted by sharpness) — never raises, never returns empty
        unless the input was empty.
    """
    if not decimated_frames:
        print("[FrameSelector] select_best_frames: no frames to score")
        return []

    scored = [
        (img, _laplacian_variance(img))
        for img in decimated_frames
    ]
    scored.sort(key=lambda x: x[1], reverse=True)

    top = scored[:n]
    selected = [img for img, _ in top]

    print(
        f"[FrameSelector] select_best_frames: "
        f"{len(decimated_frames)} decimated → {len(selected)} selected "
        f"(scores: {[f'{s:.1f}' for _, s in top]})"
    )
    return selected
