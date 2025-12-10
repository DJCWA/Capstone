// infra/s3_lambda.tf
// S3 uploads bucket + Lambda scanner wiring

############################
# Uploads bucket (source)
############################

resource "aws_s3_bucket" "uploads" {
  bucket        = "${var.app_name}-uploads"
  force_destroy = true

  tags = {
    Name        = "${var.app_name}-uploads"
    Environment = "prod"
    Component   = "uploads"
    Owner       = "group6"
  }
}

# Enable versioning on the source bucket (REQUIRED for replication)
resource "aws_s3_bucket_versioning" "uploads_versioning" {
  bucket = aws_s3_bucket.uploads.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "uploads_sse" {
  bucket = aws_s3_bucket.uploads.bucket

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "uploads_pab" {
  bucket = aws_s3_bucket.uploads.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

############################
# Lambda IAM role + policy
############################

data "aws_iam_policy_document" "scan_lambda_assume_role" {
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
  assume_role_policy = data.aws_iam_policy_document.scan_lambda_assume_role.json

  tags = {
    Name  = "${var.app_name}-scan-lambda-role"
    Owner = "group6"
  }
}

data "aws_iam_policy_document" "scan_lambda_policy" {
  # Read the uploaded file from the uploads bucket
  statement {
    sid    = "S3ReadWriteUploads"
    effect = "Allow"

    actions = [
      "s3:GetObject",
      "s3:GetObjectVersion",
      "s3:PutObject",
      "s3:DeleteObject",
    ]

    resources = [
      "${aws_s3_bucket.uploads.arn}/*",
    ]
  }

  # Update scan status records in DynamoDB
  statement {
    sid    = "DynamoDBScanResults"
    effect = "Allow"

    actions = [
      "dynamodb:PutItem",
      "dynamodb:UpdateItem",
      "dynamodb:GetItem",
      "dynamodb:Query",
      "dynamodb:Scan",
    ]

    resources = [
      aws_dynamodb_table.scan_results.arn,
    ]
  }

  # Write logs to CloudWatch (log group is auto-created by Lambda)
  statement {
    sid    = "CloudWatchLogs"
    effect = "Allow"

    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents",
      "logs:DescribeLogStreams",
      "logs:CreateLogGroup",
    ]

    resources = ["arn:aws:logs:${var.region}:*:log-group:/aws/lambda/${var.app_name}-scan-lambda:*"]
  }
}

resource "aws_iam_role_policy" "scan_lambda_policy" {
  name   = "${var.app_name}-scan-lambda-policy"
  role   = aws_iam_role.scan_lambda_role.id
  policy = data.aws_iam_policy_document.scan_lambda_policy.json
}

############################
# Lambda function
############################

resource "aws_lambda_function" "scan_lambda" {
  function_name = "${var.app_name}-scan-lambda"
  role          = aws_iam_role.scan_lambda_role.arn
  runtime       = "python3.12"
  handler       = "handler.lambda_handler"

  timeout     = 120
  memory_size = 2048

  # Zip file you created from lambda-scan/handler.py and placed in infra/
  filename         = "${path.module}/lambda-scan.zip"
  source_code_hash = filebase64sha256("${path.module}/lambda-scan.zip")

  # ClamAV layer ARN from your variable (points to the layer you created)
  layers = [
    var.clamav_layer_arn,
  ]

  environment {
    variables = {
      SCAN_TABLE = aws_dynamodb_table.scan_results.name
    }
  }

  depends_on = [
    aws_iam_role_policy.scan_lambda_policy,
    aws_s3_bucket.uploads,
    aws_s3_bucket_versioning.uploads_versioning,
    aws_dynamodb_table.scan_results,
  ]
}

############################
# Allow S3 to invoke Lambda
############################

resource "aws_lambda_permission" "allow_s3_invoke" {
  statement_id  = "AllowS3Invoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.scan_lambda.function_name
  principal     = "s3.amazonaws.com"
  source_arn    = aws_s3_bucket.uploads.arn
}

############################
# S3 event -> Lambda trigger
############################

resource "aws_s3_bucket_notification" "uploads_notification" {
  bucket = aws_s3_bucket.uploads.id

  lambda_function {
    lambda_function_arn = aws_lambda_function.scan_lambda.arn
    events              = ["s3:ObjectCreated:*"]
    filter_prefix       = "uploads/"
  }

  depends_on = [
    aws_lambda_permission.allow_s3_invoke,
  ]
}
