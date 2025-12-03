import json
import logging
import os
import subprocess
import tempfile
from urllib.parse import unquote_plus

import boto3
from botocore.exceptions import ClientError

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# AWS clients
s3 = boto3.client("s3")

# Paths provided by the ClamAV Lambda layer
CLAMSCAN_PATH = "/opt/bin/clamscan"
CLAM_DB_PATH = "/opt/share/clamav"  # matches how the layer was built

# Destination bucket for CLEAN files
SAFE_BUCKET = os.environ.get("SAFE_BUCKET")


def run_clam_scan(file_path: str) -> str:
    """
    Run ClamAV scan on the given file and return one of:
      - "CLEAN"
      - "INFECTED"
      - "ERROR"
    """
    cmd = [
        CLAMSCAN_PATH,
        "--database",
        CLAM_DB_PATH,
        "--no-summary",
        "--stdout",
        file_path,
    ]

    logger.info("Running ClamAV: %s", " ".join(cmd))
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,  # we handle non-zero exit codes ourselves
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to execute clamscan: %s", exc)
        return "ERROR"

    if result.stdout:
        logger.info("clamscan stdout: %s", result.stdout.strip())
    if result.stderr:
        logger.warning("clamscan stderr: %s", result.stderr.strip())

    # Exit code semantics:
    #   0 -> no virus found
    #   1 -> virus(es) found
    #  >1 -> error
    if result.returncode == 0:
        return "CLEAN"
    if result.returncode == 1:
        return "INFECTED"

    logger.error("clamscan exited with unexpected code %s", result.returncode)
    return "ERROR"


def lambda_handler(event, context):
    """
    Triggered by S3 ObjectCreated events on the RAW uploads bucket.

    For each object:
      1. Download object to /tmp
      2. Scan with ClamAV
      3. Stamp scan_status metadata back on the RAW object
      4. If CLEAN and SAFE_BUCKET is configured, copy to SAFE bucket
    """
    logger.info("Received event: %s", json.dumps(event))

    records = event.get("Records", [])
    if not records:
        logger.warning("No Records in event")
        return {"statusCode": 400, "body": "No records to process"}

    for record in records:
        bucket = record["s3"]["bucket"]["name"]
        key = unquote_plus(record["s3"]["object"]["key"])
        logger.info("Processing s3://%s/%s", bucket, key)

        # 1. Download to a temp file
        with tempfile.NamedTemporaryFile() as tmp:
            tmp_path = tmp.name
            try:
                logger.info("Downloading object to %s", tmp_path)
                s3.download_file(bucket, key, tmp_path)
            except ClientError as err:
                logger.exception(
                    "Failed to download s3://%s/%s: %s", bucket, key, err
                )
                continue

            # 2. Run ClamAV scan
            status = run_clam_scan(tmp_path)
            logger.info("Scan result for s3://%s/%s -> %s", bucket, key, status)

        # 3. Read existing metadata and stamp scan_status back on RAW object
        try:
            head = s3.head_object(Bucket=bucket, Key=key)
            metadata = head.get("Metadata", {}) or {}
        except ClientError as err:
            logger.warning(
                "Could not read metadata for s3://%s/%s: %s", bucket, key, err
            )
            metadata = {}

        metadata["scan_status"] = status

        try:
            logger.info("Updating RAW object metadata with scan_status=%s", status)
            s3.copy_object(
                Bucket=bucket,
                Key=key,
                CopySource={"Bucket": bucket, "Key": key},
                Metadata=metadata,
                MetadataDirective="REPLACE",
            )
        except ClientError as err:
            logger.warning(
                "Failed to update RAW object metadata for s3://%s/%s: %s",
                bucket,
                key,
                err,
            )

        # 4. If CLEAN, copy to SAFE bucket (this is your "duplicate if successful")
        if status == "CLEAN":
            if not SAFE_BUCKET:
                logger.warning(
                    "SAFE_BUCKET env var is not set; CLEAN object will not be promoted"
                )
            else:
                try:
                    logger.info(
                        "Copying CLEAN object to safe bucket s3://%s/%s",
                        SAFE_BUCKET,
                        key,
                    )
                    s3.copy_object(
                        Bucket=SAFE_BUCKET,
                        Key=key,
                        CopySource={"Bucket": bucket, "Key": key},
                        Metadata=metadata,
                        MetadataDirective="REPLACE",
                    )
                except ClientError as err:
                    logger.exception(
                        "Failed to copy CLEAN object to safe bucket s3://%s/%s: %s",
                        SAFE_BUCKET,
                        key,
                        err,
                    )
        else:
            logger.warning(
                "Object s3://%s/%s is %s; not copying to SAFE bucket",
                bucket,
                key,
                status,
            )

    return {"statusCode": 200, "body": "Scan complete"}
