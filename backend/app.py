import os
import uuid

import boto3
from botocore.exceptions import ClientError
from flask import Flask, request, jsonify

app = Flask(__name__)

s3 = boto3.client("s3")
UPLOAD_BUCKET = os.environ.get("UPLOAD_BUCKET")


@app.get("/api/health")
def health():
    return jsonify({"status": "ok"})


@app.post("/api/upload")
def upload_file():
    if "file" not in request.files:
        return jsonify({"error": "no_file", "detail": "No file field in request"}), 400

    file = request.files["file"]
    if file.filename == "":
        return jsonify({"error": "empty_filename", "detail": "No file selected"}), 400

    # Generate a unique key in S3
    key = f"uploads/{uuid.uuid4()}_{file.filename}"

    try:
        # Reset stream position
        file.stream.seek(0)

        s3.put_object(
            Bucket=UPLOAD_BUCKET,
            Key=key,
            Body=file.stream.read(),
            Metadata={
                "scan_status": "PENDING",
                "scan_detail": "File uploaded; waiting for scanner Lambda…",
            },
        )
    except ClientError as e:
        return (
            jsonify(
                {
                    "error": "upload_failed",
                    "detail": str(e),
                }
            ),
            500,
        )

    return jsonify(
        {
            "key": key,
            "status": "PENDING",
            "detail": "File uploaded successfully; scan will start shortly.",
        }
    )


@app.get("/api/status")
def get_status():
    key = request.args.get("key")
    if not key:
        return jsonify({"error": "missing_key", "detail": "key query parameter required"}), 400

    try:
        head = s3.head_object(Bucket=UPLOAD_BUCKET, Key=key)
    except ClientError:
        # Object not found yet
        return jsonify(
            {
                "status": "PENDING",
                "detail": "File not found in bucket yet; waiting for upload to complete…",
            }
        )

    md = head.get("Metadata", {}) or {}
    status = md.get("scan_status", "PENDING")
    detail = md.get("scan_detail") or "Waiting for scan to progress…"

    return jsonify(
        {
            "status": status,
            "detail": detail,
        }
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
