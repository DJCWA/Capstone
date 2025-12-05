import os
import uuid
import logging
import datetime as dt

from flask import Flask, request, jsonify
import boto3
from botocore.exceptions import BotoCoreError, ClientError
from boto3.dynamodb.conditions import Key, Attr  # ✅ For scan-status fallback

# ----------------------------------------------------------------------
# Basic Flask + logging setup
# ----------------------------------------------------------------------

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# ----------------------------------------------------------------------
# AWS config – must match ECS task environment variables
# ----------------------------------------------------------------------

UPLOAD_BUCKET = os.environ.get("UPLOAD_BUCKET")  # S3 bucket for uploaded files
SCAN_TABLE = os.environ.get("SCAN_TABLE")        # DynamoDB table for scan records

if not UPLOAD_BUCKET:
    logger.warning("UPLOAD_BUCKET env var is not set – uploads will fail until this is configured.")
if not SCAN_TABLE:
    logger.warning("SCAN_TABLE env var is not set – scan-status will fail until this is configured.")

s3 = boto3.client("s3")
dynamodb = boto3.resource("dynamodb")


def get_scan_table():
    if not SCAN_TABLE:
        raise RuntimeError("SCAN_TABLE env var not set in backend container.")
    return dynamodb.Table(SCAN_TABLE)


# ----------------------------------------------------------------------
# Helper: build consistent JSON error responses
# ----------------------------------------------------------------------

def error_response(message: str, status_code: int = 500):
    logger.error("API error (%s): %s", status_code, message)
    return jsonify({"error": message}), status_code


# ----------------------------------------------------------------------
# Health check endpoints
# ----------------------------------------------------------------------

@app.route("/api/health", methods=["GET"])
def debug_health():
    """
    Debug-style health endpoint (shows config).
    Useful to curl from inside the container.
    """
    return jsonify(
        {
            "status": "ok",
            "upload_bucket": UPLOAD_BUCKET,
            "scan_table": SCAN_TABLE,
        }
    ), 200


@app.route("/health", methods=["GET"])
def alb_health():
    """
    Minimal health endpoint for the ALB target group.
    ALB is configured to call /health and expects 200-399.
    We keep this very lightweight.
    """
    return jsonify({"status": "ok"}), 200


# ----------------------------------------------------------------------
# Route: /api/upload – receives file, stores to S3, creates PENDING record in DynamoDB
# ----------------------------------------------------------------------

@app.route("/api/upload", methods=["POST"])
def upload_file():
    try:
        if not UPLOAD_BUCKET:
            return error_response("UPLOAD_BUCKET env var is not configured on backend service.", 500)

        if "file" not in request.files:
            return error_response("No file part in request. Expected field name 'file'.", 400)

        file_storage = request.files["file"]
        if file_storage.filename == "":
            return error_response("Empty filename – please select a file.", 400)

        original_name = file_storage.filename
        file_id = str(uuid.uuid4())
        s3_key = f"uploads/{file_id}/{original_name}"

        logger.info("Uploading file to S3: bucket=%s key=%s", UPLOAD_BUCKET, s3_key)

        # Upload file to S3
        try:
            s3.upload_fileobj(
                Fileobj=file_storage,
                Bucket=UPLOAD_BUCKET,
                Key=s3_key,
                ExtraArgs={"ServerSideEncryption": "AES256"},
            )
        except (BotoCoreError, ClientError) as e:
            logger.exception("Failed to upload file to S3.")
            return error_response(f"Failed to upload file to S3: {e}", 500)

        # Create PENDING scan record in DynamoDB
        try:
            table = get_scan_table()
            now_iso = dt.datetime.utcnow().isoformat(timespec="seconds") + "Z"
            scan_timestamp = now_iso  # sort key for DynamoDB

            table.put_item(
                Item={
                    # ✅ These two are the KEY attributes in your table
                    "file_id": file_id,
                    "scan_timestamp": scan_timestamp,

                    "file_name": original_name,
                    "s3_bucket": UPLOAD_BUCKET,
                    "s3_key": s3_key,
                    "scan_status": "PENDING",
                    "scan_detail": "Waiting for Lambda scanner to run.",
                    "scan_events": [
                        {
                            "timestamp": now_iso,
                            "message": "File uploaded to S3 and scan record created.",
                        }
                    ],
                    "created_at": now_iso,
                    "updated_at": now_iso,
                }
            )
        except (BotoCoreError, ClientError, RuntimeError) as e:
            logger.exception("Failed to write scan record to DynamoDB.")
            return error_response(f"Failed to write scan record to DynamoDB: {e}", 500)

        logger.info("Upload + PENDING record created. file_id=%s", file_id)

        return jsonify(
            {
                "file_id": file_id,
                "file_name": original_name,
                "status": "PENDING",
                "detail": "File uploaded. Waiting for scan to start.",
            }
        ), 200

    except Exception as e:
        # Catch-all so we NEVER return HTML 500, always JSON
        logger.exception("Unhandled exception in /api/upload")
        return error_response(f"Unhandled exception in /api/upload: {e}", 500)


# ----------------------------------------------------------------------
# Route: /api/scan-status – returns scan status + events from DynamoDB
# 1) Tries Query (fast if key schema matches)
# 2) Falls back to Scan + FilterExpression on file_id
# ----------------------------------------------------------------------

@app.route("/api/scan-status", methods=["GET"])
def scan_status():
    try:
        file_id = request.args.get("file_id")
        if not file_id:
            return error_response("Missing required query parameter 'file_id'.", 400)

        if not SCAN_TABLE:
            return error_response("SCAN_TABLE env var is not configured on backend service.", 500)

        logger.info("Fetching scan status for file_id=%s", file_id)

        try:
            table = get_scan_table()

            # ---- Fast path: Query on partition key (if schema matches) ----
            items = []
            try:
                resp = table.query(
                    KeyConditionExpression=Key("file_id").eq(file_id),
                    ScanIndexForward=False,  # newest first if sort key exists
                    Limit=1,
                    ConsistentRead=True,
                )
                items = resp.get("Items", [])
                logger.info("Query result for file_id=%s: %d item(s)", file_id, len(items))
            except Exception as qe:
                logger.warning(
                    "Query on file_id=%s failed, will fall back to Scan. Error: %s",
                    file_id,
                    qe,
                )
                items = []

            # ---- Fallback: Scan + filter by file_id (works even if key schema differs) ----
            if not items:
                resp = table.scan(
                    FilterExpression=Attr("file_id").eq(file_id),
                    ConsistentRead=True,
                )
                items = resp.get("Items", [])
                logger.info("Scan result for file_id=%s: %d item(s)", file_id, len(items))

        except (BotoCoreError, ClientError, RuntimeError) as e:
            logger.exception("Failed to read scan record from DynamoDB.")
            return error_response(f"Failed to read scan record from DynamoDB: {e}", 500)

        if not items:
            return error_response(f"No record found for file_id={file_id}", 404)

        item = items[0]

        status = item.get("scan_status", "UNKNOWN")
        detail = item.get("scan_detail", "No detail provided.")
        events = item.get("scan_events", [])

        # Normalize events for frontend
        normalized_events = []
        for e in events:
            if isinstance(e, dict):
                normalized_events.append(
                    {
                        "timestamp": e.get("timestamp", ""),
                        "message": e.get("message", ""),
                    }
                )
            else:
                normalized_events.append(
                    {
                        "timestamp": "",
                        "message": str(e),
                    }
                )

        return jsonify(
            {
                "file_id": item.get("file_id", file_id),
                "file_name": item.get("file_name", ""),
                "status": status,
                "detail": detail,
                "events": normalized_events,
            }
        ), 200

    except Exception as e:
        logger.exception("Unhandled exception in /api/scan-status")
        return error_response(f"Unhandled exception in /api/scan-status: {e}", 500)


# ----------------------------------------------------------------------
# Local dev entrypoint (ECS uses gunicorn or similar)
# ----------------------------------------------------------------------

if __name__ == "__main__":
    # For local testing only (ECS will bind via gunicorn on 0.0.0.0:8080)
    app.run(host="0.0.0.0", port=5000, debug=True)
