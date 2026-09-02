import os
import re
import difflib
from datetime import datetime, timedelta
from typing import Optional
from pymongo import MongoClient
from bson import ObjectId
from src.utils.datetime import Datetime

from dotenv import load_dotenv

# Load environment variables
load_dotenv('src/configs/.env')

class Mongo:
    """
    A consolidated class to handle all database-related operations with tenant routing.
    """

    def __init__(self):
        # Single Cosmos cluster shared by both Reliance and Shipsy
        self.cosmos_uri = os.getenv('COSMOS_URI') or os.getenv('MONGO_URI')
        if not self.cosmos_uri:
            raise RuntimeError("COSMOS_URI/MONGO_URI is missing")

        self.reliance_db_name = os.getenv('RELIANCE_DB_NAME', 'neo-icr')

        # Single MongoClient — thread-safe and reused for both tenants
        self.cosmos_client = MongoClient(self.cosmos_uri)

        self.datetime = Datetime()
        self._active_client = None
        self._active_db = None
        self.metadata = None
        self.env = None
        self.user_col = None
        self.store_col = None

        self.set_tenant("reliance")  # default tenant
        print(f"Connected to MongoDB database: {self._active_db.name}")

    def _db_for(self, client_name: str):
        """
        Both Reliance and Shipsy share the same Cosmos cluster and DB.
        Any other client name is rejected.
        """
        name = (client_name or "").strip().lower()

        if name in ("reliance", "shipsy"):
            return self.cosmos_client, self.reliance_db_name

        raise ValueError(f"Unknown client '{client_name}'. Only 'reliance' and 'shipsy' are supported.")

    def _get_collection_names(self, client_name: str):
        """
        Both Reliance and Shipsy use the same default collection names
        on the shared Cosmos DB.
        The new_eanbatch collection is available for both clients — it lives
        in the same DB and holds ground-truth product batch records for
        EAN-based autocorrection.
        """
        return {
            'metadata': 'metadata',
            'environment': 'environment',
            'users': 'users',
            'store': 'store',
            'eanbatch': 'new_eanbatch',   # EAN autocorrection ground-truth records
        }

    def set_tenant(self, client_name: str):
        """
        Switch the active database/collections for the given client.
        Supported clients: 'reliance', 'shipsy'.
        Call this once per request (and inside threads) before DB ops.
        """
        client, db_name = self._db_for(client_name)
        self._active_client = client
        self._active_db = client[db_name]

        # Get collection names based on client
        collection_names = self._get_collection_names(client_name)

        # (Re)bind collections with dynamic names
        self.metadata  = self._active_db[collection_names['metadata']]
        self.env       = self._active_db[collection_names['environment']]
        self.user_col  = self._active_db[collection_names['users']]
        self.store_col = self._active_db[collection_names['store']]

        # eanbatch — ground-truth collection for EAN autocorrection
        eanbatch_col = collection_names.get('eanbatch')
        self.eanbatch = self._active_db[eanbatch_col] if eanbatch_col else None

        print(f"[Mongo] Tenant set → client_name={client_name}, db={db_name}, "
              f"metadata_col={collection_names['metadata']}, env_col={collection_names['environment']}, "
              f"eanbatch_col={eanbatch_col}")

    # ---------- below this line, your original methods remain unchanged ----------
    # They now operate on self.metadata/self.env/... which reflect the active tenant.

    # ------------------------------------------------------------------
    # EAN-batch verification helpers
    # ------------------------------------------------------------------

    def fetch_eanbatch_docs(self, ean: str) -> list:
        """
        Phase-1 EAN lookup: fetch all eanbatch docs for the given EAN (exact match).
        Safe to call from any thread — MongoClient is thread-safe.
        Returns [] if the collection is not configured or EAN not found.
        """
        if self.eanbatch is None:
            print("[EAN] eanbatch collection not configured for this tenant — skipping")
            return []
        try:
            docs = list(self.eanbatch.find({"ean": str(ean)}))
            print(f"[EAN] Found {len(docs)} doc(s) in eanbatch for ean={ean}")
            return docs
        except Exception as e:
            print(f"[EAN] fetch_eanbatch_docs error: {e}")
            return []

    def fetch_eanbatch_fallback(
        self,
        mrp,
        mfg_date,
        expiry_date,
        batch_no,
    ) -> list:
        """
        Phase-2 fallback: attribute-based lookup when exact EAN search returns empty.
        Searches eanbatch by MRP range (±5%) + date windows (±32 days) +
        batch prefix regex (first 4 chars). Returns up to 10 candidate docs.

        NOTE: Not used in the main path currently — available for future use.
        """
        if self.eanbatch is None:
            return []

        try:
            query: dict = {}

            # MRP: ±5% window
            if mrp is not None:
                try:
                    mrp_val = float(mrp)
                    if mrp_val > 0:
                        query["mrp"] = {
                            "$gte": round(mrp_val * 0.95, 2),
                            "$lte": round(mrp_val * 1.05, 2),
                        }
                except (TypeError, ValueError):
                    pass

            # Dates: ±32-day window
            def _date_range(val) -> Optional[dict]:
                if val is None:
                    return None
                dt = val if isinstance(val, datetime) else None
                if dt is None:
                    result = self.datetime.format_datetime_array([str(val)])
                    dt = result[0] if result else None
                if dt is None:
                    return None
                return {"$gte": dt - timedelta(days=32), "$lte": dt + timedelta(days=32)}

            exp_range = _date_range(expiry_date)
            mfg_range = _date_range(mfg_date)
            if exp_range:
                query["expiry_date"] = exp_range
            if mfg_range:
                query["mfg_date"] = mfg_range

            # Batch prefix: first 4 chars, case-insensitive
            if batch_no:
                prefix_raw = str(batch_no).strip()
                if len(prefix_raw) >= 4:
                    query["batch_no"] = {
                        "$regex": f"^{re.escape(prefix_raw[:4])}",
                        "$options": "i",
                    }

            if not query:
                print("[EAN Fallback] No queryable attributes — skipping fallback")
                return []

            docs = list(self.eanbatch.find(query).limit(10))
            print(f"[EAN Fallback] Attribute query returned {len(docs)} candidate(s)")
            return docs

        except Exception as exc:
            print(f"[EAN Fallback] error: {exc}")
            return []

    def apply_eanbatch_corrections(
        self,
        eanbatch_docs: list,
        predicted: dict,
        ean_code: Optional[str] = None,
        from_fallback: bool = False,
    ) -> dict:
        """
        Full fuzzy correction logic (older method — ported for reference).

        Rules:
        1. If batch fuzzy match >= 80%: adopt DB doc (correct any non-matching fields).
        2. If batch fuzzy match < 80%:
             - If >= 2 out of 3 other fields (mrp, mfg, exp) match: adopt DB doc.
             - Else: adopt nothing, return predicted.

        NOTE: Not used in the main path currently — `apply_simple_corrections` is
        the active method. Available for future use.
        """
        if not eanbatch_docs:
            return {"corrected": {}, "env_flags": {"new_sku": True}, "warnings": {}}

        scorable_docs = [d for d in eanbatch_docs if not d.get("manualverification")]
        if not scorable_docs:
            print("[EAN] All docs have manualverification=True — skipping EAN correction")
            return {"corrected": {}, "env_flags": {}, "warnings": {}}
        eanbatch_docs = scorable_docs

        def _clean_batch(b: str) -> str:
            if not b: return ""
            return re.sub(r'[\.\.\s]', '', str(b).lower())

        def _batch_score(pred: str, db: str) -> float:
            cp = _clean_batch(pred)
            cd = _clean_batch(db)
            if not cp or not cd: return 0.0
            if cp == cd: return 1.0
            return difflib.SequenceMatcher(None, cp, cd).ratio()

        def _mrp_match(pred_mrp, db_mrp) -> bool:
            try:
                p, d = float(pred_mrp), float(db_mrp)
                if p <= 0 or d <= 0: return False
                return abs(p - d) / max(abs(d), 0.01) <= 0.01
            except (TypeError, ValueError):
                return False

        def _to_dt(val):
            if val is None: return None
            if isinstance(val, datetime): return val
            result = self.datetime.format_datetime_array([val])
            return result[0] if result else None

        def _date_match(pred_val, db_val) -> bool:
            a = _to_dt(pred_val)
            b = _to_dt(db_val)
            if a is None or b is None: return False
            return a.date() == b.date()

        pred_batch = str(predicted.get("batch_no") or "").strip()

        best_doc = None
        best_bs = -1.0
        best_match_count = -1

        for doc in eanbatch_docs:
            bs = _batch_score(pred_batch, str(doc.get("batch_no") or ""))
            mrp_m = _mrp_match(predicted.get("mrp"), doc.get("mrp"))
            mfg_m = _date_match(predicted.get("mfg_date"), doc.get("mfg_date"))
            exp_m = _date_match(predicted.get("expiry_date"), doc.get("expiry_date"))
            mc = sum([mrp_m, mfg_m, exp_m])
            if bs > best_bs or (bs == best_bs and mc > best_match_count):
                best_bs = bs
                best_match_count = mc
                best_doc = doc

        if not best_doc:
            return {"corrected": {}, "env_flags": {"new_sku": True}, "warnings": {}}

        adopt_db = best_bs >= 0.80 or best_match_count >= 2

        if not adopt_db:
            print(f"[EAN] Best doc (batch={best_bs:.2f}, matches={best_match_count}) did not meet threshold.")
            return {"corrected": {}, "env_flags": {}, "warnings": {}}

        db_batch = str(best_doc.get("batch_no") or "").strip()
        db_mrp   = best_doc.get("mrp")
        db_mfg   = best_doc.get("mfg_date")
        db_exp   = best_doc.get("expiry_date")
        db_ean   = str(best_doc.get("ean") or "").strip()

        corrected: dict = {}
        env_flags: dict = {}
        warnings:  dict = {}

        es = 1.0 if (ean_code and str(ean_code).strip() == db_ean) else (
            difflib.SequenceMatcher(None, str(ean_code or ""), db_ean).ratio() if ean_code else 0.0
        )
        if from_fallback:
            if db_ean:
                corrected["ean"] = db_ean
            warnings["ean_correction_needed"] = True
            warnings["suggested_ean"] = db_ean
        elif es < 1.0 and db_ean:
            corrected["ean"] = db_ean
            if es >= 0.8:
                env_flags["ean_corrected"] = True
            else:
                warnings["ean_correction_needed"] = True
                warnings["suggested_ean"] = db_ean

        if best_bs < 1.0 and db_batch:
            corrected["batch_no"] = db_batch
            warnings["batch_no_needs_review"] = True
            if best_bs < 0.8:
                warnings["batch_confidence"] = "low"

        if not _mrp_match(predicted.get("mrp"), db_mrp) and db_mrp is not None:
            corrected["mrp"] = db_mrp
            warnings["mrp_corrected"] = True
            env_flags["mrp_corrected"] = True

        if not _date_match(predicted.get("mfg_date"), db_mfg) and db_mfg is not None:
            corrected["mfg_date"] = db_mfg
            env_flags["mfg_date_corrected"] = True
            warnings["mfg_date_corrected"] = True

        if not _date_match(predicted.get("expiry_date"), db_exp) and db_exp is not None:
            corrected["expiry_date"] = db_exp
            env_flags["expiry_date_corrected"] = True
            warnings["expiry_date_corrected"] = True

        if warnings:
            parts = []
            if warnings.get("batch_no_needs_review"): parts.append("batch number")
            if warnings.get("ean_correction_needed"): parts.append("EAN")
            if warnings.get("mrp_corrected"):         parts.append("MRP")
            if warnings.get("mfg_date_corrected"):    parts.append("manufacturing date")
            if warnings.get("expiry_date_corrected"): parts.append("expiry date")
            if parts:
                subject = " and ".join(parts).capitalize()
                verb = "were" if len(parts) > 1 else "was"
                warnings["message"] = f"{subject} {verb} auto-corrected — please verify before submitting."

        print(f"[EAN] Adopted DB doc: batch={best_bs:.2f} matches={best_match_count}. Corrections: {list(corrected.keys())}")
        return {"corrected": corrected, "env_flags": env_flags, "warnings": warnings}

    def apply_simple_corrections(
        self,
        eanbatch_docs: list,
        predicted: dict,
        ean_code: Optional[str] = None,
    ) -> dict:
        """
        Simplified correction logic against new_eanbatch collection.

        Case 1 — EAN matches AND batch matches 100%:
            → Silently correct mrp / mfg_date / expiry_date from DB if mismatched.
              A warning is emitted if any field was actually changed.

        Case 2 — EAN matches BUT batch does NOT match 100%:
            → ONLY update/correct batch_no if mfg_date AND expiry_date match 100% in DB doc.
              Attaches check flag: env_flags["check"] = True, warnings["check"] = True.
            → Otherwise (if mfg/exp do NOT match 100%):
              No corrections are made.

        Returns
        -------
        {
            "corrected":  dict,   # fields to apply to metadata
            "env_flags":  dict,   # persisted on environment doc
            "warnings":   dict,   # returned to the client in the API response
        }
        """
        if not eanbatch_docs:
            return {"corrected": {}, "env_flags": {}, "warnings": {"no_eanbatch_match": True}}

        # ── Helpers ──────────────────────────────────────────────────────────

        def _clean_batch(b: str) -> str:
            if not b:
                return ""
            return re.sub(r'[\.\.\s]', '', str(b).lower())

        def _batch_score(pred: str, db: str) -> float:
            cp = _clean_batch(pred)
            cd = _clean_batch(db)
            if not cp or not cd:
                return 0.0
            if cp == cd:
                return 1.0
            return difflib.SequenceMatcher(None, cp, cd).ratio()

        def _mrp_similarity(pred_mrp, db_mrp) -> float:
            """Returns 0.0–1.0 similarity. 1.0 = exact (within 1%)."""
            try:
                p, d = float(pred_mrp), float(db_mrp)
                if p <= 0 or d <= 0:
                    return 0.0
                diff_pct = abs(p - d) / max(abs(d), 0.01)
                if diff_pct <= 0.01:
                    return 1.0
                if diff_pct <= 0.20:
                    return 1.0 - diff_pct
                return 0.0
            except (TypeError, ValueError):
                return 0.0

        def _to_dt(val):
            if val is None:
                return None
            if isinstance(val, datetime):
                return val
            result = self.datetime.format_datetime_array([val])
            return result[0] if result else None

        def _date_match(pred_val, db_val) -> bool:
            a = _to_dt(pred_val)
            b = _to_dt(db_val)
            if a is None or b is None:
                return False
            return a.date() == b.date()

        valid_docs = [d for d in eanbatch_docs if not d.get("manualverification")]
        if not valid_docs:
            print("[EAN simple] All docs are manualverification=True — skipping")
            return {"corrected": {}, "env_flags": {}, "warnings": {"manual_verification_required": True}}

        pred_batch = str(predicted.get("batch_no") or "").strip()

        corrected: dict = {}
        env_flags: dict = {}
        warnings:  dict = {}

        # ── Case 1: Search for 100% batch match ──────────────────────────────
        exact_batch_doc = None
        for doc in valid_docs:
            if _batch_score(pred_batch, str(doc.get("batch_no") or "")) >= 1.0:
                exact_batch_doc = doc
                break

        if exact_batch_doc:
            # Case 1: 100% batch match → correct mrp, mfg, exp from DB if mismatched
            db_mrp = exact_batch_doc.get("mrp")
            db_mfg = exact_batch_doc.get("mfg_date")
            db_exp = exact_batch_doc.get("expiry_date")

            if not _mrp_similarity(predicted.get("mrp"), db_mrp) >= 1.0 and db_mrp is not None:
                corrected["mrp"] = db_mrp
                env_flags["mrp_corrected"] = True

            if not _date_match(predicted.get("mfg_date"), db_mfg) and db_mfg is not None:
                corrected["mfg_date"] = db_mfg
                env_flags["mfg_date_corrected"] = True

            if not _date_match(predicted.get("expiry_date"), db_exp) and db_exp is not None:
                corrected["expiry_date"] = db_exp
                env_flags["expiry_date_corrected"] = True

            if corrected:
                warnings["warning"] = True
                warnings["mrp_or_dates_corrected"] = True
                warnings["message"] = f"Auto-corrected: {', '.join(corrected.keys())} from database."
                print(f"[EAN simple] Case 1 — batch 100% match. Corrections with warning: {list(corrected.keys())}")
            else:
                print("[EAN simple] Case 1 — batch 100% match. All values already matched, no corrections needed.")

            return {"corrected": corrected, "env_flags": env_flags, "warnings": warnings}

        # ── Case 2: Batch < 100% ─────────────────────────────────────────────
        # ONLY update batch_no if BOTH mfg_date AND expiry_date match 100% (do NOT update MRP)
        matching_date_doc = None
        best_bs = -1.0

        for doc in valid_docs:
            mfg_matched = _date_match(predicted.get("mfg_date"), doc.get("mfg_date"))
            exp_matched = _date_match(predicted.get("expiry_date"), doc.get("expiry_date"))

            if mfg_matched and exp_matched:
                bs = _batch_score(pred_batch, str(doc.get("batch_no") or ""))
                if bs > best_bs:
                    best_bs = bs
                    matching_date_doc = doc

        if matching_date_doc:
            db_batch = str(matching_date_doc.get("batch_no") or "").strip()
            if db_batch:
                corrected["batch_no"] = db_batch
            env_flags["check"] = True
            warnings["check"]   = True
            warnings["message"] = (
                "Batch number was auto-corrected based on matching MFG and EXP dates — please verify."
            )
            print(f"[EAN simple] Case 2 — Batch < 100% but MFG & EXP matched 100%. Updated batch_no to '{db_batch}' with check flag.")
        else:
            print("[EAN simple] Case 2 — Batch < 100% and MFG/EXP did not match 100%. No corrections made.")

        print(
            f"[EAN simple] Done. corrections={list(corrected.keys())}  "
            f"flags={list(env_flags.keys())}  "
            f"warnings={list(warnings.keys())}"
        )
        return {"corrected": corrected, "env_flags": env_flags, "warnings": warnings}

    def update_document(self, metadata_id: str, updates: dict) -> bool:
        try:
            status = self.metadata.update_one(
                {'_id': ObjectId(metadata_id)},
                {'$set': updates}
            )
            if status.matched_count == 0:
                print("No document found with the given metadata_id.")
                return False
            elif status.modified_count == 0:
                print("Document found but no changes made (data may be identical).")
                return False
            print("Document updated successfully.")
            return True
        except Exception as e:
            print(f"An error occurred while updating the document: {e}")
            return False

    def save_document(self, data: dict):
        try:
            doc = self.metadata.insert_one(data)
            print(f"Document saved successfully with ID: {doc.inserted_id}")
            return doc.inserted_id
        except Exception as e:
            print(f"An error occurred while saving the document: {e}")
            return None

    def update_env_sap_status(self, user_id):
        """
        Fetch env docs with sap_status=False for a given user_id, 
        retrieve metadata details, and update sap_status.
        
        :param user_id: The ID of the user
        :param db: The MongoDB database connection
        :return: List of metadata details for each env_id
        """
    
        # Fetch all env docs with sap_status=False for the given user_id
        env_doc = self.env.find_one({"session_id": user_id, "sap_status": False}, sort=[("_id", -1)])
        if not env_doc:
            return None
        
        env_id = env_doc["env_id"]
        
        # Fetch metadata for the given env_id
        metadata_doc = self.metadata.find_one({"env_id": env_id}, sort=[("_id", -1)])
        
        if metadata_doc:
            metadata_details = {
                "ean": str(metadata_doc.get('ean')),
                "mrp": float(metadata_doc.get("mrp")) if metadata_doc.get("mrp") is not None else None,
                "mfg": str(metadata_doc.get("mfg_date")),
                "expiry": str(metadata_doc.get("expiry_date")),
                "batch_no": str(metadata_doc.get("batch_no")),
                "qty": metadata_doc.get("qty")
            }
            
            # Append to list
            # env_list.append(metadata_details)
            
            # Update sap_status to True for processed env doc
            self.env.update_one({"env_id": env_id}, {"$set": {"sap_status": True}})
        
        return metadata_details

    # ------------------------------------------------------------------
    # Session helpers  (shipsy & reliance)
    # ------------------------------------------------------------------

    def save_session(self, session_id: str, env_id: str) -> bool:
        """
        Stamp session_id (a.k.a. request_id) onto the existing environment
        document for this env_id.  No new document is created — the env doc
        already exists from the regular process flow.

        Used by both Shipsy and Reliance clients.

        :param session_id: request_id string passed from the SDK at capture time
        :param env_id:     The env_id of the capture whose env doc to update
        :return:           True on success, False on error
        """
        try:
            result = self.env.update_one(
                {"env_id": env_id},
                {"$set": {"session_id": session_id}}
            )
            if result.matched_count == 0:
                print(f"[Mongo] save_session: no env doc found for env_id={env_id}")
                return False
            print(f"[Mongo] Session stamped: session_id={session_id}  env_id={env_id}")
            return True
        except Exception as exc:
            print(f"[Mongo] save_session error: {exc}")
            return False


    def get_session(self, session_id: str, expected_client: str = None):
        """
        Retrieve metadata for a session by its request_id / session_id.

        Finds the env doc by session_id, cross-validates its client_name against
        expected_client (the client detected from the JWT), then fetches metadata.

        :param session_id:      request_id string stored at capture time
        :param expected_client: client detected from the JWT ('reliance'/'shipsy').
                                If provided, must match env_doc['client_name'].
        :return:                metadata detail dict, or None if not found / rejected
        """
        try:
            # NOTE: Do NOT filter on sap_status here.
            # CosmosDB for MongoDB API may store booleans as integers (0/1),
            # which causes {"sap_status": False} to miss documents where the
            # field was stored as the integer 0. Look up by session_id only.
            env_doc = self.env.find_one({"session_id": session_id})
            if not env_doc:
                print(f"[Mongo] Session NOT found: session_id={session_id}")
                return None

            # ── Cross-client validation ──────────────────────────────────────
            # The environment doc carries the client_name that originally created
            # this session. Verify the JWT's client matches it.
            if expected_client:
                doc_client = (env_doc.get("client_name") or "").strip().lower()
                if doc_client and doc_client != expected_client.strip().lower():
                    print(
                        f"[Mongo] ❌ Client mismatch: token says '{expected_client}' "
                        f"but session belongs to '{doc_client}'. "
                        f"session_id={session_id}"
                    )
                    return "CLIENT_MISMATCH"  # distinct sentinel → 401 in the API

            # Warn if already consumed — but still return data (idempotent reads)
            sap_done = env_doc.get("sap_status")
            if sap_done is True or sap_done == 1:
                print(f"[Mongo] ⚠️ Session already consumed (sap_status=True): session_id={session_id}")

            env_id = env_doc["env_id"]
            print(f"[Mongo] Session found: session_id={session_id}  env_id={env_id}")

            # Fetch the earliest metadata doc for this env (ascending _id sort)
            metadata_doc = self.metadata.find_one({"env_id": env_id}, sort=[("_id", 1)])
            if not metadata_doc:
                print(f"[Mongo] No metadata doc found for env_id={env_id}")
                return None

            metadata_details = {
                "ean":      str(metadata_doc.get("ean")),
                "mrp":      float(metadata_doc.get("mrp")) if metadata_doc.get("mrp") is not None else None,
                "mfg":      str(metadata_doc.get("mfg_date")),
                "expiry":   str(metadata_doc.get("expiry_date")),
                "batch_no": str(metadata_doc.get("batch_no")),
                "qty":      metadata_doc.get("qty"),
            }

            # Mark this session as consumed
            self.env.update_one({"env_id": env_id}, {"$set": {"sap_status": True}})
            print(f"[Mongo] sap_status set to True for env_id={env_id}")

            return metadata_details

        except Exception as exc:
            print(f"[Mongo] get_session error: {exc}")
            return None

    def find_document(self, metadata_id: str):
        """
        Find a document in the collection by its metadata_id.
        """
        try:
            document = self.metadata.find_one({'_id': ObjectId(metadata_id)})
            if document:
                print("Document found.")
                return document
            else:
                print("No document found with the given metadata_id.")
                return None
        except Exception as e:
            print(f"An error occurred while fetching the document: {e}")
            return None
        
    def user_validation(self, user_id):
        try:
            print(f'user_id:{user_id}')
            # Search for the user in metadata collection
            doc = self.user_col.find_one({'id': user_id})
            print(f'user_doc:{doc}')
            
            # Validate document existence and 'format_id' key
            if not doc or 'format_id' not in doc or not doc['format_id']:
                return False, '' # Ensure this returns a tuple
            
            format_id = doc['format_id'][0] # Get the first format_id safely
            
            # Search in the store collection using format_id
            store_doc = self.store_col.find_one({'id': format_id})
            print(f'store doc:{store_doc}')
            
            # This part already returns a tuple, which is correct
            return bool(store_doc) , format_id
            
        except Exception as e:
            print(f"Error during user validation: {e}")
            return False, '' # Ensure this returns a tuple


    def sdk_data_process(self, input_dict, env_id):
        """
        Process the input dictionary, checking each specified key ('mrp', 'mfg_date', 'expiry_date', 'batch_no') 
        and retrieving their missing values from MongoDB documents in order. It checks the documents sequentially
        and updates the missing keys. If a key is not found in any document, it remains unchanged in the input_dict.

        Parameters:
            input_dict (dict): The input dictionary with possible missing values for the specified keys.
            env_id (str): The environment ID to query MongoDB.

        Returns:
            dict: The updated dictionary with missing values filled from the database.
        """
        
        # Define the keys we are interested in checking for missing values
        keys_to_check = ['mrp', 'mfg_date', 'expiry_date', 'batch_no']
        
        # Pipeline to fetch all documents sorted by timestamp (ascending)
        pipeline = [
            {"$match": {"env_id": env_id}},  # Filter by env_id
            {"$sort": {"timestamp": 1}}      # Sort by timestamp (oldest first)
        ]
        
        # Execute the pipeline and fetch all documents
        docs = list(self.metadata.aggregate(pipeline))
        
        # If no matching documents are found, return the original input_dict
        if not docs:
            print("No matching documents found in the database.")
            return input_dict

        print(f"Found {len(docs)} documents for env_id {env_id}.")
        
        # Dictionary to store updated values
        updated_dict = input_dict.copy()
        
        # Iterate through the keys_to_check and update their values based on the documents
        for key in keys_to_check:
            for doc in docs:
                if key in doc and doc[key] is not None:
                    updated_dict[key] = doc[key]
                    
                    # Format date fields if present (for 'mfg_date' and 'expiry_date')
                    if key in ["mfg_date", "expiry_date"]:
                        if isinstance(doc[key], datetime):
                            updated_dict[key] = doc[key].strftime("%d-%m-%Y")
                    
                    break  # Stop after the first available value is found for the key

        print(f"Updated data: {updated_dict}")
        return updated_dict


    def remove_metadata(self, metadata_id):
        query = {'metadata_id':metadata_id}
        result = self.metadata.delete_one(query)

        # Check if a document was deleted
        if result.deleted_count > 0:
            print(f"Document with metadata_id ='{metadata_id}' removed successfully.")
        else:
            print(f"No document found with metada_id ='{metadata_id}'.")


    
    def create_metadata(
        self,
        data: dict,
        ean_code,
        env_id: str,
        metadata_id: str,
        env_flags: Optional[dict] = None,
        predicted: Optional[dict] = None,
    ):
        """
        Create a metadata document in MongoDB.

        Args:
            data        : processed data dict (root-level fields may be corrected by EAN verification)
            ean_code    : scanned EAN
            env_id      : environment identifier
            metadata_id : stable ID for this capture
            env_flags   : optional dict of boolean flags to write onto the environment doc
                          (e.g. {"mfg_date_corrected": True, "check": True})
            predicted   : raw model predictions — always stored as-is in the `predicted` subdoc.
                          If None, falls back to reading from data (backwards compatible).
        """
        # `predicted` always holds raw model output — never the corrected values
        raw_predicted = predicted or data.get('predicted') or {
            'mrp':         data.get('mrp'),
            'mfg_date':    data.get('mfg_date'),
            'expiry_date': data.get('expiry_date'),
            'batch_no':    data.get('batch_no'),
        }

        metadata_doc = {
            'timestamp':      datetime.now(),
            'img_url':        data.get('img_url'),
            'ean':            ean_code,
            'mrp':            data.get('mrp'),
            'mfg_date':       data.get('mfg_date'),
            'expiry_date':    data.get('expiry_date'),
            'batch_no':       data.get('batch_no'),
            'ocr_raw_output': str(data.get('ocr_raw_output')),
            'env_id':         env_id,
            'present':        True,
            'metadata_id':    metadata_id,
            'qty':            data.get('qty', 1),
            'predicted':      raw_predicted,
        }
        if data.get('scan_duration_sec') is not None:
            metadata_doc['scan_duration_sec'] = data.get('scan_duration_sec')
        if data.get('model_time_ms') is not None:
            metadata_doc['model_time_ms'] = data.get('model_time_ms')
        # Tag multicapture docs so they're distinguishable from first captures
        if data.get('capture_type'):
            metadata_doc['capture_type'] = data.get('capture_type')
        if data.get('pid') is not None:
            metadata_doc['pid'] = data.get('pid')
        if data.get('barcode_no') is not None:
            metadata_doc['barcode_no'] = data.get('barcode_no')
        if data.get('manual_entry') is not None:
            metadata_doc['manual_entry'] = data.get('manual_entry')
        if data.get('client_name') is not None:
            metadata_doc['client_name'] = data.get('client_name')
        if data.get('storage') is not None:
            metadata_doc['storage'] = data.get('storage')
        if data.get('rotation') is not None:
            metadata_doc['rotation'] = data.get('rotation')
        if data.get('upright') is not None:
            metadata_doc['upright'] = data.get('upright')

        doc_id = self.save_document(metadata_doc)

        # ---- write env flags if any (e.g. mrp_corrected, check, etc.) ----
        if env_flags:
            try:
                self.env.update_one(
                    {"env_id": env_id},
                    {"$set": env_flags},
                )
                print(f"[EAN] env_id={env_id} flags updated: {env_flags}")
            except Exception as e:
                print(f"[EAN] Failed to write env flags: {e}")

        return doc_id

    # Root-level fields that are candidates for multicapture merging.
    # `predicted` is deliberately excluded.
    _MERGE_FIELDS = ("mrp", "mfg_date", "expiry_date", "batch_no", "img_url", "ocr_raw_output")

    def merge_multicapture_metadata(self, env_id: str, new_data: dict) -> bool:
        """
        Find the FIRST (oldest) metadata document for `env_id` and fill in any
        root-level fields that are currently null / "nan" with values from
        `new_data`.  Existing non-null values are kept (capture-1 wins).
        The `predicted` sub-document is never modified.

        Returns True if a document was found and updated, False otherwise.
        """
        try:
            # Fetch the earliest doc for this env_id (ascending _id = insertion order)
            first_doc = self.metadata.find_one(
                {"env_id": env_id},
                sort=[("_id", 1)]
            )
            if not first_doc:
                print(f"[Mongo] merge_multicapture: no existing doc for env_id={env_id}")
                return False

            updates = {}
            for field in self._MERGE_FIELDS:
                existing = first_doc.get(field)
                # Consider a field "empty" if it is None or the string "nan"
                is_empty = (
                    existing is None
                    or (isinstance(existing, str) and existing.strip().lower() == "nan")
                )
                if not is_empty:
                    continue  # capture-1 value wins — skip

                new_val = new_data.get(field)
                # Only write if the new value is actually meaningful
                if new_val is None:
                    continue
                if isinstance(new_val, str) and new_val.strip().lower() == "nan":
                    continue
                updates[field] = new_val

            if not updates:
                print(f"[Mongo] merge_multicapture: nothing to merge for env_id={env_id} (all fields already populated)")
                return True  # doc exists, nothing needed — still counts as "handled"

            print(f"[Mongo] merge_multicapture: updating fields {list(updates.keys())} on doc {first_doc['_id']}")
            self.metadata.update_one(
                {"_id": first_doc["_id"]},
                {"$set": updates}
            )
            return True

        except Exception as exc:
            print(f"[Mongo] merge_multicapture error: {exc}")
            return False


    def check_env_doc(self, env_id):
        try:
            if env_id:
                # Check if a document with the same 'end_id' exists
                existing_doc = self.env.find_one({"env_id": env_id})
                
                if existing_doc:
                    print("Document already exists:", existing_doc)
                    return existing_doc, True
        except Exception as e:
            print(f"An error occurred: {e}")
            return None
                
                
    def create_env_doc(self, data):
        try:
            if data:
                # Insert the new document if no existing document is found
                status = self.env.insert_one(data)
                print("Document created with environment_id:", status.inserted_id)
                if status.inserted_id:
                    # Retrieve the newly created document and return it
                    new_doc = self.env.find_one({"_id": status.inserted_id})
                    print("Document saved successfully:", new_doc)
                    return new_doc, True
                else:
                    print("Document not saved.")
                    return None, False
            else:
                print("No data provided.")
                return None, False
        except Exception as e:
            print(f"An error occurred: {e}")
            return None, False

    def update_value(self, env_id, data):
        updates = {}
        # Always target the FIRST (initial) capture doc for this env_id.
        # In a multicapture flow the second doc has capture_type="multicapture";
        # user edits must go to the primary doc so they are visible to SAP/get_session.
        pipeline = [
            {"$match": {"env_id": env_id, "capture_type": {"$exists": False}}},  # skip multicapture docs
            {"$sort": {"_id": 1}},   # oldest first → the initial capture doc
            {"$limit": 1}
        ]

        # Execute the pipeline to get the first (initial) capture document
        target_docs = list(self.metadata.aggregate(pipeline))
        print(f'metadata (first capture): {target_docs}')
        print(f'data: {data}')

        if not target_docs:
            # Fallback: no non-multicapture doc found — try any doc (handles edge cases)
            fallback = list(self.metadata.aggregate([
                {"$match": {"env_id": env_id}},
                {"$sort": {"_id": 1}},
                {"$limit": 1}
            ]))
            if not fallback:
                return {"message": "Document not found.", "updated_document": None}
            target_docs = fallback

        # Get the document from the list
        target_doc = target_docs[0]

        # Extract the ObjectId from the target document
        oid = target_doc.get('_id')
        if not oid:
            return {"message": "Document does not have a valid ObjectId.", "updated_document": None}

        for field_name, new_value in data.items():

            if new_value is not None and new_value != "":

                # EAN
                if field_name == 'ean':
                    new_value = str(new_value).strip()

                # Date fields
                if field_name in ['mfg_date', 'expiry_date']:
                    formatted_date = self.datetime.format_datetime_array([new_value])[0]
                    if formatted_date is None:
                        return {
                            "message": f"Invalid date format for {field_name}.",
                            "updated_document": None
                        }
                    new_value = formatted_date

                # MRP
                if field_name == 'mrp':
                    try:
                        new_value = float(new_value)
                    except ValueError:
                        return {
                            "message": "Invalid value for 'mrp'.",
                            "updated_document": None
                        }

                # Quantity
                if field_name == 'qty':
                    try:
                        new_value = int(new_value)
                    except ValueError:
                        return {
                            "message": "Invalid value for 'qty'.",
                            "updated_document": None
                        }

                updates[field_name] = new_value

        # If no updates are to be made, return a message indicating no changes
        if not updates:
            return {"message": "No valid fields to update.", "updated_document": None}

        print(f'update_data: {updates}')

        # ── Write 1: update the first/primary capture doc ──────────────────────
        result = self.metadata.update_one(
            {"_id": oid},
            {"$set": updates}
        )
        print(f"[update_value] Primary doc updated: matched={result.matched_count} modified={result.modified_count}")

        # ── Write 2: apply the same edits to all multicapture docs for this env ─
        # This keeps the second (and any subsequent) capture docs in sync with
        # the user's corrections, so both the first and multicapture docs show
        # the same edited root values.
        mc_result = self.metadata.update_many(
            {"env_id": env_id, "capture_type": "multicapture"},
            {"$set": updates}
        )
        print(f"[update_value] Multicapture docs updated: matched={mc_result.matched_count} modified={mc_result.modified_count}")

        if result.modified_count > 0 or mc_result.modified_count > 0:
            updated_doc = self.metadata.find_one({"_id": oid})
            return {
                "message": "Document updated successfully.",
                "updated_document": updated_doc
            }
        else:
            return {
                "message": "Document update failed or no changes made.",
                "updated_document": None
            }