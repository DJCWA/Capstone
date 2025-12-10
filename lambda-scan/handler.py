import os
import json
import logging
import tempfile
import subprocess
from datetime import datetime, timezone

import boto3
from botocore.exceptions import ClientError, BotoCoreError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

dynamodb = boto3.resource("dynamodb")
s3 = boto3.client("s3")

SCAN_TABLE = os.environ.get("SCAN_TABLE")
REGION = os.environ.get("REGION", "ca-central-1")

# ClamAV paths inside the Lambda layer
CLAMSCAN_CANDIDATES = [
    "/opt/bin/clamscan",
    "/opt/opt/bin/clamscan",  # just in case
]
FRESHCLAM_CANDIDATES = [
    "/opt/bin/freshclam",
    "/opt/opt/bin/freshclam",
]

# Where we'll store the DB at runtime (NOT in the layer, so it doesn't count toward 250MB)
CLAMAV_DB_DIR = os.environ.get("CLAMAV_DB_DIR", "/tmp/clamav")

# Globals so we only update DB once per warm container
DB_READY = False


def _find_executable(candidates):
    for path in candidates:
        if os.path.isfile(path) and os.access(path, os.X_OK):
            logger.info("Using executable: %s", path)
            return path
    return None


CLAMSCAN = _find_executable(CLAMSCAN_CANDIDATES)
FRESHCLAM = _find_executable(FRESHCLAM_CANDIDATES)

if not CLAMSCAN:
    logger.error("clamscan executable not found in layer. Make sure layer has bin/clamscan.")
if not FRESHCLAM:
    logger.warning("freshclam executable not found in layer. We won't be able to update DB at runtime.")


def _append_event(table, file_id, message):
    """Append a scan_events entry in DynamoDB for this file."""
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")

    try:
        table.update_item(
            Key={"file_id": file_id},
            UpdateExpression=(
                "SET updated_at = :u, "
                "    scan_events = list_append(if_not_exists(scan_events, :empty_list), :new_event)"
            ),
            ExpressionAttributeValues={
                ":u": now_iso,
                ":empty_list": [],
                ":new_event": [
                    {
                        "timestamp": now_iso,
                        "message": message,
                    }
                ],
            },
        )
    except Exception as e:
        logger.exception("Failed to append event to DynamoDB for file_id=%s", file_id)


def _set_status(table, file_id, status, detail):
    """Set scan_status + scan_detail + updated_at in DynamoDB."""
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")

    try:
        table.update_item(
            Key={"file_id": file_id},
            UpdateExpression=(
                "SET scan_status = :s, "
                "    scan_detail = :d, "
                "    updated_at = :u"
            ),
            ExpressionAttributeValues={
                ":s": status,
                ":d": detail,
                ":u": now_iso,
            },
        )
    except Exception as e:
        logger.exception("Failed to set status in DynamoDB for file_id=%s", file_id)


def _ensure_clamav_db():
    """
    Ensure ClamAV DB exists in CLAMAV_DB_DIR (/tmp/clamav by default).
    If not present, call freshclam to download it.
    """
    global DB_READY

    if DB_READY:
        return

    os.makedirs(CLAMAV_DB_DIR, exist_ok=True)

    # If we already have any .cvd/.cld, assume DB is present
    existing = [
        f for f in os.listdir(CLAMAV_DB_DIR)
        if f.endswith(".cvd") or f.endswith(".cld")
    ]
    if existing:
        logger.info("ClamAV DB already present in %s: %s", CLAMAV_DB_DIR, existing)
        DB_READY = True
        return

    if not FRESHCLAM:
        logger.warning(
            "freshclam is not available; cannot download ClamAV DB. "
            "clamscan will likely fail."
        )
        return

    logger.info("No ClamAV DB in %s; running freshclam to download it...", CLAMAV_DB_DIR)

    try:
        result = subprocess.run(
            [FRESHCLAM, f"--datadir={CLAMAV_DB_DIR}"],
            capture_output=True,
            text=True,
            timeout=180,  # allow some time for download
        )
        logger.info("freshclam exit code=%s", result.returncode)
        logger.info("freshclam stdout: %s", result.stdout)
        logger.info("freshclam stderr: %s", result.stderr)

        if result.returncode != 0:
            logger.warning("freshclam returned non-zero exit code %s", result.returncode)
        else:
            logger.info("freshclam completed successfully.")

        # Mark DB as ready if we now have any DB files
        existing = [
            f for f in os.listdir(CLAMAV_DB_DIR)
            if f.endswith(".cvd") or f.endswith(".cld")
        ]
        if existing:
            DB_READY = True
            logger.info("ClamAV DB files after freshclam: %s", existing)
        else:
            logger.warning("No .cvd/.cld files found in %s after freshclam.", CLAMAV_DB_DIR)

    except subprocess.TimeoutExpired:
        logger.exception("freshclam timed out.")
    except Exception:
        logger.exception("Exception while running freshclam.")


def lambda_handler(event, context):
    """
    S3 PUT trigger:
    - Reads bucket/key from event
    - Infers file_id from key path: uploads/<file_id>/<filename>
    - Downloads file to /tmp
    - Ensures ClamAV DB in /tmp/clamav (freshclam if needed)
    - Runs clamscan
    - Writes result back to DynamoDB row with matching file_id
    """
    logger.info("Received event: %s", json.dumps(event))

    if not SCAN_TABLE:
        logger.error("SCAN_TABLE env var is not set.")
        return {"statusCode": 500, "body": "SCAN_TABLE env var not set."}

    table = dynamodb.Table(SCAN_TABLE)

    records = event.get("Records", [])
    for record in records:
        try:
            bucket = record["s3"]["bucket"]["name"]
            key = record["s3"]["object"]["key"]
        except KeyError:
            logger.error("Malformed S3 event record: %s", record)
            continue

        logger.info("Processing S3 object: bucket=%s key=%s", bucket, key)

        # Expect keys like: uploads/<file_id>/<filename>
        parts = key.split("/")
        if len(parts) < 3 or parts[0] != "uploads":
            logger.warning("Key does not match expected pattern uploads/<file_id>/<filename>: %s", key)
            continue

        file_id = parts[1]
        filename = parts[-1]

        _append_event(table, file_id, "Lambda scanner started for this file.")

        # Download the object to a temporary file
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            tmp_path = tmp.name

        try:
            logger.info("Downloading S3 object to %s", tmp_path)
            s3.download_file(bucket, key, tmp_path)
        except (BotoCoreError, ClientError) as e:
            logger.exception("Failed to download from S3.")
            _set_status(table, file_id, "ERROR", f"Failed to download file from S3: {e}")
            _append_event(table, file_id, f"S3 download error: {e}")
            continue

        # Ensure DB exists in /tmp/clamav
        _ensure_clamav_db()

        if not CLAMSCAN:
            msg = "clamscan executable not found in Lambda layer."
            logger.error(msg)
            _set_status(table, file_id, "ERROR", msg)
            _append_event(table, file_id, msg)
            continue

        # Build clamscan command
        cmd = [
            CLAMSCAN,
            "--stdout",
            f"--database={CLAMAV_DB_DIR}",
            tmp_path,
        ]

        logger.info("Running clamscan: %s", " ".join(cmd))

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=180,
            )
            exit_code = result.returncode
            stdout = result.stdout or ""
            stderr = result.stderr or ""

            logger.info("clamscan exit=%s", exit_code)
            logger.info("clamscan stdout: %s", stdout)
            logger.info("clamscan stderr: %s", stderr)

            # Store stdout/stderr as events for debugging
            _append_event(table, file_id, f"ClamAV stdout: {stdout[:1000]}")
            _append_event(table, file_id, f"ClamAV stderr: {stderr[:1000]}")

            if exit_code == 0:
                status = "CLEAN"
                detail = "File is clean according to ClamAV."
            elif exit_code == 1:
                status = "INFECTED"
                detail = "Malware detected by ClamAV."
            else:
                status = "ERROR"
                detail = f"ClamAV error (exit code {exit_code})."

            _set_status(table, file_id, status, detail)
            _append_event(table, file_id, f"Lambda scan completed with status {status}. {detail}")

        except subprocess.TimeoutExpired:
            logger.exception("clamscan timed out.")
            _set_status(table, file_id, "ERROR", "ClamAV scan timed out.")
            _append_event(table, file_id, "ClamAV scan timed out.")
        except Exception as e:
            logger.exception("Unhandled exception while running clamscan.")
            _set_status(table, file_id, "ERROR", f"Exception while running clamscan: {e}")
            _append_event(table, file_id, f"Exception while running clamscan: {e}")
        finally:
            try:
                os.remove(tmp_path)
            except OSError:
                pass

    return {"statusCode": 200, "body": "Scan processing complete."}
