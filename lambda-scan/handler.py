import os
import boto3
import subprocess
import tempfile
import logging

S3 = boto3.client("s3")
logger = logging.getLogger()
logger.setLevel(logging.INFO)

CLAMSCAN_PATH = "/opt/bin/clamscan"
CLAM_DB_PATH = "/opt/share/clamav"

def scan_file(path: str) -> str:
    """
    Run ClamAV on the given file.
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
    result = subprocess.run(cmd, capture_output=True, text=True)

    logger.info("ClamAV stdout: %s", result.stdout)
    logger.info("ClamAV stderr: %s", result.stderr)
    logger.info("ClamAV exit code: %s", result.returncode)

    # ClamAV return codes:
    # 0 = no virus found
    # 1 = virus(es) found
    # >1 = error
    if result.returncode == 0:
        return "CLEAN"
    elif result.returncode == 1:
        return "INFECTED"
    else:
        return "ERROR"


def lambda_handler(event, context):
    for record in event.get("Records", []):
        bucket = record["s3"]["bucket"]["name"]
        key = record["s3"]["object"]["key"]

        logger.info("Scanning s3://%s/%s", bucket, key)

        tmp_file = os.path.join(tempfile.gettempdir(), "scanfile")
        S3.download_file(bucket, key, tmp_file)

        status = scan_file(tmp_file)

        # Read current metadata
        head = S3.head_object(Bucket=bucket, Key=key)
        meta = head.get("Metadata", {})
        meta["scan_status"] = status

        # Update metadata by copying object onto itself (same trick as before)
        S3.copy_object(
            Bucket=bucket,
            Key=key,
            CopySource={"Bucket": bucket, "Key": key},
            Metadata=meta,
            MetadataDirective="REPLACE"
        )

        logger.info("Scan complete: status=%s", status)

    return {"status": "ok"}
