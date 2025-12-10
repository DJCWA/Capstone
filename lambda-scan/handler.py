import os
import json
import logging
import datetime as dt

import boto3
from boto3.dynamodb.conditions import Key

# ----------------------------------------------------------------------
# Logging setup
# ----------------------------------------------------------------------

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# ----------------------------------------------------------------------
# AWS clients/resources
# ----------------------------------------------------------------------

SCAN_TABLE = os.environ.get("SCAN_TABLE")

if not SCAN_TABLE:
    logger.warning("SCAN_TABLE environment variable is not set. Lambda will fail.")

dynamodb = boto3.resource("dynamodb")


def get_scan_table():
    if not SCAN_TABLE:
        raise RuntimeError("SCAN_TABLE environment variable is not set.")
    return dynamodb.Table(SCAN_TABLE)


# ----------------------------------------------------------------------
# Helper functions
# ----------------------------------------------------------------------

def extract_file_id_from_key(s3_key: str) -> str | None:
    """
    Your backend uploads to keys like:
        uploads/{file_id}/{original_name}
    So we take the second path component as file_id.
    """
    parts = s3_key.split("/")
    if len(parts) < 3:
        logger.warning("Unexpected S3 key format: %s", s3_key)
        return None
    return parts[1]


def update_scan_record_to_clean(table, file_id: str):
    """
    Find the latest record for this file_id and mark it as CLEAN.
    If no record exists (edge case), this function will create one.
    """
    now = dt.datetime.utcnow()
    now_iso = now.replace(microsecond=0).isoformat() + "Z"

    # Try to get the latest record for this file_id
    try:
        resp = table.query(
            KeyConditionExpression=Key("file_id").eq(file_id),
            ScanIndexForward=False,  # newest first
            Limit=1,
            ConsistentRead=True,
        )
        items = resp.get("Items", [])
    except Exception as e:
        logger.exception("Failed to query DynamoDB for file_id=%s", file_id)
        raise

    if items:
        item = items[0]
        logger.info("Found existing scan record for file_id=%s", file_id)

        existing_events = item.get("scan_events", [])
        if not isinstance(existing_events, list):
            existing_events = []

        existing_events.append(
            {
                "timestamp": now_iso,
                "message": "Lambda stub scanner ran and marked file as CLEAN.",
            }
        )

        try:
            table.update_item(
                Key={
                    "file_id": item["file_id"],
                    "scan_timestamp": item["scan_timestamp"],
                },
                UpdateExpression=(
                    "SET scan_status = :s, "
                    "scan_detail = :d, "
                    "scan_events = :e"
                ),
                ExpressionAttributeValues={
                    ":s": "CLEAN",
                    ":d": "Stub scanner: file marked CLEAN (no AV engine executed).",
                    ":e": existing_events,
                },
            )
            logger.info("Updated scan record for file_id=%s to CLEAN", file_id)
        except Exception as e:
            logger.exception(
                "Failed to update scan record in DynamoDB for file_id=%s", file_id
            )
            raise
    else:
        # Edge case: no record exists yet, create one so /api/scan-status still works.
        logger.warning(
            "No existing scan record for file_id=%s, creating a new CLEAN record.", file_id
        )

        try:
            table.put_item(
                Item={
                    "file_id": file_id,
                    "scan_timestamp": now_iso,
                    "file_name": file_id,
                    "s3_bucket": "unknown",
                    "s3_key": "unknown",
                    "scan_status": "CLEAN",
                    "scan_detail": "Stub scanner created CLEAN record (no AV engine).",
                    "scan_events": [
                        {
                            "timestamp": now_iso,
                            "message": "Lambda stub scanner created this record.",
                        }
                    ],
                }
            )
        except Exception as e:
            logger.exception(
                "Failed to create new scan record in DynamoDB for file_id=%s", file_id
            )
            raise


# ----------------------------------------------------------------------
# Lambda entrypoint
# ----------------------------------------------------------------------

def lambda_handler(event, context):
    logger.info("Received event: %s", json.dumps(event))

    if "Records" not in event:
        logger.warning("No Records key in event; nothing to do.")
        return {"status": "ignored", "reason": "no Records in event"}

    table = get_scan_table()
    processed = 0
    errors = 0

    for record in event["Records"]:
        try:
            s3_info = record.get("s3", {})
            bucket = s3_info.get("bucket", {}).get("name")
            key = s3_info.get("object", {}).get("key")

            if not bucket or not key:
                logger.warning("Missing bucket/key in S3 event record: %s", record)
                errors += 1
                continue

            logger.info("Processing S3 object: bucket=%s key=%s", bucket, key)

            file_id = extract_file_id_from_key(key)
            if not file_id:
                logger.warning(
                    "Could not extract file_id from key=%s; skipping this record.", key
                )
                errors += 1
                continue

            update_scan_record_to_clean(table, file_id)
            processed += 1

        except Exception:
            errors += 1
            logger.exception("Error processing record: %s", record)

    result = {"status": "ok", "processed": processed, "errors": errors}
    logger.info("Lambda stub scan result: %s", result)
    return result
