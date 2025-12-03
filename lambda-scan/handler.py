import os
import json
import logging
import subprocess
from datetime import datetime, timezone
from urllib.parse import unquote_plus

import boto3
from botocore.exceptions import ClientError, BotoCoreError

# ------------------------------------------------------------------------------
# Logging setup
# ------------------------------------------------------------------------------

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# ------------------------------------------------------------------------------
# AWS clients and env
# ------------------------------------------------------------------------------

s3 = boto3.client("s3")
dynamodb = boto3.resource("dynamodb")

SCAN_TABLE_NAME = os.environ.get("SCAN_TABLE")
if not SCAN_TABLE_NAME:
    logger.error("SCAN_TABLE environment variable is not set. Lambda will not be able to write scan results.")

try:
    SCAN_TABLE = dynamodb.Table(SCAN_TABLE_NAME) if SCAN_TABLE_NAME else None
except Exception as e:
    logger.exception("Failed to bind DynamoDB table: %s", e)
    SCAN_TABLE = None

# ClamAV in the layer
CLAMAV_BIN = "/opt/bin/clamscan"
CLAMAV_DB_DIR = os.environ.get("CLAMAV_DB_DIR", "/opt/share/clamav")

# ------------------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------------------

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds") + "Z"


def extract_ids_from_key(key: str):
    """
    Expect keys like: uploads/{file_id}/{file_name}
    Example: uploads/87299813-1033-452e-a875-97fbb8cbbd49/test.txt
    """
    parts = key.split("/")
    file_name = parts[-1] if parts else key
    file_id = file_name

    if len(parts) >= 3:
        # "uploads", "{file_id}", "{file_name}"
        file_id = parts[1]

    return file_id, file_name


def run_clamav_scan(local_path: str):
    """
    Run ClamAV against the downloaded file and interpret result:
      - returncode 0 => CLEAN
      - returncode 1 => INFECTED
      - anything else => ERROR
    """
    start_ts = now_iso()
    cmd = [
        CLAMAV_BIN,
        "--stdout",
        "--infected",
        "-d",
        CLAMAV_DB_DIR,
        local_path,
    ]

    logger.info("Running ClamAV: %s", " ".join(cmd))

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True
        )
    except Exception as e:
        msg = f"Failed to execute clamscan: {e}"
        logger.exception(msg)
        return "ERROR", msg, [
            {"timestamp": start_ts, "message": msg}
        ]

    stdout = proc.stdout or ""
    stderr = proc.stderr or ""

    logger.info("ClamAV exit=%s", proc.returncode)
    if stdout:
        logger.info("ClamAV stdout: %s", stdout)
    if stderr:
        logger.info("ClamAV stderr: %s", stderr)

    events = [
        {"timestamp": start_ts, "message": "ClamAV scan started."}
    ]
    if stdout.strip():
        events.append(
            {
                "timestamp": now_iso(),
                "message": f"ClamAV output:\n{stdout[:3900]}",  # avoid huge payload
            }
        )
    if stderr.strip():
        events.append(
            {
                "timestamp": now_iso(),
                "message": f"ClamAV stderr:\n{stderr[:3900]}",
            }
        )

    if proc.returncode == 0:
        status = "CLEAN"
        detail = "File is clean (no threats detected by ClamAV)."
    elif proc.returncode == 1:
        status = "INFECTED"
        detail = "ClamAV detected one or more threats in the file."
    else:
        status = "ERROR"
        detail = f"ClamAV scan failed with return code {proc.returncode}."

    return status, detail, events


def write_scan_record(
    file_id: str,
    file_name: str,
    bucket: str,
    key: str,
    status: str,
    detail: str,
    extra_events=None,
):
    """
    Write a *new* record to the DynamoDB scan table.
    Backend will always read the latest scan_timestamp for a given file_id.
    """
    if SCAN_TABLE is None:
        logger.error("SCAN_TABLE not initialized, cannot write scan result.")
        return

    if extra_events is None:
        extra_events = []

    ts = now_iso()
    item = {
        "file_id": file_id,
        "scan_timestamp": ts,  # 🔴 REQUIRED sort key
        "file_name": file_name,
        "s3_bucket": bucket,
        "s3_key": key,
        "scan_status": status,
        "scan_detail": detail,
        "scan_events": extra_events,
        "created_at": ts,
        "updated_at": ts,
    }

    try:
        SCAN_TABLE.put_item(Item=item)
        logger.info("Wrote scan record to DynamoDB: %s", item)
    except (ClientError, BotoCoreError, Exception) as e:
        logger.exception("Failed to write scan record to DynamoDB: %s", e)


# ------------------------------------------------------------------------------
# Main Lambda handler
# ------------------------------------------------------------------------------

def lambda_handler(event, context):
    logger.info("Received event: %s", json.dumps(event))

    results = []

    for record in event.get("Records", []):
        try:
            bucket = record["s3"]["bucket"]["name"]
            key = unquote_plus(record["s3"]["object"]["key"])

            file_id, file_name = extract_ids_from_key(key)
            local_path = f"/tmp/{file_name}"

            logger.info("Processing s3://%s/%s (file_id=%s)", bucket, key, file_id)

            # 1) Download the file from S3
            try:
                s3.download_file(bucket, key, local_path)
                logger.info("Downloaded to %s", local_path)
            except (ClientError, BotoCoreError) as e:
                msg = f"Failed to download s3://{bucket}/{key}: {e}"
                logger.exception(msg)
                write_scan_record(
                    file_id,
                    file_name,
                    bucket,
                    key,
                    "ERROR",
                    msg,
                    [{"timestamp": now_iso(), "message": msg}],
                )
                results.append(
                    {
                        "file_id": file_id,
                        "status": "ERROR",
                        "message": msg,
                    }
                )
                continue

            # 2) Run ClamAV scan
            status, detail, events = run_clamav_scan(local_path)

            # 3) Write scan result to DynamoDB
            write_scan_record(
                file_id=file_id,
                file_name=file_name,
                bucket=bucket,
                key=key,
                status=status,
                detail=detail,
                extra_events=events,
            )

            results.append(
                {
                    "file_id": file_id,
                    "status": status,
                    "detail": detail,
                }
            )

        except Exception as e:
            logger.exception("Unhandled exception while processing record: %s", e)
            results.append(
                {
                    "status": "ERROR",
                    "detail": f"Unhandled exception: {e}",
                }
            )
        finally:
            # Try to remove local file if it exists
            try:
                if os.path.exists(local_path):
                    os.remove(local_path)
            except Exception:
                pass

    return {"results": results}