from __future__ import annotations

from typing import List, Dict, Any
from PIL import Image
import os
import sys
import json
from pathlib import Path
import cv2
import numpy as np

# Dynamically add YOLOX to sys.path so we can import from it
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_YOLOX_DIR = _PROJECT_ROOT / "YOLOX"
if str(_YOLOX_DIR) not in sys.path:
    sys.path.insert(0, str(_YOLOX_DIR))

# Proper imports from YOLOX
from tools.demo import load_model

# Local configuration mapping to avoid depending on streaming_service
CONF_THRESHOLD = float(os.getenv("CROP_CONF_THRESHOLD", "0.32"))

# YOLOX specific paths from .env (fallback to default if not provided)
YOLOX_EXP_PATH = os.getenv("YOLOX_EXP_PATH", str(_YOLOX_DIR / "exps" / "example" / "custom" / "my_yoloxm.py"))
YOLOX_CKPT_PATH = os.getenv("YOLOX_CKPT_PATH", "src/models/NeoPatchCropper.pth")

_class_names_raw = json.loads(os.getenv("CLASS_NAMES", '{}'))
CLASS_NAMES = {int(k): v for k, v in _class_names_raw.items()}
# Fallback classes if .env fails or is missing
if not CLASS_NAMES:
    CLASS_NAMES = {
        0: "clean",
        1: "blur",
        2: "glare",
        3: "occlusion",
    }


class ImageClassifier:
    def __init__(self) -> None:
        self._predictor = None
        self._load()

    # ------------------------------------------------------------------
    # Startup
    # ------------------------------------------------------------------

    def _load(self) -> None:
        if not os.path.exists(YOLOX_EXP_PATH):
            raise FileNotFoundError(f"[Classifier] YOLOX exp file not found: {YOLOX_EXP_PATH}")
        if not os.path.exists(YOLOX_CKPT_PATH):
            raise FileNotFoundError(f"[Classifier] YOLOX ckpt file not found: {YOLOX_CKPT_PATH}")
            
        try:
            self._predictor = load_model(YOLOX_EXP_PATH, YOLOX_CKPT_PATH)
            # Update conf threshold to match environment variable
            self._predictor.confthre = CONF_THRESHOLD
            print(f"[Classifier] Model loaded from {YOLOX_CKPT_PATH}")
        except Exception as e:
            raise RuntimeError(f"[Classifier] Failed to load model: {e}")

    # ------------------------------------------------------------------
    # Inference
    # ------------------------------------------------------------------

    def predict(self, pil_image: Image.Image) -> List[Dict[str, Any]]:
        if self._predictor is None:
            return []

        try:
            # Convert PIL RGB to OpenCV BGR
            cv_img = cv2.cvtColor(np.array(pil_image), cv2.COLOR_RGB2BGR)
            
            outputs, img_info = self._predictor.inference(cv_img)
            
            # If no detections
            if outputs is None or outputs[0] is None:
                return []
                
            output = outputs[0].cpu()
            
            ratio = img_info["ratio"]
            bboxes = output[:, 0:4] / ratio
            cls_ids = output[:, 6]
            scores = output[:, 4] * output[:, 5]
            
            detections: List[Dict[str, Any]] = []
            
            for i in range(len(bboxes)):
                confidence = float(scores[i].item())
                if confidence < CONF_THRESHOLD:
                    continue
                    
                class_id = int(cls_ids[i].item())
                x1, y1, x2, y2 = [int(v.item()) for v in bboxes[i]]
                
                detections.append({
                    "class_id":   class_id,
                    "class_name": CLASS_NAMES.get(class_id, f"class_{class_id}"),
                    "confidence": round(confidence, 4),
                    "bbox":       [x1, y1, x2, y2],
                })
                
            # Sort highest confidence first
            detections.sort(key=lambda d: d["confidence"], reverse=True)
            return detections
            
        except Exception as exc:
            import traceback
            traceback.print_exc()
            print(f"[Classifier] Inference error: {exc}")
            return []
