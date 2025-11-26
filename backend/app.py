from flask import Flask, request, jsonify
import boto3
import os
import uuid
from datetime import datetime

app = Flask(__name__)

S3_BUCKET = os.environ.get("UPLOAD_BUCKET", "allen-capstone-uploads")
S3 = boto3.client("s3")

@app.route("/api/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"}), 200

@app.route("/api/upload", methods=["POST"])
def upload_file():
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400

    file = request.files["file"]
    if file.filename == "":
        return jsonify({"error": "Empty filename"}), 400

    # basic validation
    allowed_ext = {".pdf", ".docx", ".xlsx", ".png", ".jpg", ".zip"}
    _, ext = os.path.splitext(file.filename.lower())
    if ext not in allowed_ext:
        return jsonify({"error": f"File type {ext} not allowed"}), 400

    file_id = str(uuid.uuid4())
    key = f"uploads/{file_id}-{file.filename}"

    S3.upload_fileobj(
        file,
        S3_BUCKET,
        key,
        ExtraArgs={
            "Metadata": {
                "scan_status": "PENDING",
                "original_filename": file.filename
            }
        }
    )

    # TODO: write metadata into RDS or DynamoDB (file_id, key, status=PENDING, timestamp, user_id, etc.)

    return jsonify({"file_id": file_id, "message": "File uploaded, scanning in progress"}), 202

@app.route("/api/file-status/<file_id>", methods=["GET"])
def file_status(file_id):
    # In a real version you’d query DB by file_id
    # For demo, scan S3 keys that start with file_id (inefficient but okay for capstone)
    prefix = f"uploads/{file_id}-"
    resp = S3.list_objects_v2(Bucket=S3_BUCKET, Prefix=prefix)
    if "Contents" not in resp or len(resp["Contents"]) == 0:
        return jsonify({"error": "File not found"}), 404

    key = resp["Contents"][0]["Key"]
    head = S3.head_object(Bucket=S3_BUCKET, Key=key)
    metadata = head.get("Metadata", {})
    return jsonify({
        "file_id": file_id,
        "scan_status": metadata.get("scan_status", "UNKNOWN"),
        "original_filename": metadata.get("original_filename", ""),
        "last_checked": datetime.utcnow().isoformat() + "Z"
    }), 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)