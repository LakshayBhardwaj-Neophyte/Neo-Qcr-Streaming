import asyncio
import base64
import copy
# import uuid
import jwt
from datetime import datetime, timezone
import io
import traceback
import threading
import time
from typing import Optional, Union, Any

from fastapi import FastAPI, HTTPException, Body, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.encoders import jsonable_encoder
from fastapi.security import OAuth2PasswordBearer
from fastapi.responses import JSONResponse
from fastapi.background import BackgroundTasks
from PIL import Image
from pydantic import BaseModel
from dotenv import load_dotenv
import os
from src.orchestrator.orchestrator_neoqcr import Orchestrator
from src.comms.server.ws_stream import stream_router
from src.utils.classifier import ImageClassifier

load_dotenv('src/configs/.env')
SECRET_KEY        = os.getenv("JWT_SECRET_KEY")
ALGORITHM         = os.getenv("JWT_ALGORITHM", "HS256")
SHIPSY_SECRET_KEY = os.getenv("SHIPSY_SECRET_KEY")
SHIPSY_ALGORITHM  = os.getenv("SHIPSY_ALGORITHM", "HS256")

# ---------------------------------------------------------------------------
# YOLO Quality Gate helper
# ---------------------------------------------------------------------------
# Class 0 = "real/clean".  All other classes indicate an unusable image.
YOLO_CLEAN_CLASS  = "real/clean"
YOLO_CLASS_LABELS = {
    0: "real/clean",
    1: "blur",
    2: "dark",
    3: "glare",
    4: "occlusion",
    5: "perspective",
    6: "rotation",
}


async def _yolo_quality_check(classifier, pil_image: Image.Image) -> dict:
    """
    Run the YOLO classifier on the image (non-blocking via executor).

    Returns:
        {
            "accept":     bool,    # True only when top class == "real/clean"
            "class_name": str,     # top detected class name
            "confidence": float,   # confidence of top detection
            "message":    str,     # human-readable rejection reason (or None)
        }
    """
    loop = asyncio.get_event_loop()
    detections = await loop.run_in_executor(None, classifier.predict, pil_image)

    if not detections:
        # No detection at all — treat as clean (model found nothing wrong)
        return {"accept": True, "class_name": "real/clean", "confidence": 1.0, "message": None}

    top = detections[0]   # sorted highest-confidence first
    class_name = top.get("class_name", "unknown")
    confidence = top.get("confidence", 0.0)
    is_clean   = class_name == YOLO_CLEAN_CLASS

    message = None if is_clean else (
        f"Image quality check failed: detected '{class_name}' "
        f"(confidence {confidence:.2f}) — please retake the image"
    )
    return {
        "accept":     is_clean,
        "class_name": class_name,
        "confidence": confidence,
        "message":    message,
    }


# ---------------------------------------------------------------------------
# JWT helpers  (unchanged)
# ---------------------------------------------------------------------------

class InvalidClaimError(Exception):
    pass


def _pick_jwt_key(client_name: str):
    if (client_name or "").strip().lower() == "shipsy":
        return SHIPSY_SECRET_KEY, SHIPSY_ALGORITHM
    return SECRET_KEY, ALGORITHM


def _detect_client_from_token(token: str) -> str:
    if isinstance(token, str) and token.lower().startswith("bearer "):
        token = token[7:].strip()
    try:
        jwt.decode(token, SHIPSY_SECRET_KEY, algorithms=[SHIPSY_ALGORITHM],
                   audience="valid-audience", issuer="trusted-issuer")
        return "shipsy"
    except Exception:
        pass
    try:
        jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM],
                   audience="valid-audience", issuer="trusted-issuer")
        return "reliance"
    except Exception:
        pass
    raise ValueError("Token could not be verified with any known client key")


def validate_jwt_headers(token):
    header = jwt.get_unverified_header(token)
    if header.get("alg") == "none":
        raise Exception("Insecure algorithm: none")
    if "jku" in header or "jwk" in header:
        raise Exception("Header contains forbidden parameters")
    if "kid" in header and "../" in header["kid"]:
        raise Exception("Path traversal detected in kid")


def verify_claims(payload):
    if payload.get("iss") != "trusted-issuer":
        raise InvalidClaimError("Invalid issuer")
    if payload.get("aud") != "valid-audience":
        raise InvalidClaimError("Invalid audience")
    if datetime.fromtimestamp(payload["exp"], tz=timezone.utc) <= datetime.now(timezone.utc):
        raise InvalidClaimError("Token expired")


def test_jwt_token(token, secret_key=None, algorithm=None):
    if secret_key is None:
        secret_key = SECRET_KEY
    if algorithm is None:
        algorithm = ALGORITHM
    if isinstance(token, str) and token.lower().startswith("bearer "):
        token = token[7:].strip()
    print(f"[DEBUG token] len={len(token) if token else 0}  segments={len(token.split('.')) if token else 0}  preview={repr(token[:40]) if token else repr(token)}")
    try:
        validate_jwt_headers(token)
        payload = jwt.decode(token, secret_key, algorithms=[algorithm],
                             audience="valid-audience", issuer="trusted-issuer")
        verify_claims(payload)
        print("✅ Token is valid and passes all checklist tests.")
    except Exception as e:
        print(f"❌ Token validation failed: {e}")
        return JSONResponse(
            status_code=400,
            content={"message": "Token validation failed"}
        )


# ---------------------------------------------------------------------------
# Request models  (unchanged)
# ---------------------------------------------------------------------------

class Payload(BaseModel):
    image_name:   str
    image:        str                            # base64
    ean_code:     Optional[str]  = None
    env_id:       str
    metadata_id:  Optional[str]  = None
    capture_type: Optional[Union[bool, str]] = None
    client_name:  Optional[str]  = "reliance"
    token:        str
    pid:          Optional[str]  = None
    request_id:   Optional[str]  = None


class UserRequest(BaseModel):
    token:      str
    request_id: str


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------

class SdkAPI:
    def __init__(self):
        print("Initializing FastAPI application...")
        print("code updated on 11th apr.....")
        self.orchestrator = Orchestrator()

        self.classifier = ImageClassifier()

        self.app = FastAPI()

        self.app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

        # Attach shared objects to app state so WebSocket handlers can access them
        self.app.state.orchestrator  = self.orchestrator
        self.app.state.classifier    = self.classifier   # YOLO quality gate

        # Mount WebSocket streaming router
        self.app.include_router(stream_router)

        self._setup_routes()

    # ------------------------------------------------------------------
    # Image conversion helper  (unchanged)
    # ------------------------------------------------------------------

    @staticmethod
    def _to_pil(image_data: Union[str, bytes]) -> Image.Image:
        try:
            raw = base64.b64decode(image_data) if isinstance(image_data, str) else image_data
            img = Image.open(io.BytesIO(raw))
            if img.mode != "RGB":
                img = img.convert("RGB")
            print(f"[Image] {img.size} (w,h)  mode={img.mode}")
            return img
        except Exception as exc:
            raise ValueError(f"Invalid image data: {exc}")

    # ------------------------------------------------------------------
    # Shared form / JSON parsing helpers  (unchanged)
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_capture_type(capture_raw: Any) -> Optional[bool]:
        if capture_raw is None:
            return None
        if isinstance(capture_raw, bool):
            return capture_raw
        if isinstance(capture_raw, str):
            return capture_raw.strip().lower() in ("true", "multicapture", "1")
        return bool(capture_raw)

    async def _parse_process_request(self, request: Request):
        content_type = request.headers.get("content-type", "")

        if "multipart/form-data" in content_type:
            form         = await request.form()
            image_file   = form.get("image")
            img_bytes    = await image_file.read()
            pil_image    = self._to_pil(img_bytes)
            capture_raw  = form.get("capture_type")
            capture_type = self._parse_capture_type(capture_raw)
            return {
                "image_name":   form.get("image_name"),
                "pil_image":    pil_image,
                "ean_code":     form.get("ean_code"),
                "env_id":       form.get("env_id"),
                "metadata_id":  form.get("metadata_id"),
                "capture_type": capture_type,
                "client_name":  (form.get("client_name") or "reliance").strip().lower(),
                "token":        form.get("token"),
                "pid":          form.get("pid"),
                "request_id":   form.get("request_id"),
                "skip_quality_check": str(form.get("skip_quality_check")).lower() == "true",
                "session_start": form.get("session_start"),
            }

        if "application/json" in content_type:
            payload   = Payload(**(await request.json()))
            pil_image = self._to_pil(payload.image)
            capture_type = self._parse_capture_type(payload.capture_type)
            return {
                "image_name":   payload.image_name,
                "pil_image":    pil_image,
                "ean_code":     payload.ean_code,
                "env_id":       payload.env_id,
                "metadata_id":  payload.metadata_id,
                "capture_type": capture_type,
                "client_name":  (payload.client_name or "reliance").strip().lower(),
                "token":        payload.token,
                "pid":          payload.pid,
                "request_id":   payload.request_id,
                "skip_quality_check": False, # JSON doesn't typically come from internal stream
                "session_start": None,
            }

        raise ValueError("Unsupported content-type — use application/json or multipart/form-data")

    # ------------------------------------------------------------------
    # Background save helper  (unchanged)
    # ------------------------------------------------------------------

    def _save_metadata_bg(
        self,
        image_name:        str,
        ean_code:          Optional[str],
        env_id:            str,
        store_id:          str,
        client_name:       str,
        snapshot:          dict,
        rec_metadata_id:   Optional[str] = None,
        skip_image_upload: bool          = False,
        pid:               Optional[str] = None,
        request_id:        Optional[str] = None,
        scan_duration:     Optional[float] = None,
    ) -> None:
        try:
            print(f"[BG save] image={image_name}  skip_upload={skip_image_upload}  pid={pid}")
            self.orchestrator.database.set_tenant(client_name)
            self.orchestrator.process_image_save_metadata(
                image_name        = image_name,
                ean_code          = ean_code,
                env_id            = env_id,
                store_id          = store_id,
                snapshot          = snapshot,
                rec_metadata_id   = rec_metadata_id,
                recapture_flag    = rec_metadata_id is not None,
                client_name       = client_name,
                skip_image_upload = skip_image_upload,
                pid               = pid,
                request_id        = request_id,
                scan_duration     = scan_duration,
            )
            print("[BG save] Done")
        except Exception as exc:
            print(f"[BG save] Error: {exc}")
            traceback.print_exc()

    def _save_manual_entry_bg(
        self,
        image_name:  str,
        ean_code:    Optional[str],
        env_id:      str,
        store_id:    str,
        client_name: str,
        manual_data: dict,
        url_holder:  dict,
        request_id:  Optional[str] = None,
        pid:         Optional[str]  = None,
    ) -> None:
        """
        Background save for /manual-entry.

        Builds a metadata doc that exactly matches the production format:
          - mrp stored as float (not string)
          - mfg_date / expiry_date stored as datetime objects (MongoDB $date)
          - barcode_no mirrors the ean field
          - manual_entry: True flag added
          - predicted block mirrors the top-level values
        """
        import time as _time
        from datetime import datetime as _dt
        from src.utils.image_processor import ImageProcessor
        from src.utils.metadata_processor import MetadataProcessor

        def _parse_date(val):
            """Convert DD-MM-YYYY string → datetime. Returns None on failure."""
            if val is None:
                return None
            if isinstance(val, _dt):
                return val
            s = str(val).strip()
            for fmt in ("%d-%m-%Y", "%Y-%m-%d", "%m-%d-%Y", "%d/%m/%Y"):
                try:
                    return _dt.strptime(s, fmt)
                except ValueError:
                    continue
            print(f"[BG manual-save] Could not parse date: {val!r}")
            return None

        def _parse_mrp(val):
            """Convert mrp string → float. Returns None on failure."""
            try:
                return float(val)
            except (TypeError, ValueError):
                return val

        try:
            print(f"[BG manual-save] image={image_name}  env={env_id}  client={client_name}")
            self.orchestrator.database.set_tenant(client_name)

            # Wait for parallel image upload (max 10 s)
            for _ in range(100):
                if url_holder.get("complete"):
                    break
                _time.sleep(0.1)
            img_url = url_holder.get("img_url")

            # ── Convert types to match production metadata format ──────────
            mrp_val    = _parse_mrp(manual_data.get("mrp"))
            mfg_dt     = _parse_date(manual_data.get("mfg_date"))
            expiry_dt  = _parse_date(manual_data.get("expiry_date"))
            batch_no   = manual_data.get("batch_no")
            metadata_id = manual_data.get("metadata_id")
            qty        = manual_data.get("qty", 1)

            # ── Build the DB document ─────────────────────────────────────
            db_doc = {
                "img_url":        img_url,
                "ean":            ean_code,
                "mrp":            mrp_val,          # float, e.g. 78.0
                "mfg_date":       mfg_dt,           # datetime object → stored as $date
                "expiry_date":    expiry_dt,         # datetime object → stored as $date
                "batch_no":       batch_no,
                "ocr_raw_output": "manual-entry",
                "env_id":         env_id,
                "present":        True,
                "metadata_id":    metadata_id,
                "qty":            int(qty) if qty else 1,
                "barcode_no":     ean_code,          # mirrors ean, matches production format
                "manual_entry":   True,              # ← flag to identify manual entries
                "client_name":    client_name,
                "storage":        "azure" if client_name.lower() in ("reliance", "shipsy") else "e2e",
                "predicted": {
                    "mrp":         mrp_val,
                    "mfg_date":    mfg_dt,
                    "expiry_date": expiry_dt,
                    "batch_no":    batch_no,
                },
            }
            if pid is not None:
                db_doc["pid"] = pid

            # ── Create env doc if it doesn't exist yet ────────────────────
            _, user_id, device_id, _ = ImageProcessor.extract_img_details(image_name)
            if not self.orchestrator.database.check_env_doc(env_id):
                env_data = MetadataProcessor.create_env_data(store_id, device_id, user_id, env_id)
                if env_data:
                    env_data["client_name"] = client_name
                    if request_id:
                        env_data["session_id"] = request_id
                    self.orchestrator.database.create_env_doc(env_data)
            elif request_id:
                self.orchestrator.database.save_session(request_id, env_id)

            # ── Write metadata doc ────────────────────────────────────────
            created_id = self.orchestrator.database.create_metadata(
                db_doc, ean_code, env_id, metadata_id
            )
            print(
                f"[BG manual-save] Metadata {created_id} created  "
                f"mrp={mrp_val}  mfg={mfg_dt}  exp={expiry_dt}  img_url={img_url}"
            )

        except Exception as exc:
            print(f"[BG manual-save] Error: {exc}")
            traceback.print_exc()

    # ------------------------------------------------------------------
    # Routes
    # ------------------------------------------------------------------

    def _setup_routes(self):

        # ---- /health --------------------------------------------------
        @self.app.get("/health")
        def health():
            return {"ok": True}

        # ---- /process -------------------------------------------------
        @self.app.post("/process")
        async def process_image(request: Request):
            try:
                req = await self._parse_process_request(request)
            except ValueError as exc:
                return JSONResponse(status_code=400, content={"message": str(exc)})

            try:
                image_name   = req["image_name"]
                pil_image    = req["pil_image"]
                ean_code     = req["ean_code"]
                env_id       = req["env_id"]
                metadata_id  = req["metadata_id"]
                capture_type = req["capture_type"]
                client_name  = req["client_name"]
                token        = req["token"]
                pid          = req.get("pid")

                if not token:
                    return JSONResponse(status_code=400, content={"message": "Token is missing"})

                secret_key, algo = _pick_jwt_key(client_name)
                auth_error = test_jwt_token(token, secret_key, algo)
                if auth_error:
                    return auth_error

                if not ean_code:
                    return JSONResponse(status_code=401, content={"message": "ean_code is required"})

                print(f"[/process] image={image_name}  ean={ean_code}  env={env_id}  "
                      f"capture_type={capture_type}  client={client_name}")

                # ── YOLO QUALITY GATE ────────────────────────────────────────
                # Runs BEFORE OCR. Rejects blurry / dark / glare / etc. images
                # so the SDK can immediately ask for a retake.
                if not req.get("skip_quality_check") and not req.get("capture_type"):
                    qc = await _yolo_quality_check(self.classifier, pil_image)
                    print(
                        f"[/process] quality_check → accept={qc['accept']}  "
                        f"class={qc['class_name']}  confidence={qc['confidence']:.2f}"
                    )

                    if not qc["accept"]:
                        return JSONResponse(status_code=200, content={
                            "quality_check": {
                                "accept":     False,
                                "class_name": qc["class_name"],
                                "confidence": qc["confidence"],
                                "message":    qc["message"],
                            }
                        })
                # ── END YOLO QUALITY GATE ────────────────────────────────────

                self.orchestrator.database.set_tenant(client_name)

                user_id = image_name.split("_")[1]
                user_ok, store_id = self.orchestrator.database.user_validation(user_id)
                if not user_ok:
                    return JSONResponse(status_code=400, content={"message": "Invalid user"})

                img_token   = image_name.split("_")[3].split(".jpg")[0]
                metadata_id = metadata_id or f"{env_id}{img_token}"

                result = await self.orchestrator.process_image_get_metadata(
                    image_data   = pil_image,
                    env_id       = env_id,
                    capture_type = capture_type,
                    metadata_id  = metadata_id,
                    client_name  = client_name,
                    user_id      = user_id,
                )

                metadata = result["metadata"]
                snapshot = result["snapshot"]
                
                # Calculate latency if session_start is provided
                session_start = req.get("session_start")
                scan_duration = None
                if session_start:
                    scan_duration = time.monotonic() - float(session_start)

                print(f"[/process] env_id={env_id}  capture_type={capture_type}  metadata={metadata}")

                metadata["qty"]         = 1
                metadata["metadata_id"] = metadata_id

                if isinstance(snapshot.get("data"), dict):
                    snapshot["data"]["qty"] = 1

                print(f"[/process] metadata={metadata}")

                request_id = req.get("request_id")

                if client_name == "shipsy":
                    if not request_id:
                        return JSONResponse(status_code=400,
                                            content={"message": "request_id is required for shipsy"})
                    print(f"[/process] Shipsy request_id received from SDK: {request_id}")

                elif client_name == "reliance":
                    if not request_id:
                        return JSONResponse(status_code=400,
                                            content={"message": "request_id is required for reliance"})
                    print(f"[/process] Reliance request_id received from SDK: {request_id}")

                skip_upload = client_name != "reliance"
                threading.Thread(
                    target = self._save_metadata_bg,
                    kwargs = {
                        "image_name":        image_name,
                        "ean_code":          ean_code,
                        "env_id":            env_id,
                        "store_id":          store_id,
                        "client_name":       client_name,
                        "snapshot":          snapshot,
                        "rec_metadata_id":   None,
                        "skip_image_upload": skip_upload,
                        "pid":               pid,
                        "request_id":        request_id,
                        "scan_duration":     scan_duration,
                    },
                    daemon=True,
                ).start()

                resp_content = {"metadata": metadata}
                if request_id:
                    resp_content["request_id"] = request_id

                return JSONResponse(status_code=200, content=resp_content)

            except Exception as exc:
                print(f"[/process] Error: {exc}")
                traceback.print_exc()
                raise HTTPException(status_code=500, detail=str(exc))

        # ---- /recapture -----------------------------------------------
        @self.app.post("/recapture")
        async def recapture_image(request: Request):
            try:
                req = await self._parse_process_request(request)
            except ValueError as exc:
                return JSONResponse(status_code=400, content={"message": str(exc)})

            try:
                image_name      = req["image_name"]
                pil_image       = req["pil_image"]
                ean_code        = req["ean_code"]
                env_id          = req["env_id"]
                rec_metadata_id = req["metadata_id"]
                client_name     = req["client_name"]
                token           = req["token"]
                pid             = req.get("pid")

                if not token:
                    return JSONResponse(status_code=400, content={"message": "Token is missing"})

                secret_key, algo = _pick_jwt_key(client_name)
                auth_error = test_jwt_token(token, secret_key, algo)
                if auth_error:
                    return auth_error

                print(f"[/recapture] image={image_name}  ean={ean_code}  env={env_id}  "
                      f"old_id={rec_metadata_id}  client={client_name}")

                # ── YOLO QUALITY GATE ────────────────────────────────────────
                if not req.get("skip_quality_check"):
                    qc = await _yolo_quality_check(self.classifier, pil_image)
                    print(
                        f"[/recapture] quality_check → accept={qc['accept']}  "
                        f"class={qc['class_name']}  confidence={qc['confidence']:.2f}"
                    )

                    if not qc["accept"]:
                        return JSONResponse(status_code=200, content={
                            "quality_check": {
                                "accept":     False,
                                "class_name": qc["class_name"],
                                "confidence": qc["confidence"],
                                "message":    qc["message"],
                            }
                        })
                # ── END YOLO QUALITY GATE ────────────────────────────────────

                self.orchestrator.database.set_tenant(client_name)

                user_id = image_name.split("_")[1]
                user_ok, store_id = self.orchestrator.database.user_validation(user_id)
                if not user_ok:
                    return JSONResponse(status_code=400, content={"message": "Invalid user"})

                img_token       = image_name.split("_")[3].split(".jpg")[0]
                new_metadata_id = f"{env_id}{img_token}"

                result = await self.orchestrator.process_image_get_metadata(
                    image_data   = pil_image,
                    env_id       = env_id,
                    capture_type = False,
                    metadata_id  = new_metadata_id,
                    client_name  = client_name,
                    user_id      = user_id,
                )

                metadata = result["metadata"]
                snapshot = result["snapshot"]

                metadata["qty"]         = 1
                metadata["metadata_id"] = new_metadata_id

                if isinstance(snapshot.get("data"), dict):
                    snapshot["data"]["qty"] = 1

                print(f"[/recapture] metadata={metadata}")

                request_id = req.get("request_id")

                if client_name == "shipsy":
                    if not request_id:
                        return JSONResponse(status_code=400,
                                            content={"message": "request_id is required for shipsy"})
                    print(f"[/recapture] Shipsy request_id received from SDK: {request_id}")

                elif client_name == "reliance":
                    if not request_id:
                        return JSONResponse(status_code=400,
                                            content={"message": "request_id is required for reliance"})
                    print(f"[/recapture] Reliance request_id received from SDK: {request_id}")

                skip_upload = client_name != "reliance"
                threading.Thread(
                    target = self._save_metadata_bg,
                    kwargs = {
                        "image_name":        image_name,
                        "ean_code":          ean_code,
                        "env_id":            env_id,
                        "store_id":          store_id,
                        "client_name":       client_name,
                        "snapshot":          snapshot,
                        "rec_metadata_id":   rec_metadata_id,
                        "skip_image_upload": skip_upload,
                        "pid":               pid,
                        "request_id":        request_id,
                    },
                    daemon=True,
                ).start()

                resp_content = {"metadata": metadata}
                if request_id:
                    resp_content["request_id"] = request_id

                return JSONResponse(status_code=200, content=resp_content)

            except Exception as exc:
                print(f"[/recapture] Error: {exc}")
                traceback.print_exc()
                raise HTTPException(status_code=500, detail=str(exc))

        # ---- /get_session_data ----------------------------------------
        @self.app.post("/get_session_data")
        def get_session_data(payload: UserRequest):
            try:
                if not payload.token:
                    return JSONResponse(status_code=400, content={"message": "Token is missing"})

                try:
                    client = _detect_client_from_token(payload.token)
                except ValueError:
                    return JSONResponse(status_code=401,
                                        content={"message": "Token could not be verified"})

                print(f"[/get_session_data] client={client}  request_id={payload.request_id}")

                if client == "reliance":
                    auth_error = test_jwt_token(payload.token, SECRET_KEY, ALGORITHM)
                    if auth_error:
                        return auth_error
                    self.orchestrator.database.set_tenant("reliance")
                    meta = self.orchestrator.database.get_session(payload.request_id,
                                                                   expected_client="reliance")
                    if meta == "CLIENT_MISMATCH":
                        return JSONResponse(status_code=401,
                                            content={"message": "Token client does not match session owner"})
                    if meta is None:
                        return JSONResponse(status_code=400,
                                            content={"message": "request_id not found or expired"})
                    return JSONResponse(status_code=200, content={"metadata": meta})

                if client == "shipsy":
                    auth_error = test_jwt_token(payload.token, SHIPSY_SECRET_KEY, SHIPSY_ALGORITHM)
                    if auth_error:
                        return auth_error
                    self.orchestrator.database.set_tenant("shipsy")
                    meta = self.orchestrator.database.get_session(payload.request_id,
                                                                   expected_client="shipsy")
                    if meta == "CLIENT_MISMATCH":
                        return JSONResponse(status_code=401,
                                            content={"message": "Token client does not match session owner"})
                    if meta is None:
                        return JSONResponse(status_code=400,
                                            content={"message": "request_id not found or expired"})
                    return JSONResponse(status_code=200, content={"metadata": meta})

                return JSONResponse(status_code=401, content={"message": "Unknown client"})

            except Exception as exc:
                print(f"[/get_session_data] Error: {exc}")
                traceback.print_exc()
                raise HTTPException(status_code=500, detail=str(exc))

        # ---- /update_data ---------------------------------------------
        @self.app.post("/update_data")
        async def update_key_value(
            key_value:   dict = Body(...),
            env_id:      str  = Body(...),
            token:       str  = Body(...),
            client_name: str  = Body("reliance"),
            ean_code:    str  = Body(None),
            request_id:  str  = Body(None)
        ):
            try:
                if not token:
                    return JSONResponse(status_code=400, content={"message": "Token is missing"})

                secret_key, algo = _pick_jwt_key(client_name)
                auth_error = test_jwt_token(token, secret_key, algo)
                if auth_error:
                    return auth_error

                print(f"[/update_data] env_id={env_id}  key_value={key_value}")
                if ean_code:
                    key_value["ean"] = ean_code

                self.orchestrator.database.set_tenant(client_name)

                ok = self.orchestrator.database.update_value(env_id, key_value)
                if ok:
                    return JSONResponse(status_code=200, content={"message": "Data updated successfully"})
                return JSONResponse(status_code=400, content={"message": "Failed to update data."})

            except Exception as exc:
                print(f"[/update_data] Error: {exc}")
                traceback.print_exc()
                raise HTTPException(status_code=500, detail=str(exc))

        # ---- /manual-entry --------------------------------------------
        # Accepts product attributes typed/scanned by a human operator.
        # Image is optional — if provided it is uploaded to blob storage
        # but is NEVER sent to the OCR model.
        # Creates env doc + metadata doc the same way /process does.
        # ---------------------------------------------------------------
        @self.app.post("/manual-entry")
        async def manual_entry(request: Request):
            try:
                content_type = request.headers.get("content-type", "")

                # ── Parse request (multipart or JSON) ──────────────────────
                if "multipart/form-data" in content_type:
                    _raw_form = await request.form()
                    # Normalize keys — strips accidental leading/trailing spaces
                    form = {k.strip(): v for k, v in _raw_form.items()}
                    token      = form.get("token")
                    client_name = (form.get("client_name") or "reliance").strip().lower()
                    env_id     = form.get("env_id")
                    image_name = form.get("image_name")
                    ean_code   = form.get("ean_code") or form.get("ean")
                    mrp        = form.get("mrp")
                    mfg_date   = form.get("mfg_date") or form.get("mfd")
                    expiry_date = form.get("expiry_date") or form.get("exp")
                    batch_no   = form.get("batch_no")
                    qty        = form.get("qty", 1)
                    metadata_id = form.get("metadata_id")
                    request_id = form.get("request_id")
                    pid        = form.get("pid")

                    # Optional image
                    image_file = form.get("image")
                    pil_image  = None
                    if image_file and hasattr(image_file, "read"):
                        img_bytes = await image_file.read()
                        if img_bytes:
                            pil_image = self._to_pil(img_bytes)

                elif "application/json" in content_type:
                    body       = await request.json()
                    token      = body.get("token")
                    client_name = (body.get("client_name") or "reliance").strip().lower()
                    env_id     = body.get("env_id")
                    image_name = body.get("image_name")
                    ean_code   = body.get("ean_code") or body.get("ean")
                    mrp        = body.get("mrp")
                    mfg_date   = body.get("mfg_date") or body.get("mfd")
                    expiry_date = body.get("expiry_date") or body.get("exp")
                    batch_no   = body.get("batch_no")
                    qty        = body.get("qty", 1)
                    metadata_id = body.get("metadata_id")
                    request_id = body.get("request_id")
                    pid        = body.get("pid")
                    pil_image  = None  # JSON path: no binary image

                else:
                    return JSONResponse(
                        status_code=400,
                        content={"message": "Unsupported content-type — use application/json or multipart/form-data"},
                    )

                # ── Auth ───────────────────────────────────────────────────
                if not token:
                    return JSONResponse(status_code=400, content={"message": "Token is missing"})

                secret_key, algo = _pick_jwt_key(client_name)
                auth_error = test_jwt_token(token, secret_key, algo)
                if auth_error:
                    return auth_error

                # ── Required fields validation ──────────────────────────────
                missing = [f for f, v in {
                    "env_id":     env_id,
                    "image_name": image_name,
                    "ean_code":   ean_code,
                }.items() if not v]
                if missing:
                    return JSONResponse(
                        status_code=400,
                        content={"message": f"Missing required fields: {', '.join(missing)}"},
                    )

                print(
                    f"[/manual-entry] image={image_name}  ean={ean_code}  env={env_id}  "
                    f"mrp={mrp}  mfg={mfg_date}  exp={expiry_date}  batch={batch_no}  "
                    f"qty={qty}  client={client_name}"
                )

                # ── User + store validation (reuse existing DB helper) ──────
                self.orchestrator.database.set_tenant(client_name)

                user_id = image_name.split("_")[1]
                user_ok, store_id = self.orchestrator.database.user_validation(user_id)
                if not user_ok:
                    return JSONResponse(status_code=400, content={"message": "Invalid user"})

                # ── Build stable metadata_id ────────────────────────────────
                img_token   = image_name.split("_")[3].split(".jpg")[0]
                metadata_id = metadata_id or f"{env_id}{img_token}"

                # ── Build the data dict from the manually provided values ───
                manual_data = {
                    "mrp":         mrp,
                    "mfg_date":    mfg_date,
                    "expiry_date": expiry_date,
                    "batch_no":    batch_no,
                    "ean":         ean_code,
                    "qty":         int(qty) if qty else 1,
                    "metadata_id": metadata_id,
                }

                # ── Assemble snapshot for the background save thread ────────
                # url_holder is used if there is a parallel image upload below;
                # for JSON-only requests (no image) it stays empty.
                url_holder: dict = {"img_url": None, "cdn_url": None, "complete": False}

                if pil_image is not None:
                    # Start image upload in background so the response is fast
                    from src.orchestrator.orchestrator_neoqcr import build_blob_name

                    image_copy = pil_image.copy()

                    def _upload_image():
                        try:
                            blob_name = build_blob_name(metadata_id, client_name=client_name, user_id=user_id)
                            result    = self.orchestrator.storage.upload_image(
                                image_copy, blob_name, client_name, user_id
                            )
                            if isinstance(result, dict):
                                url_holder["img_url"] = result.get("public_url")
                                url_holder["cdn_url"]  = result.get("cdn_url")
                            else:
                                url_holder["img_url"] = result
                            url_holder["complete"] = True
                            print(f"[/manual-entry] Image uploaded → {url_holder['img_url']}")
                        except Exception as exc:
                            print(f"[/manual-entry] Image upload failed: {exc}")
                            traceback.print_exc()
                            url_holder["complete"] = True  # unblock save thread

                    threading.Thread(target=_upload_image, daemon=True).start()
                else:
                    url_holder["complete"] = True  # no image, nothing to wait for


                # ── Fire-and-forget DB save (dedicated manual-entry helper) ──
                # Uses _save_manual_entry_bg instead of _save_metadata_bg so that
                # the operator-supplied dates are NOT overwritten with None by the
                # OCR date-parsing step inside process_image_save_metadata.
                threading.Thread(
                    target = self._save_manual_entry_bg,
                    kwargs = {
                        "image_name":  image_name,
                        "ean_code":    ean_code,
                        "env_id":      env_id,
                        "store_id":    store_id,
                        "client_name": client_name,
                        "manual_data": manual_data,
                        "url_holder":  url_holder,
                        "request_id":  request_id,
                        "pid":         pid,
                    },
                    daemon=True,
                ).start()

                # ── Return immediately — same shape as /process ─────────────
                resp_content = {"metadata": manual_data}
                if request_id:
                    resp_content["request_id"] = request_id

                return JSONResponse(status_code=200, content=resp_content)

            except Exception as exc:
                print(f"[/manual-entry] Error: {exc}")
                traceback.print_exc()
                raise HTTPException(status_code=500, detail=str(exc))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    sdk = SdkAPI()
    uvicorn.run(sdk.app, host="0.0.0.0", port=8001)

