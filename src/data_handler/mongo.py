import os
from datetime import datetime, timedelta
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
        """
        return {
            'metadata': 'metadata',
            'environment': 'environment',
            'users': 'users',
            'store': 'store'
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
        self.metadata = self._active_db[collection_names['metadata']]
        self.env = self._active_db[collection_names['environment']]
        self.user_col = self._active_db[collection_names['users']]
        self.store_col = self._active_db[collection_names['store']]

        print(f"[Mongo] Tenant set → client_name={client_name}, db={db_name}, "
              f"metadata_col={collection_names['metadata']}, env_col={collection_names['environment']}")

    # ---------- below this line, your original methods remain unchanged ----------
    # They now operate on self.metadata/self.env/... which reflect the active tenant.

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


    
    def create_metadata(self,  data: dict, ean_code, env_id: str, metadata_id:str):
        """
        Create metadata for a document in mongodb ;
        a.
        """
        metadata_doc = {
            'timestamp': datetime.now(),
            'img_url': data.get('img_url'),
            'ean': ean_code,
            'mrp': data.get('mrp'),
            'mfg_date': data.get('mfg_date'),
            'expiry_date': data.get('expiry_date'),
            'batch_no': data.get('batch_no'),
            'ocr_raw_output': str(data.get('ocr_raw_output')),
            'env_id': env_id,
            'present': True,
            'metadata_id': metadata_id,
            'qty': data.get('qty', 1),
            'predicted': data.get('predicted', {
                'mrp': data.get('mrp'),
                'mfg_date': data.get('mfg_date'),
                'expiry_date': data.get('expiry_date'),
                'batch_no': data.get('batch_no')
            })
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
        return self.save_document(metadata_doc)

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