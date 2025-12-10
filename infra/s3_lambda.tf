############################
# S3 Uploads Bucket
############################

resource "aws_s3_bucket" "uploads" {
  bucket        = "${var.app_name}-uploads"
  force_destroy = true

  tags = {
    Name        = "${var.app_name}-uploads"
    Project     = var.app_name
    Environment = "prod"
  }
}

# Default SSE (AES-256) on the uploads bucket
resource "aws_s3_bucket_server_side_encryption_configuration" "uploads_sse" {
  bucket = aws_s3_bucket.uploads.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

############################
# Lambda IAM Role & Policy
############################

data "aws_caller_identity" "current" {}

data "aws_iam_policy_document" "scan_lambda_assume_role" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "scan_lambda_role" {
  name               = "${var.app_name}-scan-lambda-role"
  assume_role_policy = data.aws_iam_policy_document.scan_lambda_assume_role.json
}

# Main permissions for Lambda:
# - CloudWatch Logs
# - Read from uploads bucket
# - Read/Write to DynamoDB scan table
data "aws_iam_policy_document" "scan_lambda_policy" {
  statement {
    sid     = "AllowCloudWatchLogs"
    effect  = "Allow"
    actions = [
      "logs:CreateLogGroup",
      "logs:CreateLogStream",
      "logs:PutLogEvents"
    ]

    resources = [
      "arn:aws:logs:${var.region}:${data.aws_caller_identity.current.account_id}:*"
    ]
  }

  statement {
    sid     = "AllowReadFromUploadsBucket"
    effect  = "Allow"
    actions = [
      "s3:GetObject",
      "s3:GetObjectVersion"
    ]

    resources = [
      "${aws_s3_bucket.uploads.arn}/*"
    ]
  }

  statement {
    sid     = "AllowScanTableAccess"
    effect  = "Allow"
    actions = [
      "dynamodb:PutItem",
      "dynamodb:UpdateItem",
      "dynamodb:GetItem",
      "dynamodb:Query",
      "dynamodb:Scan"
    ]

    resources = [
      aws_dynamodb_table.scan_results.arn
    ]
  }
}

resource "aws_iam_policy" "scan_lambda_policy" {
  name   = "${var.app_name}-scan-lambda-policy"
  policy = data.aws_iam_policy_document.scan_lambda_policy.json
}

resource "aws_iam_role_policy_attachment" "scan_lambda_policy_attach" {
  role       = aws_iam_role.scan_lambda_role.name
  policy_arn = aws_iam_policy.scan_lambda_policy.arn
}

# Also attach the standard AWS managed basic execution policy for Lambda
resource "aws_iam_role_policy_attachment" "scan_lambda_basic_logs" {
  role       = aws_iam_role.scan_lambda_role.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

############################
# Lambda Function (ClamAV scan)
############################

resource "aws_lambda_function" "scan_lambda" {
  function_name = "${var.app_name}-scan-lambda"
  role          = aws_iam_role.scan_lambda_role.arn

  # Python 3.12, x86_64 as we discussed
  runtime       = "python3.12"
  architectures = ["x86_64"]
  handler       = "handler.lambda_handler"

  # Zip with your handler.py (you created this manually)
  filename         = "${path.module}/lambda-scan.zip"
  source_code_hash = filebase64sha256("${path.module}/lambda-scan.zip")

  timeout      = 120
  memory_size  = 2048

  # Attach the pre-built ClamAV layer you created
  layers = [
    var.clamav_layer_arn
  ]

  environment {
    variables = {
      # Bucket where the backend uploads files
      UPLOAD_BUCKET = aws_s3_bucket.uploads.bucket

      # DynamoDB table that tracks scan status
      SCAN_TABLE    = aws_dynamodb_table.scan_results.name

      # Where the ClamAV databases live inside the layer
      # (matches how the layer was built: /opt/share/clamav)
      CLAMAV_DB_DIR = "/opt/share/clamav"
    }
  }

  tags = {
    Name        = "${var.app_name}-scan-lambda"
    Project     = var.app_name
    Environment = "prod"
  }
}

# Optional: explicit log group with retention for the scan Lambda
resource "aws_cloudwatch_log_group" "scan_lambda_log_group" {
  name              = "/aws/lambda/${aws_lambda_function.scan_lambda.function_name}"
  retention_in_days = 14
}

############################
# S3 → Lambda Notification
############################

# Allow S3 to invoke the Lambda
resource "aws_lambda_permission" "allow_s3_invoke_scan" {
  statement_id  = "AllowExecutionFromUploadsBucket"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.scan_lambda.function_name
  principal     = "s3.amazonaws.com"
  source_arn    = aws_s3_bucket.uploads.arn
}

# Trigger Lambda for new objects in the "uploads/" prefix
resource "aws_s3_bucket_notification" "uploads_scan_notification" {
  bucket = aws_s3_bucket.uploads.id

  lambda_function {
    lambda_function_arn = aws_lambda_function.scan_lambda.arn
    events              = ["s3:ObjectCreated:*"]
    filter_prefix       = "uploads/"
  }

  depends_on = [
    aws_lambda_permission.allow_s3_invoke_scan
  ]
}
