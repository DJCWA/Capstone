import os
import boto3
import logging
import tempfile
import subprocess
from urllib.parse import unquote_plus
from botocore.exceptions import ClientError

s3 = boto3.client("s3")
logger = logging.getLogger()
logger.setLevel(logging.INFO)

CLAMSCAN_PATH = "/opt/bin/clamscan"
CLAM_DB_PATH = "/opt/share/clamav"

FINAL_STATUSES = {"CLEAN", "INFECTED", "ERROR"}


def scan_file(path: str) -> str:
    """
    Run ClamAV on the given file path.
    Returns: 'CLEAN', 'INFECTED', or 'ERROR'
    """
    if not os.path.exists(CLAMSCAN_PATH):
        logger.error("clamscan binary not found at %s", CLAMSCAN_PATH)
        return "ERROR"

    cmd = [
        CLAMSCAN_PATH,
        "--stdout",
        f"--database={CLAM_DB_PATH}",
        path,
    ]

    logger.info("Running ClamAV: %s", " ".join(cmd))

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
        )
    except Exception as e:
        logger.error("Error running clamscan: %s", e)
        return "ERROR"

    logger.info("ClamAV stdout: %s", result.stdout)
    logger.info("ClamAV stderr: %s", result.stderr)
    logger.info("ClamAV exit code: %s", result.returncode)

    # 0 = OK, 1 = infected, >1 = error
    if result.returncode == 0:
        return "CLEAN"
    elif result.returncode == 1:
        return "INFECTED"
    else:
        return "ERROR"


def lambda_handler(event, context):
    logger.info("Received event: %s", event)

    for record in event.get("Records", []):
        bucket = record["s3"]["bucket"]["name"]
        raw_key = record["s3"]["object"]["key"]
        key = unquote_plus(raw_key)

        logger.info("Processing s3://%s/%s (raw key: %s)", bucket, key, raw_key)

        # Check existing metadata so we don't rescan endlessly
        try:
            head = s3.head_object(Bucket=bucket, Key=key)
        except ClientError as e:
            logger.error("head_object failed for %s: %s", key, e)
            continue

        metadata = head.get("Metadata", {}) or {}
        existing_status = metadata.get("scan_status")

        if existing_status in FINAL_STATUSES:
            logger.info(
                "Object %s already has final status %s, skipping", key, existing_status
            )
            continue

        # Stage 1: Lambda picked up the event
        _update_scan_status(
            bucket,
            key,
            status="SCANNING",
            detail="Scan started in Lambda…",
        )

        # Download to /tmp
        tmp_dir = tempfile.gettempdir()
        tmp_path = os.path.join(tmp_dir, os.path.basename(key) or "scanfile")

        try:
            _update_scan_status(
                bucket,
                key,
                status="SCANNING",
                detail="Downloading file from S3…",
            )
            s3.download_file(bucket, key, tmp_path)
        except ClientError as e:
            logger.error(
                "Failed to download object %s from bucket %s: %s", key, bucket, e
            )
            _update_scan_status(
                bucket,
                key,
                status="ERROR",
                detail="Failed to download file from S3.",
            )
            continue

        # Stage 2: running ClamAV
        _update_scan_status(
            bucket,
            key,
            status="SCANNING",
            detail="Running ClamAV antivirus scan…",
        )

        status = scan_file(tmp_path)

        if status == "CLEAN":
            detail = "Scan completed: no threats detected."
        elif status == "INFECTED":
            detail = "Scan completed: file is INFECTED. See CloudWatch logs for details."
        else:
            detail = "Scan failed due to an internal error during antivirus scan."

        _update_scan_status(bucket, key, status=status, detail=detail)

    return {"status": "ok"}


def _update_scan_status(bucket: str, key: str, status: str, detail: str | None = None):
    """
    Updates S3 object metadata with scan_status and scan_detail.
    Uses copy_object onto itself. Because this generates another
    ObjectCreated event, the handler has a guard to exit early
    when a final status is present.
    """
    try:
        head = s3.head_object(Bucket=bucket, Key=key)
    except ClientError as e:
        logger.error("head_object in _update_scan_status failed for %s: %s", key, e)
        return

    metadata = head.get("Metadata", {}) or {}
    metadata["scan_status"] = status
    if detail:
        metadata["scan_detail"] = detail

    s3.copy_object(
        Bucket=bucket,
        Key=key,
        CopySource={"Bucket": bucket, "Key": key},
        Metadata=metadata,
        MetadataDirective="REPLACE",
    )

    logger.info(
        "Updated s3://%s/%s with scan_status=%s, scan_detail=%s",
        bucket,
        key,
        status,
        detail,
    )
