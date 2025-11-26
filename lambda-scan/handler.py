import os
import boto3
import subprocess
import tempfile

S3 = boto3.client("s3")

# NOTE: In real AWS, you bundle or layer ClamAV & signatures. This is a simplified capstone version.
# You can fake the scan result for demo purposes if needed.

def lambda_handler(event, context):
    for record in event.get("Records", []):
        bucket = record["s3"]["bucket"]["name"]
        key = record["s3"]["object"]["key"]

        print(f"Scanning s3://{bucket}/{key}")

        # Download file to /tmp
        tmp_file = os.path.join(tempfile.gettempdir(), "scanfile")
        S3.download_file(bucket, key, tmp_file)

        # TODO: run clamav here; for demo we'll mark everything CLEAN.
        # Example (if clamav is available as 'clamscan'):
        # result = subprocess.run(["clamscan", tmp_file], capture_output=True, text=True)
        # if "OK" in result.stdout:
        #     status = "CLEAN"
        # else:
        #     status = "INFECTED"

        status = "CLEAN"  # Fake result for capstone demo

        head = S3.head_object(Bucket=bucket, Key=key)
        meta = head.get("Metadata", {})
        meta["scan_status"] = status

        # Update metadata (copy over itself)
        S3.copy_object(
            Bucket=bucket,
            Key=key,
            CopySource={"Bucket": bucket, "Key": key},
            Metadata=meta,
            MetadataDirective="REPLACE"
        )

        print(f"Scan complete, status={status}")

    return {"status": "ok"}
