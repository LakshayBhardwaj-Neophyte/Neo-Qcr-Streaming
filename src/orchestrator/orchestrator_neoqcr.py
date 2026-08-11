import copy
import time
import asyncio
import threading
import traceback
from typing import Optional, Dict, List, Any, Tuple

from PIL import Image

# from src.core.think.machine_learning.computer_vision.text_analytics.Vllm_inference import Vllm_inference
from src.core.think.machine_learning.computer_vision.text_analytics.qwen_api_client import Vllm_inference
from src.core.think.machine_learning.computer_vision.text_analytics.key_value_extraction.regex_module.mrp_regex import Get_mrp
from src.core.think.machine_learning.computer_vision.text_analytics.key_value_extraction.regex_module.date_regex import GetDates
from src.core.think.machine_learning.computer_vision.text_analytics.key_value_extraction.regex_module.batch_no_regex import GetBatchno

from src.utils.datetime import Datetime
from src.data_handler.mongo import Mongo
from src.utils.metadata_processor import MetadataProcessor
from src.utils.image_processor import ImageProcessor
from src.utils.storage_router import StorageRouter
from src.utils.monitoring import time_calculate
from src.utils.session_cache import SessionCache

from dotenv import load_dotenv
load_dotenv('src/configs/.env')


# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------

class ModelInitializationError(Exception):
    pass

class ProcessingError(Exception):
    pass

class DatabaseError(Exception):
    pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def build_blob_name(
    final_metadata_id: str,
    plotted: bool = False,
    ext: str = "jpg",
    client_name: Optional[str] = None,
    user_id: Optional[str] = None,
) -> str:
    filename = f"{final_metadata_id}{'_plotted' if plotted else ''}.{ext.lower()}"
    if client_name and user_id and client_name.lower() in [
        "reliance",
        "trends-beauty",
        "trends_beauty",
        "shipsy"
    ]:
        return f"{client_name}/{user_id}/{filename}"
    return filename


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

class Orchestrator:
    """
    Singleton service object.  Only truly shared, immutable state lives on
    self (models, db connection, utils).  Every request carries its own data
    through local variables and explicit return values — nothing request-
    specific is ever written to self.
    """

    def __init__(self):
        try:
            print("Initializing Orchestrator...")
            self._init_models()
            self._init_database()
            self._init_utils()
            self.session_cache = SessionCache()
            self.total_ocr_time = 0.0
            print("Orchestrator initialization completed successfully")
        except Exception as e:
            raise ModelInitializationError(f"Failed to initialize Orchestrator: {str(e)}")

    # ------------------------------------------------------------------
    # Init helpers
    # ------------------------------------------------------------------

    def _init_models(self) -> None:
        try:
            print("Loading model...")
            model_path = "src/models/checkpoint-5200_aug_25"
            start = time.time()
            # print(f"model path :: {model_path}")
            # self.qwen_model = Vllm_inference(model_path, image_dir=None, output_csv=None)
            self.qwen_model = Vllm_inference(api_url="http://neo-qcr-model:8093")
            self.qwen_model.initialize()
            print(f"model loaded in {time.time() - start:.2f}s")

            print("Initializing regex modules...")
            self.getmrp = Get_mrp()
            self.getdate = GetDates()
            self.getbatchno = GetBatchno()
        except Exception as e:
            raise ModelInitializationError(f"Failed to initialize models: {str(e)}")

    def _init_database(self) -> None:
        try:
            print("Connecting to database...")
            self.database = Mongo()
        except Exception as e:
            raise DatabaseError(f"Failed to connect to database: {str(e)}")

    def _init_utils(self) -> None:
        try:
            print("Initializing utils...")
            self.str_to_datetime = Datetime()
            self.image_processor = ImageProcessor()
            self.storage = StorageRouter()
        except Exception as e:
            raise ModelInitializationError(f"Failed to initialize utilities: {str(e)}")

    # ------------------------------------------------------------------
    # Internal helpers  (stateless — no self writes)
    # ------------------------------------------------------------------

    def _print_image_info(self, label: str, img: Any) -> None:
        if isinstance(img, Image.Image):
            print(f"[{label}] PIL image size: {img.size} (w x h)")
        elif hasattr(img, "shape"):
            print(f"[{label}] OpenCV image shape: {img.shape}")
        else:
            print(f"[{label}] Unknown image type: {type(img)}")

    @time_calculate()
    def _extract_metadata(
        self, ocr_result: List[str]
    ) -> Tuple[Optional[str], List[Optional[str]], Optional[str]]:
        try:
            mrp      = self.getmrp.process_mrp(str(ocr_result[0]))
            dates    = self.getdate.process_dates(ocr_result)
            batch_no = self.getbatchno.get_batch_number(str(ocr_result[0]))
            print(f"\nMfg/exp dates: {dates}\n")
            return mrp, dates, batch_no
        except Exception as e:
            raise ProcessingError(f"Metadata extraction failed: {str(e)}")

    @time_calculate()
    def get_processed_data(self, data: Dict, env_id: str) -> Dict:
        return self.database.sdk_data_process(data, env_id)

    # ------------------------------------------------------------------
    # Core processing  — returns a self-contained result bundle.
    # NOTHING is written to self.  The caller owns the snapshot.
    # ------------------------------------------------------------------

    @time_calculate()
    async def process_image_get_metadata(
        self,
        image_data: Any,
        env_id: str,
        capture_type: Optional[bool],
        metadata_id: Optional[str] = None,
        client_name: Optional[str] = None,
        recapture_flag: bool = False,
        user_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Runs inference and returns a bundle containing:
          - "metadata"  : the dict to send back to the client
          - "snapshot"  : thread-safe data for the background DB save

        No request-specific data is stored on self.
        """
        print(f"Processing for client: {client_name}...")

        # ---- 1. Decode / validate image (local variable) ---------------
        if isinstance(image_data, str):
            image = ImageProcessor().process_image(image_data)
        else:
            image = image_data

        self._print_image_info("Processed image", image)

        # ---- 2. Start upload in background (non-blocking) --------------
        #
        # url_holder is created HERE, per request.  The upload thread
        # closes over it; save_metadata receives it as a parameter.
        # It is NEVER stored on self.
        #
        url_holder: Dict[str, Optional[str]] = {"img_url": None, "cdn_url": None, "complete": False}

        if client_name and client_name.lower() != "reliance":
            image_copy = image.copy()   # PIL copy for thread safety

            def upload_worker() -> None:
                try:
                    print("[Parallel Upload] Starting...")
                    t0 = time.time()
                    final_id  = metadata_id or f"{env_id}_inference_temp"
                    blob_name = build_blob_name(final_id, client_name=client_name, user_id=user_id)
                    result    = self.storage.upload_image(image_copy, blob_name, client_name, user_id)
                    if isinstance(result, dict):
                        url_holder["img_url"] = result.get("public_url")
                        url_holder["cdn_url"]  = result.get("cdn_url")
                    else:
                        url_holder["img_url"] = result
                    url_holder["complete"] = True
                    print(f"[Upload] Done in {time.time() - t0:.2f}s  url={url_holder['img_url']}")
                except Exception as exc:
                    print(f"[Upload] Failed: {exc}")
                    traceback.print_exc()

            threading.Thread(target=upload_worker, daemon=True).start()
        else:
            url_holder["complete"] = True   # Reliance: no parallel upload needed

        # ---- 3. Run inference (while upload continues) -----------------
        ocr_query = (
            "what is the maximum retail price, manufacturing date, "
            "expiry date, batch number in the retail image?"
        )

        t0 = time.perf_counter()
        ocr_result = await self.qwen_model.process_batch(image, ocr_query)
        model_time_ms = (time.perf_counter() - t0) * 1000
        print(f"[TIMER] Inference: {model_time_ms:.2f} ms")
        print(f"Inference result: {ocr_result}")

        # ---- 4. Extract metadata (all local) ---------------------------
        t0 = time.perf_counter()
        mrp, dates, batch_no = self._extract_metadata(ocr_result)
        print(f"[TIMER] Regex extraction: {(time.perf_counter() - t0) * 1000:.2f} ms")

        data = MetadataProcessor.create_metadata(mrp, dates, batch_no)

        # ---- 5. Session cache (keyed by env_id — already isolated) -----
        if recapture_flag:
            self.session_cache.set(env_id, self.get_processed_data(data, env_id))
        elif capture_type:
            session_meta = self.session_cache.get(env_id)
            if not session_meta:
                self.session_cache.set(env_id, copy.deepcopy(data))
            else:
                for key, value in session_meta.items():
                    if value is None or (isinstance(value, str) and value.lower() == "nan"):
                        if key in data and data[key] is not None:
                            session_meta[key] = data[key]
                self.session_cache.set(env_id, session_meta)
        else:
            self.session_cache.set(env_id, copy.deepcopy(data))

        metadata = copy.deepcopy(self.session_cache.get(env_id))
        print(f"[MULTICAPTURE] Final Metadata Returned: {metadata}")

        # ---- 6. Build and return the self-contained result bundle ------
        #
        # "snapshot" carries everything the background save thread needs.
        # url_holder is included by reference so the thread can wait for
        # the upload to complete after inference has already returned.
        #
        return {
            "metadata":   metadata,
            "snapshot": {
                "image":        image,            # this request's image
                "data":         data,             # this request's extracted data
                "ocr_raw":      ocr_result[0] if ocr_result else "N/A",
                "dates":        dates,
                "url_holder":   url_holder,       # per-request upload holder
                "capture_type": capture_type,     # True = multicapture second+ shot
                "model_time_ms": model_time_ms,
            },
        }

    # ------------------------------------------------------------------
    # DB save  — fully driven by explicit parameters, reads nothing from self
    # ------------------------------------------------------------------

    @time_calculate()
    def process_image_save_metadata(
        self,
        image_name: str,
        ean_code: Optional[str],
        env_id: str,
        store_id: str,
        snapshot: Dict[str, Any],           # the bundle from process_image_get_metadata
        rec_metadata_id: Optional[str] = None,
        recapture_flag: bool = False,
        client_name: str = "reliance",
        metadata_id: Optional[str] = None,
        skip_image_upload: bool = False,
        pid: Optional[str] = None,
        request_id: Optional[str] = None,   # optional request ID (shipsy & reliance)
        scan_duration: Optional[float] = None,
    ) -> None:
        """
        Saves metadata to the database.

        All request-specific data arrives via `snapshot`; nothing is read
        from self.* fields set by other requests.
        """
        try:
            print(f"Starting process_image_save_metadata (skip_upload={skip_image_upload})...")

            # Unpack snapshot
            snap_image   = snapshot.get("image")
            snap_data    = snapshot.get("data", {})
            snap_ocr_raw = snapshot.get("ocr_raw", "N/A")
            snap_dates   = snapshot.get("dates", [])
            url_holder   = snapshot.get("url_holder", {})
            snap_model_time_ms = snapshot.get("model_time_ms")

            store, user_id, device_id, img_name = ImageProcessor.extract_img_details(image_name)
            final_metadata_id = metadata_id or (env_id + img_name)

            img_blob = build_blob_name(final_metadata_id, client_name=client_name, user_id=user_id)
            plt_blob = build_blob_name(final_metadata_id, plotted=True, client_name=client_name, user_id=user_id)

            # ---- Resolve image URL ------------------------------------
            if not skip_image_upload:
                print("[save_metadata] Uploading image...")
                upload_result = self.storage.upload_image(snap_image, img_blob, client_name, user_id)
                if isinstance(upload_result, dict):
                    img_url = upload_result.get("public_url")
                    cdn_url = upload_result.get("cdn_url")
                else:
                    img_url = upload_result
                    cdn_url = None
            else:
                # Wait for the parallel upload thread (max 10 s)
                print("[save_metadata] Waiting for parallel upload URL...")
                for _ in range(100):
                    if url_holder.get("img_url"):
                        break
                    time.sleep(0.1)
                img_url = url_holder.get("img_url")
                cdn_url = url_holder.get("cdn_url")

            print(f"[save_metadata] img_url={img_url}")

            # ---- Optional plotted overlay (Reliance only) -------------
            # if client_name.lower() == "reliance":
            #     try:
            #         ImageProcessor.plot_ocr_output(snap_image, plt_blob, snap_ocr_raw)
            #     except Exception as exc:
            #         print(f"Plot overlay failed: {exc}")

            # ---- Format dates and build DB document -------------------
            mfg_exp_dates = self.str_to_datetime.format_datetime_array(snap_dates)

            processed_data = copy.deepcopy(snap_data)
            processed_data["mfg_date"]      = mfg_exp_dates[0] if mfg_exp_dates else None
            processed_data["expiry_date"]   = (
                mfg_exp_dates[1]
                if len(mfg_exp_dates) > 1 and mfg_exp_dates[0] != mfg_exp_dates[1]
                else None
            )
            processed_data["ocr_raw_output"] = snap_ocr_raw
            processed_data["img_url"]        = img_url
            processed_data["client_name"]    = client_name
            processed_data["storage"]        = "azure" if client_name.lower() in ("reliance", "shipsy") else "e2e"
            if scan_duration is not None:
                processed_data["scan_duration_sec"] = round(scan_duration, 2)
            if snap_model_time_ms is not None:
                processed_data["model_time_ms"] = round(snap_model_time_ms, 2)
            if pid is not None:
                processed_data["pid"]        = pid

            # ---- Write to DB ------------------------------------------
            if env_id:
                check_result = self.database.check_env_doc(env_id)
                if check_result:
                    # env doc exists, stamp request_id if provided
                    if request_id:
                        self.database.save_session(request_id, env_id)
                else:
                    # env doc does not exist, create it with request_id
                    env_data = MetadataProcessor.create_env_data(store_id, device_id, user_id, env_id)
                    if env_data:
                        env_data["client_name"] = client_name
                        if request_id:
                            env_data["session_id"] = request_id
                        self.database.create_env_doc(env_data)

                if recapture_flag and rec_metadata_id:
                    self.database.remove_metadata(rec_metadata_id)

                # ----------------------------------------------------------
                # Multicapture (capture_type=True):
                #   1. Merge null/nan root fields into the FIRST doc (C1 wins)
                #   2. Also save this capture as a NEW doc tagged
                #      capture_type="multicapture" for reference.
                #   The `predicted` field on the first doc is never touched.
                # ----------------------------------------------------------
                if snapshot.get("capture_type") and not recapture_flag:
                    # Step 1 — patch the first doc
                    merged = self.database.merge_multicapture_metadata(
                        env_id   = env_id,
                        new_data = processed_data,
                    )
                    if merged:
                        print(f"[Multicapture] Merged second-capture data into first doc for env_id={env_id}")
                    else:
                        print("[Multicapture] No existing doc to merge into — first doc will be created below")

                    # Step 2 — always create the second-capture doc for reference
                    processed_data["capture_type"] = "multicapture"
                    print(f"[DB] Saving multicapture metadata document: {processed_data}")
                    created_id = self.database.create_metadata(processed_data, ean_code, env_id, final_metadata_id)
                    print(f"[Multicapture] Second-capture doc {created_id} saved with capture_type=multicapture")
                else:
                    print(f"[DB] Saving metadata document: {processed_data}")
                    created_id = self.database.create_metadata(processed_data, ean_code, env_id, final_metadata_id)
                    print(f"Metadata {created_id} created successfully")
            else:
                print("env_id is not defined — skipping DB write")

        except Exception as exc:
            print(f"Error in save_metadata: {exc}")
            traceback.print_exc()
            raise ProcessingError(f"Failed to save metadata: {exc}")

    # ------------------------------------------------------------------
    # Batch processing  (stateless — all results returned, nothing on self)
    # ------------------------------------------------------------------

    async def process_batch_images(
        self,
        images: List[Any],
        query: str,
    ) -> List[Tuple[Optional[Dict[str, Any]], Optional[str]]]:
        """
        Process multiple images concurrently.
        Returns list of (metadata_dict, raw_ocr_string) tuples.
        """
        try:
            print(f"Processing concurrent batch of {len(images)} images")

            tasks              = []
            valid_indices: List[int] = []

            for i, img in enumerate(images):
                if img is not None:
                    tasks.append(self.qwen_model.process_batch(img, query))
                    valid_indices.append(i)

            if not tasks:
                return [(None, "No valid images provided")] * len(images)

            t0 = time.perf_counter()
            ocr_results = await asyncio.gather(*tasks, return_exceptions=True)
            elapsed = (time.perf_counter() - t0) * 1000
            print(f"[TIMER] Batch of {len(tasks)} in {elapsed:.2f} ms  ({elapsed/len(tasks):.2f} ms avg)")

            results: List[Tuple[Optional[Dict], Optional[str]]] = [
                (None, "Image skipped or failed preparation")
            ] * len(images)

            for task_idx, img_idx in enumerate(valid_indices):
                ocr = ocr_results[task_idx]

                if isinstance(ocr, Exception):
                    print(f"Error processing image {img_idx}: {ocr}")
                    results[img_idx] = (None, str(ocr))
                    continue

                raw = ocr[0]
                if raw.startswith("ERROR"):
                    results[img_idx] = (None, raw)
                    continue

                try:
                    mrp, dates, batch_no = self._extract_metadata([raw])
                    data = MetadataProcessor.create_metadata(mrp, dates, batch_no)
                    results[img_idx] = (data, raw)
                except Exception as exc:
                    print(f"Metadata extraction error for image {img_idx}: {exc}")
                    results[img_idx] = (None, f"Metadata extraction failed: {exc}")

            return results

        except Exception as exc:
            print(f"Error in process_batch_images: {exc}")
            traceback.print_exc()
            return [(None, str(exc))] * len(images)

    # ------------------------------------------------------------------
    # Stream processing  — called by /ws/stream after frame selection
    # ------------------------------------------------------------------

    @staticmethod
    def _merge_stream_results(
        all_mrp:   List[Optional[str]],
        all_dates: List[Any],
        all_batch: List[Optional[str]],
    ) -> Tuple[Optional[str], List[Any], Optional[str]]:
        """
        Merge OCR extractions from multiple patches using majority vote.

        MRP & batch_no — most-common non-None value wins.
        Dates          — union across all patches, deduplicated by string repr.
        """
        from collections import Counter

        def _majority(values: List[Optional[str]]) -> Optional[str]:
            non_null = [v for v in values if v is not None]
            if not non_null:
                return None
            return Counter(non_null).most_common(1)[0][0]

        mrp      = _majority(all_mrp)
        batch_no = _majority(all_batch)

        # Date dedup by string representation to handle equivalent date objects
        seen_dates: List[Any] = []
        seen_strs: set        = set()
        for d in all_dates:
            key = str(d)
            if key not in seen_strs:
                seen_dates.append(d)
                seen_strs.add(key)
        dates = seen_dates

        return mrp, dates, batch_no

    @time_calculate()
    async def process_stream_images(
        self,
        patches:      List[Any],              # 1–3 cropped PIL Images from PatchCropper
        env_id:       str,
        capture_type: Optional[bool],
        metadata_id:  Optional[str] = None,
        client_name:  Optional[str] = None,
        user_id:      Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Run parallel Qwen LLM inference on label patches and merge results.

        Pipeline:
          1. Fire one Qwen call per patch concurrently (asyncio.gather).
          2. Extract MRP / dates / batch_no from each successful result.
          3. Merge by majority vote (MRP, batch_no) and union (dates).
          4. Update session cache with the same logic as process_image_get_metadata.
          5. Return same {"metadata", "snapshot"} bundle shape.

        Fallback:
          If ALL patches fail LLM inference, raises ProcessingError.
          If SOME fail, the successful ones are still merged normally.
        """
        print(
            f"[Stream] process_stream_images — "
            f"{len(patches)} patches  env_id={env_id}  client={client_name}"
        )

        ocr_query = (
            "what is the maximum retail price, manufacturing date, "
            "expiry date, batch number in the retail image?"
        )

        # ── 1. Parallel Qwen calls ──────────────────────────────────────
        tasks = [self.qwen_model.process_batch(patch, ocr_query) for patch in patches]

        t0 = time.perf_counter()
        ocr_results = await asyncio.gather(*tasks, return_exceptions=True)
        elapsed_ms  = (time.perf_counter() - t0) * 1000
        print(f"[TIMER][Stream] {len(tasks)} parallel Qwen calls: {elapsed_ms:.2f} ms")

        # ── 2. Collect raw strings (skip exceptions / ERROR responses) ──
        raw_outputs: List[str] = []
        for i, res in enumerate(ocr_results):
            if isinstance(res, Exception):
                print(f"[Stream] Patch {i} LLM error: {res}")
                continue
            if not res or not isinstance(res, list):
                continue
            raw = str(res[0])
            if raw.startswith("ERROR"):
                print(f"[Stream] Patch {i} LLM returned error string: {raw}")
                continue
            raw_outputs.append(raw)
            print(f"[Stream] Patch {i} OCR raw: {raw[:120]}")

        if not raw_outputs:
            raise ProcessingError(
                "All stream patches failed LLM inference — cannot merge results"
            )

        print(f"[Stream] Valid OCR results: {len(raw_outputs)}/{len(patches)}")

        # ── 3. Extract metadata from each result ────────────────────────
        all_mrp:   List[Optional[str]] = []
        all_dates: List[Any]           = []
        all_batch: List[Optional[str]] = []

        for raw in raw_outputs:
            try:
                mrp, dates, batch_no = self._extract_metadata([raw])
                if mrp:
                    all_mrp.append(mrp)
                if dates:
                    all_dates.extend(dates)
                if batch_no:
                    all_batch.append(batch_no)
            except Exception as exc:
                print(f"[Stream] Metadata extraction error for raw='{raw[:60]}': {exc}")

        # ── 4. Majority-vote merge ───────────────────────────────────────
        mrp, dates, batch_no = self._merge_stream_results(all_mrp, all_dates, all_batch)
        print(f"[Stream] Merged result — mrp={mrp}  dates={dates}  batch_no={batch_no}")

        data = MetadataProcessor.create_metadata(mrp, dates, batch_no)

        # ── 5. Session cache (same logic as process_image_get_metadata) ─
        if capture_type:
            session_meta = self.session_cache.get(env_id)
            if not session_meta:
                self.session_cache.set(env_id, copy.deepcopy(data))
            else:
                for key, value in session_meta.items():
                    if value is None or (isinstance(value, str) and value.lower() == "nan"):
                        if key in data and data[key] is not None:
                            session_meta[key] = data[key]
                self.session_cache.set(env_id, session_meta)
        else:
            self.session_cache.set(env_id, copy.deepcopy(data))

        metadata = copy.deepcopy(self.session_cache.get(env_id))
        print(f"[Stream] Final session metadata: {metadata}")

        # ── 6. Return result bundle ──────────────────────────────────────
        main_image = patches[0] if patches else None

        return {
            "metadata": metadata,
            "snapshot": {
                "image":        main_image,
                "data":         data,
                "ocr_raw":      raw_outputs[0] if raw_outputs else "N/A",
                "dates":        dates,
                "url_holder":   {"img_url": None, "cdn_url": None, "complete": True},
                "capture_type": capture_type,
            },
        }

