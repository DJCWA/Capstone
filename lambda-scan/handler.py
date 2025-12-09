import os
import json
import logging
import subprocess
import urllib.parse
from datetime import datetime

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from boto3.dynamodb.conditions import Key

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# ---------------------------------------------------------------------------
# AWS setup
# ---------------------------------------------------------------------------

s3 = boto3.client("s3")
dynamodb = boto3.resource("dynamodb")

SCAN_TABLE = os.environ.get("SCAN_TABLE")
UPLOAD_BUCKET = os.environ.get("UPLOAD_BUCKET")
CLEAN_BUCKET = os.environ.get("CLEAN_BUCKET")

if not SCAN_TABLE:
    logger.warning("SCAN_TABLE env var is not set; Lambda will not be able to update scan records.")

scan_table = dynamodb.Table(SCAN_TABLE) if SCAN_TABLE else None


def now_iso() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


def extract_file_id_from_key(key: str) -> str | None:
    # Expect keys like uploads/<file_id>/<filename>
    parts = key.split("/")
    if len(parts) < 3 or parts[0] != "uploads":
        return None
    return parts[1]


def find_latest_scan_record(file_id: str) -> dict | None:
    if not scan_table:
        return None

    try:
        resp = scan_table.query(
            KeyConditionExpression=Key("file_id").eq(file_id),
            ScanIndexForward=False,
            Limit=1,
            ConsistentRead=True,
        )
        items = resp.get("Items", [])
        if items:
            logger.info("DynamoDB query for file_id=%s returned %d item(s)", file_id, len(items))
            return items[0]
        logger.warning("DynamoDB query for file_id=%s returned no items", file_id)
    except Exception as e:
        logger.exception("DynamoDB query failed for file_id=%s: %s", file_id, e)

    return None


def append_scan_result(item_key: dict, status: str, detail: str, extra_events: list[dict] | None = None):
    if not scan_table:
        logger.error("SCAN_TABLE not configured; cannot update scan record.")
        return

    now = now_iso()
    events = [{
        "timestamp": now,
        "message": f"Lambda scan completed with status {status}. {detail}",
    }]
    if extra_events:
        events.extend(extra_events)

    try:
        scan_table.update_item(
            Key=item_key,
            UpdateExpression=(
                "SET scan_status = :s, scan_detail = :d, updated_at = :u, "
                "scan_events = list_append(if_not_exists(scan_events, :empty), :ev)"
            ),
            ExpressionAttributeValues={
                ":s": status,
                ":d": detail,
                ":u": now,
                ":empty": [],
                ":ev": events,
            },
        )
        logger.info("Wrote scan result to DynamoDB: key=%s status=%s", item_key, status)
    except Exception as e:
        logger.exception("Failed to update DynamoDB with scan result: %s", e)


# ---------------------------------------------------------------------------
# ClamAV path detection
# ---------------------------------------------------------------------------

def detect_clamav_paths():
    """
    Try to find clamscan, lib dir, and DB dir under /opt.
    This handles both:
      - zip root = bin/lib64/share  ->  /opt/bin, /opt/lib64, /opt/share/clamav
      - zip root = opt/bin...       ->  /opt/opt/bin, /opt/opt/lib64, /opt/opt/share/clamav
    """
    bin_candidates = [
        "/opt/bin/clamscan",
        "/opt/opt/bin/clamscan",
        "/usr/bin/clamscan",  # just in case
    ]
    lib_candidates = [
        "/opt/lib64",
        "/opt/opt/lib64",
        "/opt/lib",
        "/opt/opt/lib",
    ]
    db_candidates = [
        "/opt/share/clamav",
        "/opt/opt/share/clamav",
    ]

    clam_bin = next((p for p in bin_candidates if os.path.isfile(p)), None)
    clam_lib = next((d for d in lib_candidates if os.path.isdir(d)), None)
    clam_db = next((d for d in db_candidates if os.path.isdir(d)), None)

    logger.info("Detected ClamAV paths: bin=%s lib=%s db=%s", clam_bin, clam_lib, clam_db)
    return clam_bin, clam_lib, clam_db


CLAMSCAN_PATH, CLAM_LIB_DIR, CLAM_DB_DIR = detect_clamav_paths()

# configure env based on what we actually found
if CLAM_LIB_DIR:
    os.environ["LD_LIBRARY_PATH"] = f"{CLAM_LIB_DIR}:{os.environ.get('LD_LIBRARY_PATH', '')}"
if CLAM_DB_DIR:
    os.environ["CLAMAVDB"] = CLAM_DB_DIR
os.environ["PATH"] = f"/opt/bin:{os.environ.get('PATH', '')}"


# ---------------------------------------------------------------------------
# Main handler
# ---------------------------------------------------------------------------

def lambda_handler(event, context):
    logger.info("Received event: %s", json.dumps(event))

    if not SCAN_TABLE:
        raise RuntimeError("SCAN_TABLE env var is not set")

    for record in event.get("Records", []):
        bucket = record["s3"]["bucket"]["name"]
        key = urllib.parse.unquote_plus(record["s3"]["object"]["key"])
        logger.info("Processing S3 object: bucket=%s key=%s", bucket, key)

        file_id = extract_file_id_from_key(key)
        if not file_id:
            logger.warning(
                "Object key did not match expected pattern 'uploads/<file_id>/<filename>': %s",
                key,
            )
            continue

        ddb_item = find_latest_scan_record(file_id)
        if not ddb_item:
            logger.warning("No DynamoDB record found for file_id=%s; skipping", file_id)
            continue

        item_key = {
            "file_id": ddb_item["file_id"],
            "scan_timestamp": ddb_item["scan_timestamp"],
        }

        # If clamscan is missing entirely, fail gracefully
        if not CLAMSCAN_PATH:
            detail = (
                "ClamAV binary not found under /opt. "
                "Check that the ClamAV Lambda layer is attached and packaged correctly."
            )
            append_scan_result(item_key, "ERROR", detail, [])
            continue

        # 1) Download object to /tmp
        local_path = f"/tmp/{os.path.basename(key)}"
        try:
            s3.download_file(bucket, key, local_path)
            logger.info("Downloaded S3 object to %s", local_path)
        except Exception as e:
            logger.exception("Failed to download S3 object: %s", e)
            append_scan_result(
                item_key,
                "ERROR",
                f"Failed to download object: {e}",
                [],
            )
            continue

        # 2) Mark IN_PROGRESS
        try:
            scan_table.update_item(
                Key=item_key,
                UpdateExpression=(
                    "SET scan_status = :s, scan_detail = :d, updated_at = :u, "
                    "scan_events = list_append(if_not_exists(scan_events, :empty), :ev)"
                ),
                ExpressionAttributeValues={
                    ":s": "IN_PROGRESS",
                    ":d": "Lambda is running ClamAV scan.",
                    ":u": now_iso(),
                    ":empty": [],
                    ":ev": [{
                        "timestamp": now_iso(),
                        "message": "Lambda scanner started for this file.",
                    }],
                },
            )
            logger.info("Marked record IN_PROGRESS for key=%s", item_key)
        except Exception as e:
            logger.exception("Failed to mark record IN_PROGRESS: %s", e)

        # 3) Run ClamAV
        cmd = [CLAMSCAN_PATH, "--stdout", "--infected", local_path]
        logger.info("Running ClamAV: %s", " ".join(cmd))

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=False,
            )
            exit_code = proc.returncode
            stdout = proc.stdout or ""
            stderr = proc.stderr or ""
            logger.info("ClamAV exit code=%s", exit_code)
        except Exception as e:
            logger.exception("Error executing clamscan: %s", e)
            append_scan_result(
                item_key,
                "ERROR",
                f"Exception while running clamscan: {e}",
                [],
            )
            continue

        stdout_tail = stdout[-2000:] if stdout else ""
        stderr_tail = stderr[-2000:] if stderr else ""

        # 4) Interpret result
        if exit_code == 0:
            status = "CLEAN"
            detail = "File is clean. ClamAV reported no malware."
        elif exit_code == 1:
            status = "INFECTED"
            threat = None
            for line in stdout.splitlines():
                if line.strip().endswith("FOUND"):
                    threat = line.strip()
                    break
            if not threat:
                threat = "Malware detected (see ClamAV output)."
            detail = threat
        else:
            status = "ERROR"
            detail = f"ClamAV error (exit code {exit_code})."

        extra_events = [
            {"timestamp": now_iso(), "message": f"ClamAV stdout: {stdout_tail}"},
            {"timestamp": now_iso(), "message": f"ClamAV stderr: {stderr_tail}"},
        ]

        # 5) Optional: copy CLEAN files to CLEAN_BUCKET
        if status == "CLEAN" and CLEAN_BUCKET and CLEAN_BUCKET != bucket:
            try:
                clean_key = key.replace("uploads/", "clean/", 1)
                s3.copy_object(
                    CopySource={"Bucket": bucket, "Key": key},
                    Bucket=CLEAN_BUCKET,
                    Key=clean_key,
                    ServerSideEncryption="AES256",
                )
                logger.info("Copied clean file to %s/%s", CLEAN_BUCKET, clean_key)
                extra_events.append({
                    "timestamp": now_iso(),
                    "message": f"Clean file copied to {CLEAN_BUCKET}/{clean_key}.",
                })
            except Exception as e:
                logger.exception("Failed to copy clean file to CLEAN_BUCKET: %s", e)
                extra_events.append({
                    "timestamp": now_iso(),
                    "message": f"Failed to copy clean file to CLEAN_BUCKET: {e}",
                })

        # 6) Final write
        append_scan_result(item_key, status, detail, extra_events)

    return {
        "statusCode": 200,
        "body": json.dumps({"message": "Scan processing complete"}),
    }
