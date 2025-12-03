import os
import uuid
from datetime import datetime, timezone

import boto3
from botocore.exceptions import ClientError
from flask import Flask, request, jsonify
from boto3.dynamodb.conditions import Key

app = Flask(__name__)

# -------------------------------------------------------------------
# Environment / AWS setup
# -------------------------------------------------------------------

UPLOAD_BUCKET = os.environ.get("UPLOAD_BUCKET")
SCAN_TABLE_NAME = os.environ.get("SCAN_TABLE")

if not UPLOAD_BUCKET or not SCAN_TABLE_NAME:
  raise RuntimeError(
      "UPLOAD_BUCKET and SCAN_TABLE environment variables must be set for the backend."
  )

s3 = boto3.client("s3")
dynamodb = boto3.resource("dynamodb")
scan_table = dynamodb.Table(SCAN_TABLE_NAME)


def utc_now_iso() -> str:
  """Return current UTC time in ISO-8601 format."""
  return datetime.now(timezone.utc).isoformat()


# -------------------------------------------------------------------
# Health check
# -------------------------------------------------------------------

@app.route("/api/health", methods=["GET"])
def health():
  return jsonify({"status": "ok"}), 200


# -------------------------------------------------------------------
# Upload endpoint
# 1) Accepts a file
# 2) Uploads to S3 (triggers Lambda scan)
# 3) Inserts a PENDING record into DynamoDB
# -------------------------------------------------------------------

@app.route("/api/upload", methods=["POST"])
def upload_file():
  if "file" not in request.files:
    return jsonify({"error": "No file part in request"}), 400

  file = request.files["file"]
  if not file or file.filename == "":
    return jsonify({"error": "No file selected"}), 400

  original_name = file.filename
  file_id = str(uuid.uuid4())
  s3_key = f"uploads/{file_id}/{original_name}"
  scan_timestamp = utc_now_iso()  # 🔴 MUST exist because it's the sort key

  try:
    # 1) Upload the file to S3 (this will trigger the Lambda scanner)
    s3.upload_fileobj(file, UPLOAD_BUCKET, s3_key)

    # 2) Insert initial PENDING record into DynamoDB
    item = {
        "file_id": file_id,
        "scan_timestamp": scan_timestamp,  # 🔴 REQUIRED sort key
        "file_name": original_name,
        "bucket": UPLOAD_BUCKET,
        "s3_key": s3_key,
        "status": "PENDING",
        "uploaded_at": scan_timestamp,
        # These can be filled in later by the Lambda after scan:
        "scan_result": "PENDING",
        "details": "Waiting for ClamAV scan to complete",
    }

    scan_table.put_item(Item=item)

  except ClientError as e:
    # Bubble up the exact error message so you see it in the frontend
    return (
        jsonify(
            {
                "error": f"Failed to write scan record to DynamoDB: {e}"
            }
        ),
        500,
    )
  except Exception as e:
    return jsonify({"error": f"Unexpected error: {e}"}), 500

  return (
      jsonify(
          {
              "message": "File uploaded and queued for scanning",
              "file_id": file_id,
              "status": "PENDING",
          }
      ),
      200,
  )


# -------------------------------------------------------------------
# Get latest scan status for a given file_id
# - Lambda can write multiple records for same file_id with different
#   scan_timestamp values (PENDING, CLEAN, INFECTED, etc.)
# - This endpoint returns the latest one.
# -------------------------------------------------------------------

@app.route("/api/scan-status/<file_id>", methods=["GET"])
def scan_status(file_id):
  try:
    response = scan_table.query(
        KeyConditionExpression=Key("file_id").eq(file_id)
    )
    items = response.get("Items", [])

    if not items:
      return jsonify({"error": "No scan record found for this file_id"}), 404

    # Sort by scan_timestamp descending (latest first)
    items.sort(
        key=lambda x: x.get("scan_timestamp", ""),
        reverse=True,
    )
    latest = items[0]

    return jsonify(latest), 200

  except ClientError as e:
    return (
        jsonify(
            {
                "error": f"Failed to read scan record from DynamoDB: {e}"
            }
        ),
        500,
    )
  except Exception as e:
    return jsonify({"error": f"Unexpected error: {e}"}), 500


# -------------------------------------------------------------------
# List recent scans (for dashboard)
# - For small tables, a Scan + in-memory sort is fine.
# -------------------------------------------------------------------

@app.route("/api/scans", methods=["GET"])
def list_scans():
  try:
    # You can adjust Limit or add pagination later if needed
    response = scan_table.scan()
    items = response.get("Items", [])

    # Sort by scan_timestamp descending so newest appear first
    items.sort(
        key=lambda x: x.get("scan_timestamp", ""),
        reverse=True,
    )

    return jsonify({"items": items}), 200

  except ClientError as e:
    return (
        jsonify(
            {
                "error": f"Failed to list scan records from DynamoDB: {e}"
            }
        ),
        500,
    )
  except Exception as e:
    return jsonify({"error": f"Unexpected error: {e}"}), 500


# -------------------------------------------------------------------
# Root (optional – just to avoid 404 if someone curls backend directly)
# -------------------------------------------------------------------

@app.route("/", methods=["GET"])
def root():
  return jsonify(
      {
          "service": "Capstone Group 6 File Scanner Backend",
          "status": "running",
      }
  )


if __name__ == "__main__":
  # Useful for local testing; in ECS you'll use gunicorn
  app.run(host="0.0.0.0", port=8080, debug=True)
