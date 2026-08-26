import os
import threading
import time
import traceback
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from PIL import Image


STORAGE_BACKEND: str = os.getenv(
    "STORAGE_BACKEND",
    "azure"
).strip().lower()

LOCAL_STORAGE_ROOT: str = os.getenv(
    "LOCAL_STORAGE_ROOT",
    "/application/local_blob_storage",
)

LOCAL_RETENTION_DAYS: int = int(
    os.getenv("LOCAL_RETENTION_DAYS", "1")
)

# Application cleanup timezone
CLEANUP_TIMEZONE = ZoneInfo("Asia/Kolkata")


class StorageRouter:
    """
    Storage router with Azure → Local fallback and daily retention cleanup.

    Behaviour:

      "azure" (default):
        1. Attempt to upload to Azure Blob Storage.
        2. If Azure upload fails for any reason, save the image locally.

      "local":
        Bypass Azure entirely and save directly to LOCAL_STORAGE_ROOT.

    Retention policy:

      LOCAL_RETENTION_DAYS=1:
        At 12:00 AM IST every day, delete files whose modification
        time belongs to the previous day or earlier.

      LOCAL_RETENTION_DAYS=0:
        Disable cleanup.
    """

    def __init__(self):

        if STORAGE_BACKEND == "local":

            self._backend = "local"
            self.azure = None

            print(
                f"[StorageRouter] ⚠️ LOCAL-only mode → "
                f"{LOCAL_STORAGE_ROOT}"
            )

        else:

            self._backend = "azure"

            try:

                from src.data_handler.azure_blob_storage import (
                    AzureBlobUploader
                )

                self.azure = AzureBlobUploader()

                print(
                    "[StorageRouter] Azure Blob backend initialised "
                    "(local fallback enabled)"
                )

            except Exception as exc:

                self.azure = None

                print(
                    f"[StorageRouter] ⚠️ Azure init failed ({exc}) "
                    f"— falling back to LOCAL mode"
                )

        # Start retention scheduler
        if LOCAL_RETENTION_DAYS > 0:
            self._start_retention_daemon()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def upload_image(
        self,
        image_or_bytes,
        key: str,
        client_name: str,
        user_id: str = None,
    ):
        """
        Upload/save an image.

        Returns:
            str — Azure URL, local path, or None on failure.
        """

        if self._backend == "local" or self.azure is None:
            return self._save_locally(
                image_or_bytes,
                key
            )

        # Try Azure first
        try:

            result = self.azure.upload_image_to_blob(
                image_or_bytes,
                key
            )

            if result:
                return result

            raise RuntimeError(
                "Azure upload returned empty result"
            )

        except Exception as azure_exc:

            print(
                f"[StorageRouter] ⚠️ Azure upload failed "
                f"for '{key}': {azure_exc}\n"
                f"                    → falling back "
                f"to local disk save"
            )

        # Local fallback
        return self._save_locally(
            image_or_bytes,
            key
        )

    # ------------------------------------------------------------------
    # Local backend
    # ------------------------------------------------------------------

    def _save_locally(
        self,
        image_or_bytes,
        key: str
    ):
        """Save image to LOCAL_STORAGE_ROOT/<key>."""

        try:

            save_path = (
                Path(LOCAL_STORAGE_ROOT) / key
            )

            save_path.parent.mkdir(
                parents=True,
                exist_ok=True
            )

            if isinstance(
                image_or_bytes,
                Image.Image
            ):

                image_or_bytes.save(
                    str(save_path),
                    format="JPEG",
                    quality=95
                )

            elif hasattr(
                image_or_bytes,
                "read"
            ):

                with open(
                    str(save_path),
                    "wb"
                ) as f:

                    f.write(
                        image_or_bytes.read()
                    )

            else:

                with open(
                    str(save_path),
                    "wb"
                ) as f:

                    f.write(
                        image_or_bytes
                    )

            print(
                f"[StorageRouter][Local] "
                f"✅ Saved → {save_path}"
            )

            return str(save_path)

        except Exception as exc:

            print(
                f"[StorageRouter][Local] "
                f"❌ Local save failed for "
                f"'{key}': {exc}"
            )

            traceback.print_exc()

            return None

    # ------------------------------------------------------------------
    # Retention scheduler
    # ------------------------------------------------------------------

    def _start_retention_daemon(self):

        t = threading.Thread(
            target=self._retention_loop,
            name="StorageRetentionDaemon",
            daemon=True
        )

        t.start()

        print(
            f"[StorageRouter][Retention] 🗑️ "
            f"Daily cleanup scheduler started "
            f"(retention={LOCAL_RETENTION_DAYS} day(s), "
            f"root={LOCAL_STORAGE_ROOT}, "
            f"timezone=Asia/Kolkata)"
        )

    def _retention_loop(self):
        """
        Loop forever:

        1. Run cleanup once on startup.
        2. Wait until next 12:00 AM IST.
        3. Run cleanup.
        4. Repeat every day.
        """

        # Initial cleanup on startup
        self._delete_old_files()

        while True:

            seconds_until_cleanup = (
                self._seconds_until_next_midnight()
            )

            print(
                f"[StorageRouter][Retention] "
                f"Next cleanup in "
                f"{seconds_until_cleanup / 3600:.2f}h "
                f"(at next 12:00 AM IST)"
            )

            time.sleep(
                seconds_until_cleanup
            )

            self._delete_old_files()

    @staticmethod
    def _seconds_until_next_midnight() -> float:
        """
        Calculate seconds until the next 12:00 AM IST.
        """

        now = datetime.now(
            CLEANUP_TIMEZONE
        )

        tomorrow = (
            now + timedelta(days=1)
        ).replace(
            hour=0,
            minute=0,
            second=0,
            microsecond=0
        )

        return (
            tomorrow - now
        ).total_seconds()

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def _delete_old_files(self):
        """
        Delete files from previous days.

        At 12:00 AM IST:

            Yesterday's files → DELETE
            Today's files      → KEEP
        """

        root = Path(
            LOCAL_STORAGE_ROOT
        )

        if not root.exists():

            print(
                f"[StorageRouter][Retention] "
                f"Storage root does not exist: {root}"
            )

            return

        # Current time in IST
        now_ist = datetime.now(
            CLEANUP_TIMEZONE
        )

        # Midnight today in IST
        today_midnight_ist = now_ist.replace(
            hour=0,
            minute=0,
            second=0,
            microsecond=0
        )

        # With retention=1:
        #
        # cutoff = today's midnight
        #
        # Anything before today's midnight
        # belongs to yesterday or earlier.

        cutoff_dt = (
            today_midnight_ist
            - timedelta(
                days=LOCAL_RETENTION_DAYS - 1
            )
        )

        # Convert cutoff to timestamp.
        cutoff_ts = cutoff_dt.timestamp()

        deleted_files = 0
        deleted_bytes = 0

        print(
            f"[StorageRouter][Retention] "
            f"Running cleanup — "
            f"deleting files modified before "
            f"{cutoff_dt.strftime('%Y-%m-%d %H:%M:%S %Z')}"
        )

        try:

            for filepath in root.rglob("*"):

                if not filepath.is_file():
                    continue

                try:

                    mtime = filepath.stat().st_mtime

                    if mtime < cutoff_ts:

                        size = filepath.stat().st_size

                        filepath.unlink()

                        deleted_files += 1
                        deleted_bytes += size

                        print(
                            f"[StorageRouter][Retention] "
                            f"Deleted → {filepath}"
                        )

                except Exception as file_exc:

                    print(
                        f"[StorageRouter][Retention] "
                        f"Could not delete "
                        f"{filepath}: "
                        f"{file_exc}"
                    )

            # Remove empty directories
            for dirpath in sorted(
                root.rglob("*"),
                reverse=True
            ):

                if dirpath.is_dir():

                    try:

                        dirpath.rmdir()

                    except OSError:

                        pass

        except Exception as exc:

            print(
                f"[StorageRouter][Retention] "
                f"Cleanup error: {exc}"
            )

            traceback.print_exc()

            return

        print(
            f"[StorageRouter][Retention] "
            f"✅ Cleanup done — "
            f"deleted {deleted_files} file(s) "
            f"({deleted_bytes / 1024 / 1024:.2f} MB freed)"
        )