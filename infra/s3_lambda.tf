// infra/s3_lambda.tf
// S3 bucket for uploaded files + Lambda that scans them with ClamAV

resource "aws_s3_bucket" "uploads" {
  bucket        = "${var.app_name}-uploads"
  force_destroy = true

  tags = {
    Name        = "${var.app_name}-uploads"
    Environment = "dev"
  }
}

resource "aws_s3_bucket_versioning" "uploads_versioning" {
  bucket = aws_s3_bucket.uploads.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_public_access_block" "uploads" {
  bucket                  = aws_s3_bucket.uploads.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# -------------------------
# Lambda IAM Role & Policies
# -------------------------

data "aws_iam_policy_document" "lambda_assume_role" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "lambda_scan_role" {
  name               = "${var.app_name}-lambda-scan-role"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json
}

# Basic Lambda logging to CloudWatch
resource "aws_iam_role_policy_attachment" "lambda_basic_execution" {
  role       = aws_iam_role.lambda_scan_role.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

# S3 access for reading uploaded objects and updating metadata
resource "aws_iam_role_policy_attachment" "lambda_s3_access" {
  role       = aws_iam_role.lambda_scan_role.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonS3FullAccess"
}

# DynamoDB access so Lambda can update scan status records
resource "aws_iam_role_policy" "lambda_dynamodb_access" {
  name = "${var.app_name}-lambda-dynamodb-access"
  role = aws_iam_role.lambda_scan_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "dynamodb:PutItem",
          "dynamodb:UpdateItem",
          "dynamodb:GetItem",
          "dynamodb:DescribeTable",
          "dynamodb:Query"
        ]
        Resource = aws_dynamodb_table.scan_results.arn
      }
    ]
  })
}

# -------------------------
# Package Lambda code
# -------------------------
# This zips everything in ../lambda-scan into ../lambda-scan.zip
# (Make sure lambda-scan.zip is in .gitignore.)

data "archive_file" "lambda_scan_zip" {
  type        = "zip"
  source_dir  = "${path.module}/../lambda-scan"
  output_path = "${path.module}/../lambda-scan.zip"
}

# -------------------------
# Lambda function
# -------------------------

resource "aws_lambda_function" "scan_lambda" {
  function_name = "${var.app_name}-scan-lambda"
  role          = aws_iam_role.lambda_scan_role.arn

  # Use a supported Python runtime (your handler.py is plain Python)
  runtime = "python3.12"
  handler = "handler.lambda_handler"

  # Use the zip produced by archive_file above
  filename    = data.archive_file.lambda_scan_zip.output_path
  timeout     = 300
  memory_size = 2048

  # Use the pre-built ClamAV layer passed in via variable
  layers = [var.clamav_layer_arn]

  environment {
    variables = {
      # Bucket holding uploaded files that trigger this Lambda
      UPLOAD_BUCKET = aws_s3_bucket.uploads.bucket

      # DynamoDB table name used by backend /scan-status API
      SCAN_TABLE = aws_dynamodb_table.scan_results.name

      LOG_LEVEL     = "INFO"
      CLAMAV_TMPDIR = "/tmp"
    }
  }

  depends_on = [
    aws_iam_role.lambda_scan_role,
    aws_iam_role_policy_attachment.lambda_basic_execution,
    aws_iam_role_policy_attachment.lambda_s3_access,
    aws_iam_role_policy.lambda_dynamodb_access,
    aws_s3_bucket.uploads,
    aws_s3_bucket_versioning.uploads_versioning
  ]
}

# Allow S3 to invoke the Lambda when new objects are created
resource "aws_lambda_permission" "allow_s3_invoke" {
  statement_id  = "AllowExecutionFromS3"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.scan_lambda.arn
  principal     = "s3.amazonaws.com"
  source_arn    = aws_s3_bucket.uploads.arn
}

# Wire S3 events -> Lambda
resource "aws_s3_bucket_notification" "uploads_notifications" {
  bucket = aws_s3_bucket.uploads.id

  lambda_function {
    lambda_function_arn = aws_lambda_function.scan_lambda.arn
    events              = ["s3:ObjectCreated:*"]
  }

  depends_on = [aws_lambda_permission.allow_s3_invoke]
}
