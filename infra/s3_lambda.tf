########################
# S3 bucket for uploads
########################

resource "aws_s3_bucket" "uploads" {
  bucket = "${var.app_name}-uploads"

  force_destroy = true

  tags = {
    Name        = "${var.app_name}-uploads"
    Environment = "prod"
    Component   = "uploads"
  }
}

# Versioning is required for cross-region replication and DR
resource "aws_s3_bucket_versioning" "uploads_versioning" {
  bucket = aws_s3_bucket.uploads.id

  versioning_configuration {
    status = "Enabled"
  }
}

# Lock bucket down (no public access)
resource "aws_s3_bucket_public_access_block" "uploads_pab" {
  bucket = aws_s3_bucket.uploads.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

########################
# IAM Role for Lambda
########################

data "aws_iam_policy_document" "lambda_assume" {
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
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

# Policy: Lambda can read/write from the uploads bucket and write logs
data "aws_iam_policy_document" "lambda_scan_policy" {
  statement {
    effect = "Allow"

    actions = [
      "logs:CreateLogGroup",
      "logs:CreateLogStream",
      "logs:PutLogEvents"
    ]

    resources = ["*"]
  }

  statement {
    effect = "Allow"

    actions = [
      "s3:GetObject",
      "s3:GetObjectVersion",
      "s3:PutObject",
      "s3:PutObjectAcl",
      "s3:ListBucket"
    ]

    resources = [
      aws_s3_bucket.uploads.arn,
      "${aws_s3_bucket.uploads.arn}/*"
    ]
  }
}

resource "aws_iam_role_policy" "lambda_scan_inline" {
  name   = "${var.app_name}-lambda-scan-policy"
  role   = aws_iam_role.lambda_scan_role.id
  policy = data.aws_iam_policy_document.lambda_scan_policy.json
}

########################
# Package Lambda code
########################

# Zips the ../lambda-scan folder into ../lambda-scan.zip
data "archive_file" "lambda_scan_zip" {
  type        = "zip"
  source_dir  = "${path.module}/../lambda-scan"
  output_path = "${path.module}/../lambda-scan.zip"
}

########################
# Lambda function
########################

resource "aws_lambda_function" "scan" {
  function_name = "${var.app_name}-scan"
  role          = aws_iam_role.lambda_scan_role.arn

  filename         = data.archive_file.lambda_scan_zip.output_path
  source_code_hash = data.archive_file.lambda_scan_zip.output_base64sha256

  handler = "handler.lambda_handler"
  runtime = "python3.12"
  timeout = 120
  memory_size = 1024

  # Use the ClamAV layer ARN you passed via variable
  layers = [
    var.clamav_layer_arn
  ]

  environment {
    variables = {
      # You can add things like LOG_LEVEL here if your handler uses them
      LOG_LEVEL = "INFO"
    }
  }

  tags = {
    Name        = "${var.app_name}-scan-lambda"
    Environment = "prod"
  }
}

########################
# S3 → Lambda notification
########################

resource "aws_lambda_permission" "allow_s3_invoke" {
  statement_id  = "AllowExecutionFromS3"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.scan.function_name
  principal     = "s3.amazonaws.com"
  source_arn    = aws_s3_bucket.uploads.arn
}

resource "aws_s3_bucket_notification" "uploads_notification" {
  bucket = aws_s3_bucket.uploads.id

  lambda_function {
    lambda_function_arn = aws_lambda_function.scan.arn
    events              = ["s3:ObjectCreated:*"]
  }

  depends_on = [aws_lambda_permission.allow_s3_invoke]
}
