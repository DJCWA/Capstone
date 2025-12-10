import os
import json
import logging
import subprocess
import tempfile
import datetime as dt

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import BotoCoreError, ClientError

# -------------------------------------------------------------------
# Logging
# -------------------------------------------------------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# -------------------------------------------------------------------
# Environment
# -------------------------------------------------------------------
UPLOAD_BUCKET = os.environ.get("UPLOAD_BUCKET")
SCAN_TABLE = os.environ.get("SCAN_TABLE")

CLAMSCAN_PATH = "/opt/bin/clamscan"
FRESHCLAM_PATH = "/opt/bin/freshclam"
CLAMAV_DB_DIR = "/tmp/clamav"
LD_LIBRARY_PATH = "/opt/lib64"

if not UPLOAD_BUCKET:
    logger.warning("UPLOAD_BUCKET env var not set in Lambda.")
if not SCAN_TABLE:
    logger.warning("SCAN_TABLE env var not set in Lambda.")

s3 = boto3.client("s3")
dynamodb = boto3.resource("dynamodb")


def _get_table():
    if not SCAN_TABLE:
        raise RuntimeError("SCAN_TABLE env var is not set in Lambda.")
    return dynamodb.Table(SCAN_TABLE)


# -------------------------------------------------------------------
# Helper: safe DynamoDB event append
# -------------------------------------------------------------------
def append_event(file_id: str, message: str):
    """Append a scan_events entry to the latest record for this file_id."""
    try:
        table = _get_table()
        now_iso = dt.datetime.utcnow().isoformat(timespec="seconds") + "Z"

        # Find the latest record for this file_id (by scan_timestamp)
        resp = table.query(
            KeyConditionExpression=Key("file_id").eq(file_id),
            ScanIndexForward=False,
            Limit=1,
            ConsistentRead=True,
        )
        items = resp.get("Items", [])
        if not items:
            logger.warning("append_event: no record found for file_id=%s", file_id)
            return

        item = items[0]
        scan_timestamp = item["scan_timestamp"]

        table.update_item(
            Key={
                "file_id": file_id,
                "scan_timestamp": scan_timestamp,
            },
            UpdateExpression=(
                "SET updated_at = :u, "
                "scan_events = list_append(if_not_exists(scan_events, :empty), :ev)"
            ),
            ExpressionAttributeValues={
                ":u": now_iso,
                ":empty": [],
                ":ev": [
                    {
                        "timestamp": now_iso,
                        "message": message,
                    }
                ],
            },
        )
    except Exception as e:
        logger.exception("Failed to append_event for file_id=%s: %s", file_id, e)


# -------------------------------------------------------------------
# Helper: ensure ClamAV DB is in /tmp/clamav
# -------------------------------------------------------------------
def ensure_clamav_db():
    """
    Fetch or update the ClamAV DB in /tmp/clamav using freshclam.
    This keeps the Lambda layer small; DB lives in ephemeral /tmp.
    """
    os.makedirs(CLAMAV_DB_DIR, exist_ok=True)

    env = os.environ.copy()
    env["LD_LIBRARY_PATH"] = LD_LIBRARY_PATH

    logger.info("Running freshclam to update DB in %s", CLAMAV_DB_DIR)

    try:
        result = subprocess.run(
            [FRESHCLAM_PATH, f"--datadir={CLAMAV_DB_DIR}"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
            timeout=120,  # give it some time
        )
    except Exception as e:
        raise RuntimeError(f"Error running freshclam: {e}")

    # 0 = DB updated, 1 = already up to date, >1 = error
    if result.returncode not in (0, 1):
        raise RuntimeError(
            f"freshclam failed (rc={result.returncode}). "
            f"stdout={result.stdout} stderr={result.stderr}"
        )

    logger.info("freshclam completed with rc=%s", result.returncode)


# -------------------------------------------------------------------
# Helper: run clamscan on a local file path
# -------------------------------------------------------------------
def run_clamscan(local_path: str):
    env = os.environ.copy()
    env["LD_LIBRARY_PATH"] = LD_LIBRARY_PATH

    logger.info("Running clamscan on %s", local_path)

    result = subprocess.run(
        [
            CLAMSCAN_PATH,
            f"--database={CLAMAV_DB_DIR}",
            "-i",  # only print infected files
            local_path,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
        timeout=120,
    )

    logger.info("clamscan rc=%s", result.returncode)

    stdout = result.stdout or ""
    stderr = result.stderr or ""

    if result.returncode == 0:
        status = "CLEAN"
        detail = "No malware detected by ClamAV."
    elif result.returncode == 1:
        status = "INFECTED"
        detail = "Malware detected by ClamAV."
    else:
        status = "ERROR"
        detail = f"ClamAV error (exit code {result.returncode})."

    return status, detail, stdout, stderr


# -------------------------------------------------------------------
# Lambda handler (triggered by S3 object-created event)
# -------------------------------------------------------------------
def lambda_handler(event, context):
    logger.info("Received event: %s", json.dumps(event))

    # S3 put event(s)
    records = event.get("Records", [])
    results = []

    for rec in records:
        try:
            s3_info = rec.get("s3", {})
            bucket = s3_info.get("bucket", {}).get("name")
            key = s3_info.get("object", {}).get("key")

            if not bucket or not key:
                logger.warning("Missing bucket or key in event record.")
                continue

            # Our key pattern: uploads/{file_id}/{file_name}
            parts = key.split("/")
            file_id = None
            file_name = None
            if len(parts) >= 3 and parts[0] == "uploads":
                file_id = parts[1]
                file_name = "/".join(parts[2:])
            else:
                logger.warning(
                    "Key does not match expected pattern 'uploads/<file_id>/<file_name>': %s",
                    key,
                )

            logger.info("Processing S3 object: bucket=%s key=%s file_id=%s", bucket, key, file_id)

            local_path = os.path.join(tempfile.gettempdir(), "scan_input")
            s3.download_file(bucket, key, local_path)
            logger.info("Downloaded object to %s", local_path)

            if file_id:
                append_event(file_id, "Lambda scanner started for this file.")

            # Ensure DB is present/updated
            try:
                ensure_clamav_db()
            except Exception as e:
                logger.exception("Failed to update ClamAV DB.")
                if file_id:
                    append_event(
                        file_id,
                        f"Failed to update ClamAV DB: {e}",
                    )
                raise

            # Run clamscan
            status, detail, stdout, stderr = run_clamscan(local_path)

            # Update DynamoDB record
            if file_id:
                try:
                    table = _get_table()
                    # Find latest record for this file_id
                    resp = table.query(
                        KeyConditionExpression=Key("file_id").eq(file_id),
                        ScanIndexForward=False,
                        Limit=1,
                        ConsistentRead=True,
                    )
                        # Note: scan_events is our event list
                    items = resp.get("Items", [])
                    if items:
                        item = items[0]
                        scan_timestamp = item["scan_timestamp"]

                        now_iso = dt.datetime.utcnow().isoformat(timespec="seconds") + "Z"

                        new_events = [
                            {
                                "timestamp": now_iso,
                                "message": (
                                    f"Lambda scan completed with status {status}. {detail}"
                                ),
                            },
                            {
                                "timestamp": now_iso,
                                "message": f"ClamAV stdout: {stdout[:2048]}",
                            },
                            {
                                "timestamp": now_iso,
                                "message": f"ClamAV stderr: {stderr[:2048]}",
                            },
                        ]

                        table.update_item(
                            Key={
                                "file_id": file_id,
                                "scan_timestamp": scan_timestamp,
                            },
                            UpdateExpression=(
                                "SET scan_status = :s, "
                                "scan_detail = :d, "
                                "updated_at = :u, "
                                "scan_events = list_append(if_not_exists(scan_events, :empty), :ev)"
                            ),
                            ExpressionAttributeValues={
                                ":s": status,
                                ":d": detail,
                                ":u": now_iso,
                                ":empty": [],
                                ":ev": new_events,
                            },
                        )
                    else:
                        logger.warning("No DynamoDB record found for file_id=%s", file_id)
                except Exception as e:
                    logger.exception("Failed to update DynamoDB for file_id=%s", file_id)

            results.append(
                {
                    "bucket": bucket,
                    "key": key,
                    "file_id": file_id,
                    "file_name": file_name,
                    "status": status,
                    "detail": detail,
                }
            )

        except Exception as e:
            logger.exception("Unhandled error processing record: %s", e)
            results.append(
                {
                    "error": str(e),
                }
            )

    return {
        "results": results,
    }
