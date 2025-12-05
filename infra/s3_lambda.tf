############################################################
# S3 BUCKET FOR UPLOADS (PRIMARY REGION)
############################################################

resource "aws_s3_bucket" "uploads" {
  bucket        = "${var.app_name}-uploads"
  force_destroy = true

  tags = {
    Name        = "${var.app_name}-uploads"
    Environment = "capstone"
    Owner       = "group6"
  }
}

# Ownership & ACL – keep it private
resource "aws_s3_bucket_ownership_controls" "uploads" {
  bucket = aws_s3_bucket.uploads.id

  rule {
    object_ownership = "BucketOwnerPreferred"
  }
}

resource "aws_s3_bucket_acl" "uploads" {
  depends_on = [aws_s3_bucket_ownership_controls.uploads]

  bucket = aws_s3_bucket.uploads.id
  acl    = "private"
}

# Versioning – needed for replication & safer deletes
resource "aws_s3_bucket_versioning" "uploads_versioning" {
  bucket = aws_s3_bucket.uploads.id

  versioning_configuration {
    status = "Enabled"
  }
}

############################################################
# IAM ROLE + POLICY FOR SCAN LAMBDA
############################################################

# Trust policy so Lambda can assume this role
data "aws_iam_policy_document" "scan_lambda_assume" {
  statement {
    effect = "Allow"

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }

    actions = ["sts:AssumeRole"]
  }
}

resource "aws_iam_role" "scan_lambda_role" {
  name               = "${var.app_name}-scan-lambda-role"
  assume_role_policy = data.aws_iam_policy_document.scan_lambda_assume.json
}

# Permissions for the scan Lambda:
# - write logs
# - read objects from uploads bucket
# - write scan results to DynamoDB
data "aws_iam_policy_document" "scan_lambda_policy" {
  statement {
    sid    = "AllowCloudWatchLogs"
    effect = "Allow"

    actions = [
      "logs:CreateLogGroup",
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]

    resources = ["arn:aws:logs:${var.region}:${data.aws_caller_identity.current.account_id}:*"]
  }

  statement {
    sid    = "AllowReadUploadsBucket"
    effect = "Allow"

    actions = [
      "s3:GetObject",
      "s3:GetObjectTagging",
      "s3:PutObjectTagging",
    ]

    resources = [
      aws_s3_bucket.uploads.arn,
      "${aws_s3_bucket.uploads.arn}/*",
    ]
  }

  statement {
    sid    = "AllowWriteScanResultsDynamoDB"
    effect = "Allow"

    actions = [
      "dynamodb:PutItem",
      "dynamodb:UpdateItem",
      "dynamodb:GetItem",
      "dynamodb:Query",
    ]

    resources = [
      aws_dynamodb_table.scan_results.arn,
    ]
  }
}

resource "aws_iam_policy" "scan_lambda_policy" {
  name   = "${var.app_name}-scan-lambda-policy"
  policy = data.aws_iam_policy_document.scan_lambda_policy.json
}

resource "aws_iam_role_policy_attachment" "scan_lambda_attach" {
  role       = aws_iam_role.scan_lambda_role.name
  policy_arn = aws_iam_policy.scan_lambda_policy.arn
}

############################################################
# LAMBDA FUNCTION – S3 VIRUS SCAN (USES CLAMAV LAYER)
############################################################

# Needed for account ID in logs ARN above
data "aws_caller_identity" "current" {}

resource "aws_lambda_function" "scan_lambda" {
  function_name = "${var.app_name}-scan-lambda"
  role          = aws_iam_role.scan_lambda_role.arn

  # Python handler in your lambda-scan folder: handler.py → lambda_handler()
  handler = "handler.lambda_handler"
  runtime = "python3.12"

  # ZIP containing handler.py + its requirements (NOT the ClamAV layer)
  # Path is relative to this infra folder:
  #   repo-root/
  #     lambda-scan/
  #       handler.py
  #       lambda-scan.zip  <-- this file
  filename         = "${path.module}/../lambda-scan/lambda-scan.zip"
  source_code_hash = filebase64sha256("${path.module}/../lambda-scan/lambda-scan.zip")

  timeout     = 300
  memory_size = 1024
  publish     = true

  # Attach the prebuilt ClamAV Lambda layer
  layers = [
    var.clamav_layer_arn
  ]

  environment {
    variables = {
      # Where uploaded objects live
      UPLOAD_BUCKET = aws_s3_bucket.uploads.bucket

      # DynamoDB table (from dynamodb.tf)
      SCAN_TABLE = aws_dynamodb_table.scan_results.name

      # Logging / ClamAV config
      LOG_LEVEL       = "INFO"
      CLAMAV_TMPDIR   = "/tmp"
      CLAMAV_DB_DIR   = "/opt/share/clamav"
      LD_LIBRARY_PATH = "/opt/lib64:/opt/lib"
    }
  }

  tags = {
    Name        = "${var.app_name}-scan-lambda"
    Environment = "capstone"
    Owner       = "group6"
  }
}

############################################################
# S3 → LAMBDA NOTIFICATION
############################################################

# Allow S3 to invoke the scan Lambda
resource "aws_lambda_permission" "allow_s3_invoke_scan" {
  statement_id  = "AllowS3InvokeScan"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.scan_lambda.function_name
  principal     = "s3.amazonaws.com"
  source_arn    = aws_s3_bucket.uploads.arn
}

# S3 event notification for object uploads
resource "aws_s3_bucket_notification" "uploads_notifications" {
  bucket = aws_s3_bucket.uploads.id

  lambda_function {
    lambda_function_arn = aws_lambda_function.scan_lambda.arn
    events              = ["s3:ObjectCreated:*"]
  }

  depends_on = [
    aws_lambda_permission.allow_s3_invoke_scan
  ]
}
