import json
import logging
import os
import subprocess
import tempfile
from typing import Literal

import boto3
from botocore.exceptions import ClientError
from urllib.parse import unquote_plus

logger = logging.getLogger()
logger.setLevel(logging.INFO)

S3 = boto3.client("s3")

CLAMSCAN_PATH = "/opt/bin/clamscan"
CLAM_DB_PATH = "/opt/var/lib/clamav"
SAFE_BUCKET = os.environ.get("SAFE_BUCKET")


def scan_file(file_path: str) -> Literal["CLEAN", "INFECTED", "ERROR"]:
    """Run ClamAV against a local file and map the exit code to a status string.

    Exit code mapping:
      0  -> CLEAN
      1  -> INFECTED
      >1 -> ERROR
    """
    try:
        logger.info("Running ClamAV scan on %s", file_path)
        result = subprocess.run(
            [
                CLAMSCAN_PATH,
                "--database",
                CLAM_DB_PATH,
                "--stdout",
                "--no-summary",
                file_path,
            ],
            capture_output=True,
            text=True,
            check=False,
        )

        logger.info("ClamAV stdout: %s", result.stdout.strip())
        if result.stderr:
            logger.warning("ClamAV stderr: %s", result.stderr.strip())

        if result.returncode == 0:
            return "CLEAN"
        if result.returncode == 1:
            return "INFECTED"

        logger.error("ClamAV exited with unexpected code %s", result.returncode)
        return "ERROR"

    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to run ClamAV: %s", exc)
        return "ERROR"


def lambda_handler(event, context):
    """Lambda entrypoint for S3 ObjectCreated events.

    Flow:
      1. Download object from RAW bucket to /tmp
      2. Scan with ClamAV
      3. Stamp scan_status metadata back onto the RAW object
      4. If CLEAN and SAFE_BUCKET is configured, copy the object into SAFE bucket
         (metadata replicated, including scan_status).
    """
    logger.info("Received event: %s", json.dumps(event))

    records = event.get("Records", [])
    if not records:
        logger.warning("No Records found in event")
        return {"statusCode": 400, "body": "No records"}

    for record in records:
        bucket = record["s3"]["bucket"]["name"]
        key = unquote_plus(record["s3"]["object"]["key"])

        logger.info("Processing object s3://%s/%s", bucket, key)

        with tempfile.NamedTemporaryFile() as tmp:
            tmp_file = tmp.name

            try:
                logger.info("Downloading object to %s", tmp_file)
                S3.download_file(bucket, key, tmp_file)
            except ClientError as err:
                logger.exception("Failed to download s3://%s/%s: %s", bucket, key, err)
                continue

            status = scan_file(tmp_file)

            # Read current metadata (may be empty if none set)
            try:
                head = S3.head_object(Bucket=bucket, Key=key)
                meta = head.get("Metadata", {}) or {}
            except ClientError as err:
                logger.exception(
                    "Failed to read metadata for s3://%s/%s: %s", bucket, key, err
                )
                meta = {}

            meta["scan_status"] = status

            # Always stamp RAW object with scan_status
            try:
                logger.info("Updating RAW object metadata with scan_status=%s", status)
                S3.copy_object(
                    Bucket=bucket,
                    Key=key,
                    CopySource={"Bucket": bucket, "Key": key},
                    Metadata=meta,
                    MetadataDirective="REPLACE",
                )
            except ClientError as err:
                logger.exception(
                    "Failed to update metadata for s3://%s/%s: %s", bucket, key, err
                )

            # If clean, promote into SAFE bucket so only clean files end up there
            if status == "CLEAN" and SAFE_BUCKET:
                try:
                    logger.info(
                        "File is CLEAN; copying s3://%s/%s to safe bucket s3://%s/%s",
                        bucket,
                        key,
                        SAFE_BUCKET,
                        key,
                    )
                    S3.copy_object(
                        Bucket=SAFE_BUCKET,
                        Key=key,
                        CopySource={"Bucket": bucket, "Key": key},
                        Metadata=meta,
                        MetadataDirective="REPLACE",
                    )
                except ClientError as err:
                    logger.exception(
                        "Failed to copy clean object to safe bucket s3://%s/%s: %s",
                        SAFE_BUCKET,
                        key,
                        err,
                    )
            else:
                if not SAFE_BUCKET:
                    logger.warning(
                        "SAFE_BUCKET env var is not set; skipping promotion for s3://%s/%s",
                        bucket,
                        key,
                    )
                else:
                    logger.info(
                        "File status is %s; not promoting s3://%s/%s to safe bucket",
                        status,
                        bucket,
                        key,
                    )

    return {"statusCode": 200, "body": "Scan completed"}
